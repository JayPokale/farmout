"""Skills: folders with a SKILL.md (YAML frontmatter with name and description, then instructions), the format
Claude Code, Codex and Copilot share. farmout runs a skill by sending its instructions to workers.

Where: <repo>/.farmout/skills/<name>/ (the project's), then ~/.config/farmout/skills/<name>/ (yours, and those you
install). Frontmatter farmout reads, besides name and description (other agents ignore these):
  mode: ask | review | race   ask (default) sends the instructions to the workers; review runs `farmout review`
                              with them as its focus; race has each worker attempt them in a worktree
  workers: [codex, claude]    default: one worker for ask, every worker for review and race
  write / isolate: true       ask mode only: let workers edit
  test: <command>             race mode: the command that picks the winner
  diff: true                  hand the workers your uncommitted change as a diff file
  model / effort
$ARGUMENTS in the instructions is replaced by the arguments; without it, they are appended.

`farmout skill add` installs from anywhere: a local folder (yours, as it is), a git repository, or a raw SKILL.md
URL. Remote skills that contain code (scripts/, executables) are refused: code comes only from the store.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from . import flows, review, sessions
from .config import CONFIG, project_root, repo_root

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CODE = {".py", ".sh", ".bash", ".zsh", ".fish", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".ps1",
        ".bat", ".cmd", ".exe", ".lua", ".go", ".rs", ".jar"}
MODES = ("ask", "review", "race")


def scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1].replace("\\" + v[0], v[0]) if v[0] == '"' else v[1:-1].replace("''", "'")
    if v.startswith("[") and v.endswith("]"):
        return [scalar(x) for x in v[1:-1].split(",") if x.strip()]
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    return int(v) if re.fullmatch(r"-?\d+", v) else v


def frontmatter(text):
    """(metadata, body) of a SKILL.md. Reads the YAML that skills use: scalars, quoted strings, [a, b] and
    `- item` lists, `|`/`>` blocks and one level of nested keys; anything fancier comes back as text."""
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.S)
    if not m:
        return {}, text
    meta, lines, i = {}, m.group(1).splitlines(), 0
    while i < len(lines):
        line = lines[i]
        km = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        i += 1
        if not km:
            continue
        key, rest = km.group(1), km.group(2).strip()
        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            block.append(lines[i])
            i += 1
        if rest in ("|", ">", "|-", ">-"):
            body = [b.strip() for b in block]
            meta[key] = ("\n" if rest[0] == "|" else " ").join(body).strip()
        elif rest:
            meta[key] = scalar(rest)
        elif any(b.strip().startswith("- ") for b in block):
            meta[key] = [scalar(b.strip()[2:]) for b in block if b.strip().startswith("- ")]
        elif block:
            meta[key] = {k.strip(): scalar(v) for k, _, v in (b.strip().partition(":") for b in block if ":" in b)}
        else:
            meta[key] = ""
    return meta, text[m.end():]


def fetched(d):
    """Whether `farmout skill add` fetched this skill from elsewhere (a git repository or a URL)."""
    try:
        source = json.loads((Path(d) / ".farmout-source.json").read_text()).get("source", "")
    except (OSError, ValueError, AttributeError):
        return False
    return bool(re.match(r"^(https?|ssh|git|file)://|^[\w.-]+@", str(source)))


def load(skill_md, origin):
    text = Path(skill_md).read_text(errors="replace")
    meta, body = frontmatter(text)
    name = str(meta.get("name") or Path(skill_md).parent.name)
    remote = fetched(Path(skill_md).parent)
    # What a skill may make farmout *do* (run its test command, write your tree) needs a skill you vouch for: one
    # you wrote or copied in, or one from the store. A project's skills and skills fetched from elsewhere are text.
    return {"name": name, "description": str(meta.get("description") or ""), "dir": str(Path(skill_md).parent),
            "path": str(skill_md), "meta": meta, "body": body.strip(), "origin": origin,
            "trusted": origin == "yours" and not remote}


def roots(cwd):
    return [("project", project_root(cwd) / ".farmout" / "skills"), ("yours", CONFIG / "skills")]


def available(cwd):
    """name -> skill; the project's skills win over yours."""
    found = {}
    for origin, d in reversed(roots(cwd)):
        for f in sorted(Path(d).glob("*/SKILL.md")) if Path(d).is_dir() else []:
            try:
                s = load(f, origin)
            except OSError:
                continue
            found[s["name"]] = s
    return found


def get(name, cwd):
    s = available(cwd).get(name)
    if not s:
        raise KeyError(f"no skill named {name!r} (`farmout skill ls` lists them)")
    return s


def render(skill, args=""):
    body = skill["body"]
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", args).strip()
    return body + (f"\n\nArguments: {args}" if args else "")


UNTRUSTED = ("test", "write")  # frontmatter keys that make farmout run a command or write your tree


def option(skill, key, given=None):
    if given not in (None, [], ""):
        return given
    if key in UNTRUSTED and not skill.get("trusted"):
        return None  # pass --test / --write yourself for a project's or a fetched skill
    return skill["meta"].get(key)


