"""The store: reviewed skills, hooks and tools, every file pinned by its SHA-256.

The store is an index.json (default: store/index.json in the farmout repository on GitHub; set `store` in your own
settings, or FARMOUT_STORE, to use another, including a local path). Each entry lists its files with their hashes;
`farmout store add` downloads them, refuses the whole entry if any hash differs, and installs it under
~/.config/farmout/{skills,hooks,tools}/<name>/. Hooks and tools are code, so this, or a file you wrote yourself, is
the only way farmout takes them on.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import CONFIG, EVENTS, settings
from .sessions import PKG

DEFAULT = "https://raw.githubusercontent.com/JayPokale/farmout/main/store/index.json"
KINDS = {"skill": "skills", "hook": "hooks", "tool": "tools"}
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def url(cwd=None):
    return os.environ.get("FARMOUT_STORE") or settings(cwd or os.getcwd()).get("store") or DEFAULT


def fetch(where, limit=5_000_000):
    if re.match(r"^https?://", where):
        req = urllib.request.Request(where, headers={"User-Agent": "farmout"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(limit + 1)
    else:
        data = Path(where[7:] if where.startswith("file://") else where).expanduser().read_bytes()
    if len(data) > limit:
        raise ValueError(f"{where} is larger than {limit} bytes")
    return data


def join(index_url, rel):
    if not safe(rel):
        raise ValueError(f"store path {rel!r} must be relative and stay inside the store")
    if re.match(r"^https?://", index_url):
        return urllib.parse.urljoin(index_url, rel)
    base = index_url[7:] if index_url.startswith("file://") else index_url
    return str(Path(base).expanduser().parent / rel)


SAFE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")


def safe(rel):
    """A relative path that stays where it's put: no leading /, no .., no backslashes."""
    return isinstance(rel, str) and bool(SAFE.fullmatch(rel)) and ".." not in rel.split("/")


def check_entry(e):
    if not isinstance(e, dict) or not NAME.fullmatch(str(e.get("name", ""))) or e.get("kind") not in KINDS:
        raise ValueError(f"store entry {str(e)[:80]}: needs a name and a kind ({', '.join(KINDS)})")
    files = e.get("files")
    if not isinstance(files, list) or not files or not all(
            isinstance(f, dict) and safe(f.get("path")) and re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256", "")))
            for f in files):
        raise ValueError(f"store entry {e['name']}: files need a relative path and a sha256")
    if "dir" in e and not safe(e["dir"]):
        raise ValueError(f"store entry {e['name']}: dir must be a relative path inside the store")
    spec = e.get(e["kind"]) if e["kind"] in ("hook", "tool") else {}
    if e["kind"] in ("hook", "tool") and not (isinstance(spec, dict) and isinstance(spec.get("command"), str)):
        raise ValueError(f"store entry {e['name']}: a {e['kind']} needs a command")
    if e["kind"] == "hook" and not (isinstance(spec.get("events"), list) and spec["events"] and set(spec["events"]) <= set(EVENTS)):
        raise ValueError(f"store entry {e['name']}: a hook needs events from {', '.join(EVENTS)}")
    return e


def index(where=None):
    where = where or url()
    data = json.loads(fetch(where))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"{where} isn't a farmout store index (version 1)")
    return where, [check_entry(e) for e in data["entries"]]


def entry(name, where=None):
    where, entries = index(where)
    e = next((e for e in entries if e["name"] == name), None)
    if not e:
        raise KeyError(f"no {name!r} in the store at {where} (`farmout store ls`)")
    return where, e


def installed():
    """name -> (kind, the entry it was installed from, its directory)."""
    out = {}
    for kind, sub in KINDS.items():
        for f in sorted((CONFIG / sub).glob("*/.farmout-store.json")):
            try:
                out[f.parent.name] = (kind, json.loads(f.read_text()), f.parent)
            except (OSError, ValueError):
                continue
    return out


def download(where, e):
    """[(relative path, bytes)] for every file of entry e, each checked against its hash."""
    out = []
    for f in e["files"]:
        rel = f["path"]
        data = fetch(join(where, f"{e['dir']}/{rel}" if e.get("dir") else rel))
        got = hashlib.sha256(data).hexdigest()
        if got != f["sha256"]:
            raise ValueError(f"{e['name']}/{rel}: its SHA-256 is {got}, the index says {f['sha256']}; nothing was installed")
        out.append((rel, data))
    return out


def command(template, dest):
    return template.replace("{python}", shlex.quote(sys.executable)).replace("{dir}", shlex.quote(str(dest)))


def add(name, where=None, force=False):
    """Install (or update) an entry. Returns (entry, destination)."""
    where, e = entry(name, where)
    dest = CONFIG / KINDS[e["kind"]] / e["name"]
    if dest.exists() and not (dest / ".farmout-store.json").exists() and not force:
        raise ValueError(f"{dest} is yours, not from the store; --force replaces it")
    files = download(where, e)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{e['name']}.", dir=dest.parent))  # fresh and private: not a planted link
    for rel, data in files:
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if rel.endswith((".py", ".sh")):
            target.chmod(0o755)
    if e["kind"] == "hook":
        h = e["hook"]
        spec = {"name": e["name"], "events": h["events"], "command": command(h["command"], dest),
                "matcher": h.get("matcher"), "timeout": h.get("timeout")}
        (staged / "hook.json").write_text(json.dumps(spec, indent=1))
    if e["kind"] == "tool":
        (staged / "tool.json").write_text(json.dumps({"name": e["name"], "command": command(e["tool"]["command"], dest)}, indent=1))
    meta = {k: e.get(k) for k in ("name", "kind", "version", "description", "files")}
    (staged / ".farmout-store.json").write_text(json.dumps({**meta, "index": where, "installed": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(dest)
    return e, dest


def remove(name):
    found = installed().get(name)
    if not found:
        raise KeyError(f"{name} wasn't installed from the store")
    shutil.rmtree(found[2])
    return found[0]


def tools():
    """name -> the command of every tool in ~/.config/farmout/tools/ (from the store or your own)."""
    out = {}
    for f in sorted((CONFIG / "tools").glob("*/tool.json")):
        try:
            spec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(spec.get("command"), str):
            out[f.parent.name] = spec["command"]
    return out


def run_tool(name, args, cwd):
    cmd = tools().get(name)
    if not cmd:
        raise KeyError(f"no tool named {name!r} (`farmout tool ls`; install one with `farmout store add`)")
    env = {**os.environ, "FARMOUT_PYTHON": sys.executable,
           "PYTHONPATH": PKG + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    return subprocess.run(cmd + "".join(" " + shlex.quote(a) for a in args), shell=True, cwd=cwd, env=env).returncode