def run(skill, cwd, args="", workers=None, files=(), write=None, isolate=None, test=None, model=None):
    """Run a skill. Returns (mode, result): review -> the review result; race -> (group, rows, winner);
    ask -> (group, [(session, turn record)])."""
    mode = str(skill["meta"].get("mode") or "ask")
    if mode not in MODES:
        raise ValueError(f"skill {skill['name']}: mode must be one of {', '.join(MODES)}")
    names = workers or skill["meta"].get("workers")
    names = [names] if isinstance(names, str) else names
    prompt = render(skill, args)
    files = list(files) + [skill["dir"]]  # its references and assets are readable
    group = sessions.new_group(skill["name"])
    if skill["meta"].get("diff") is True and mode != "review":
        top = repo_root(cwd)
        if not top:
            raise ValueError(f"skill {skill['name']} works on your change, so it needs a git repository")
        diff, untracked, _ = review.capture(str(top), group)
        files = [str(diff), *untracked] + files
    if mode == "review":
        return mode, review.review(cwd, workers=names or None, focus=prompt, group=group)
    if mode == "race":
        return mode, flows.race(cwd, prompt, names or flows.default_workers(), test=option(skill, "test", test),
                                group=group, files=files)
    from .cli import auto_worker
    names = names or [auto_worker(cwd)]
    m = option(skill, "model", model)
    return mode, (group, flows.fan(group, names, cwd, prompt, files, write=bool(option(skill, "write", write)),
                                   isolate=bool(option(skill, "isolate", isolate)), models={w: m for w in names} if m else None,
                                   effort=skill["meta"].get("effort")))


def has_code(d):
    """The first file in d that is code: under scripts/, executable, or a script by its extension."""
    for f in sorted(Path(d).rglob("*")):
        rel = f.relative_to(d)
        if ".git" in rel.parts or not f.is_file():
            continue
        if "scripts" in rel.parts or f.suffix.lower() in CODE or os.access(f, os.X_OK):
            return str(rel)
    return None


def fetch(url, limit=2_000_000):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "farmout"}), timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than {limit} bytes")
    return data


def add(source, name=None, subpath=None, ref=None, force=False):
    """Install a skill; returns (name, destination, notes)."""
    if source.startswith("-"):
        raise ValueError("the source is a path or URL, not an option")
    tmp, notes, origin = Path(tempfile.mkdtemp(prefix="farmout-skill-")), [], {"source": source}
    try:
        local = Path(os.path.expanduser(source))
        if local.exists():
            base = local.resolve()
            if base.is_file():
                base = base.parent
        elif re.match(r"^https?://", source) and source.split("?")[0].endswith(".md"):
            base = tmp / "skill"
            base.mkdir()
            (base / "SKILL.md").write_bytes(fetch(source))
        else:
            clone = ["git", "-c", "protocol.ext.allow=never", "clone", "-q", "--depth", "1"] + (["--branch", ref] if ref else [])
            p = subprocess.run(clone + ["--", source, str(tmp / "repo")], capture_output=True, text=True)
            if p.returncode:
                raise RuntimeError(f"git clone {source}: {p.stderr.strip()[-400:]}")
            base = tmp / "repo"
            origin["commit"] = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"], capture_output=True,
                                              text=True).stdout.strip()
        base = base.resolve()
        d = (base / subpath).resolve() if subpath else base
        if subpath and base not in d.parents and d != base:
            raise ValueError("--path must stay inside the source")
        if not (d / "SKILL.md").is_file():
            found = sorted(str(f.parent.relative_to(base)) for f in d.rglob("SKILL.md") if ".git" not in f.parts)
            if len(found) != 1:
                raise ValueError(f"no single SKILL.md in {source}" +
                                 (f"; choose one with --path: {', '.join(found[:20])}" if found else ""))
            d = base / found[0]
            origin["path"] = found[0]
        links = [str(f.relative_to(d)) for f in d.rglob("*") if f.is_symlink()]
        if links and not local.exists():  # a link could pull in ~/.ssh/id_rsa, which workers would then be handed
            raise PermissionError(f"this skill contains symbolic links ({', '.join(links[:5])}); farmout won't install those")
        code = has_code(d)
        if code and not local.exists():
            raise PermissionError(f"this skill contains code ({code}); farmout installs code only from the store. "
                                  "Read it, and if you trust it, copy the folder into ~/.config/farmout/skills/ yourself")
        if code:
            notes.append(f"it contains code ({code}); installed as it is, from your own files")
        meta, _ = frontmatter((d / "SKILL.md").read_text(errors="replace"))
        name = name or str(meta.get("name") or d.name)
        if not NAME.fullmatch(name):
            raise ValueError(f"bad skill name {name!r}: lowercase letters, digits and '-' (or give --name)")
        if not meta.get("description"):
            notes.append("its SKILL.md has no description")
        dest = CONFIG / "skills" / name
        if dest.exists() and not force:
            raise ValueError(f"a skill named {name} is already installed; --force replaces it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        box = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=dest.parent))  # fresh and private: not a planted link
        try:
            staged = box / name
            shutil.copytree(d, staged, ignore=shutil.ignore_patterns(".git"), symlinks=True)
            (staged / ".farmout-source.json").write_text(json.dumps({**origin, "installed": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
            shutil.rmtree(dest, ignore_errors=True)
            staged.rename(dest)
        finally:
            shutil.rmtree(box, ignore_errors=True)
        return name, dest, notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove(name):
    if not NAME.fullmatch(name or ""):
        raise ValueError(f"bad skill name {name!r}")
    dest = CONFIG / "skills" / name
    if not dest.is_dir():
        raise KeyError(f"{name} isn't installed in {CONFIG / 'skills'} (project skills live in .farmout/skills/)")
    shutil.rmtree(dest)
