"""Conversations you had yourself in Claude Code or Codex, read from the files those CLIs keep, so `farmout continue`
can carry one on with another worker after a usage limit: the prompts, the replies and one line per action (Edit
x, Bash: y). Only interactive sessions count: the `claude -p` and `codex exec` runs farmout itself starts are
skipped. Nothing here writes to those files."""
import itertools
import json
import os
import re
import subprocess
from pathlib import Path

LABELS = {"claude": "Claude Code", "codex": "Codex"}
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# Claude Code's own messages in the user's place: slash commands and their output, background task notices
NOT_ASKED = re.compile(r"\s*(<(command-name|command-message|command-args|local-command-stdout|local-command-stderr|"
                       r"local-command-caveat|task-notification|bash-input|bash-stdout|bash-stderr|user-memory-input)>"
                       r"|\[Request interrupted)")
INJECTED = re.compile(r"\s*(<[a-z_ -]+>|# AGENTS\.md instructions)")  # what Codex adds to a user message
KEYS = ("file_path", "notebook_path", "path", "command", "cmd", "pattern", "url", "query", "description")


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def lines(path, tail=None):
    """The JSON objects in a JSONL file (the last `tail` bytes only, if given); bad lines are skipped."""
    with open(path, "rb") as fh:
        if tail and os.path.getsize(path) > tail:
            fh.seek(os.path.getsize(path) - tail)
            fh.readline()  # most likely cut in half
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def first(path, limit=50):
    """The first `limit` objects in a JSONL file."""
    return list(itertools.islice(lines(path), limit))


def top(cwd):
    try:
        p = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError:
        return None
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 else None


def found(source, path, cwd, sid):
    return {"from": source, "label": LABELS[source], "file": str(path), "cwd": cwd, "id": sid,
            "updated": os.path.getmtime(path)}


def claude_sessions(root=None):
    """Claude Code keeps a project's sessions in projects/<its folder, every other character a dash>/; with root,
    only the folders that can hold root or a folder inside it are read (all of them if none match)."""
    projects = claude_home() / "projects"
    dirs = [d for d in projects.iterdir() if d.is_dir()] if projects.is_dir() else []
    near = [d for d in dirs if root and d.name.startswith(re.sub(r"[^A-Za-z0-9]", "-", root))]
    for f in (x for d in near or dirs for x in d.glob("*.jsonl")):
        if f.name.startswith("agent-"):  # a subagent's transcript, in older versions
            continue
        try:
            head = first(f, 200)
        except OSError:
            continue
        cwd = next((o["cwd"] for o in head if isinstance(o.get("cwd"), str) and o["cwd"]), None)
        entry = next((str(o["entrypoint"]) for o in head if o.get("entrypoint")), "")
        said = next((o for o in head if o.get("type") in ("user", "assistant")), {})
        if cwd and not entry.startswith("sdk") and not said.get("isSidechain"):
            yield found("claude", f, cwd, f.stem)  # not `claude -p` (farmout's own runs, scripts): sdk-cli


def codex_sessions(root=None):
    base = codex_home() / "sessions"
    for f in base.rglob("rollout-*.jsonl") if base.is_dir() else []:
        try:
            meta = next((o.get("payload") for o in first(f, 5) if o.get("type") == "session_meta"), None)
        except OSError:
            continue
        if isinstance(meta, dict) and isinstance(meta.get("cwd"), str) and meta.get("source") in ("cli", "vscode"):
            yield found("codex", f, meta["cwd"], str(meta.get("id") or f.stem))  # not `codex exec`


READERS = {"claude": claude_sessions, "codex": codex_sessions}


def find(cwd, source=None, session=None):
    """Sessions you ran in this folder (failing that, anywhere in its git repository), newest first. session: an id
    or the start of one, from any folder."""
    cwd = os.path.realpath(cwd)
    root = None if session else top(cwd) or cwd
    every = [s for src, read in READERS.items() if source in (None, src) for s in read(root)]
    if session:
        return sorted([s for s in every if s["id"].startswith(session)], key=lambda s: -s["updated"])
    here = [s for s in every if os.path.realpath(s["cwd"]) == cwd]
    if not here and root != cwd:
        here = [s for s in every if (os.path.realpath(s["cwd"]) + "/").startswith(root + "/")]
    return sorted(here, key=lambda s: -s["updated"])


def title(s):
    """What the session was about, cheaply: Claude Code's own title or last prompt; Codex's first prompt."""
    try:
        if s["from"] == "claude":
            got = {}
            for o in lines(s["file"], tail=512 * 1024):
                if o.get("type") == "ai-title" and text_of(o.get("aiTitle")):
                    got["title"] = o["aiTitle"]
                elif o.get("type") == "last-prompt" and text_of(o.get("lastPrompt")):
                    got["prompt"] = o["lastPrompt"]
            return got.get("title") or got.get("prompt") or ""
        for o in lines(s["file"]):
            p = o.get("payload")
            if (o.get("type") == "response_item" and isinstance(p, dict) and p.get("type") == "message"
                    and p.get("role") == "user"):
                text = user_text(p)
                if text:
                    return text
    except OSError:
        pass
    return ""


def action(name, args, cwd):
    """One line for a tool call: `Edit farmout/x.py`, `Bash: pytest -q`."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"input": args}
    args = args if isinstance(args, dict) else {}
    if name == "apply_patch" or "*** Begin Patch" in str(args.get("input", "")):
        files = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", str(args.get("input", "")), re.M)
        return f"apply_patch {', '.join(files)}" if files else "apply_patch"
    what = next((args[k] for k in KEYS if args.get(k)), "")
    if isinstance(what, list):  # codex: ["bash", "-lc", "pytest -q"]
        what = what[-1] if what else ""
    what = " ".join(str(what).split())
    if what.startswith(cwd.rstrip("/") + "/"):
        what = what[len(cwd.rstrip("/")) + 1:]
    name = str(name)
    sep = ": " if name.lower() in ("bash", "shell", "exec_command", "local_shell", "grep", "websearch", "webfetch",
                                   "agent", "task") else " "
    return (name + (sep + what if what else ""))[:160]


def text_of(x):
    return x if isinstance(x, str) else ""


def blocks_of(content):
    """A message's content as a list of block dicts (a string is one text block; anything else, none)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def claude_read(s):
    """(summary, turns) of a Claude Code session: the branch you were on (Claude Code keeps rewound ones in the
    same file), from its last compaction on, as Claude itself saw it then."""
    keep, leaf = {}, None
    for o in lines(s["file"]):
        kind, uuid, parent = o.get("type"), o.get("uuid"), o.get("parentUuid")
        if o.get("isSidechain") or not isinstance(uuid, str):
            continue
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        blocks = blocks_of(msg.get("content")) if kind in ("user", "assistant") else []
        e = {"type": kind, "parent": parent if isinstance(parent, str) else None}  # these files run to hundreds of MB
        if kind == "user" and not o.get("isMeta") and any(b.get("type") in ("text", "image") for b in blocks):
            text = "\n".join(text_of(b.get("text")) if b.get("type") == "text" else "[an image]"
                             for b in blocks if b.get("type") in ("text", "image"))  # not tool results
            e.update(text=REMINDER.sub("", text).strip(), summary=o.get("isCompactSummary"))
        elif kind == "assistant":
            e.update(texts=[text_of(b.get("text")).strip() for b in blocks
                            if b.get("type") == "text" and text_of(b.get("text")).strip()],
                     actions=[action(text_of(b.get("name")) or "tool", b.get("input"), s["cwd"]) for b in blocks
                              if b.get("type") == "tool_use"], error=o.get("isApiErrorMessage"))
        keep[uuid] = e
        if kind in ("user", "assistant"):
            leaf = uuid
    chain, seen = [], set()
    while leaf in keep and leaf not in seen:  # the newest message back to the start, or to the last compaction
        seen.add(leaf)
        chain.append(keep[leaf])
        leaf = keep[leaf]["parent"]
    summary, turns = None, []
    for e in reversed(chain):
        if e.get("text") and e.get("summary"):
            summary = e["text"]
        elif e.get("text") and not NOT_ASKED.match(e["text"]):
            turns.append({"prompt": e["text"], "reply": [], "actions": []})
        elif e["type"] == "assistant" and (e["texts"] or e["actions"]):
            if not turns:  # Claude carried on right after compacting
                turns.append({"prompt": f"(carried on after {s['label']} compacted the conversation)", "reply": [],
                              "actions": []})
            if e["error"]:
                turns[-1]["stopped"] = " ".join(" ".join(e["texts"]).split())[:300]
            else:
                turns[-1]["reply"] += e["texts"]
            turns[-1]["actions"] += e["actions"]
    return summary, turns


def user_text(p):
    texts = [text_of(c.get("text")) for c in blocks_of(p.get("content")) if c.get("type") == "input_text"]
    return "\n".join(t for t in texts if not INJECTED.match(t)).strip()


def codex_read(s):
    """(summary, turns) of a Codex session, from its last compaction on."""
    summary, turns = None, []

    def take(p):
        kind = p.get("type")
        if kind == "message" and p.get("role") == "user":
            text = user_text(p)
            if text:
                turns.append({"prompt": text, "reply": [], "actions": []})
        elif not turns:
            return
        elif kind == "message" and p.get("role") == "assistant":
            text = "\n".join(text_of(c.get("text")) for c in blocks_of(p.get("content"))
                             if c.get("type") == "output_text").strip()
            if text:
                turns[-1]["reply"].append(text)
        elif kind in ("function_call", "custom_tool_call"):
            turns[-1]["actions"].append(action(text_of(p.get("name")) or "tool",
                                               p.get("arguments") or {"input": p.get("input")}, s["cwd"]))
        elif kind == "local_shell_call":
            turns[-1]["actions"].append(action("shell", p.get("action"), s["cwd"]))

    for o in lines(s["file"]):
        p = o.get("payload")
        if not isinstance(p, dict):
            continue
        if o.get("type") == "compacted":  # what came before is now its summary, or the history it kept
            history = p.get("replacement_history")
            if text_of(p.get("message")).strip() or isinstance(history, list):
                summary, turns[:] = text_of(p.get("message")).strip() or None, []
                for item in history if isinstance(history, list) else []:
                    if isinstance(item, dict):
                        take(item)
        elif o.get("type") == "response_item":
            take(p)
    return summary, turns


def read(s):
    return (claude_read if s["from"] == "claude" else codex_read)(s)


def transcript(s, summary, turns, actions=30):
    """The session in farmout's transcript format (see sessions.transcript), so a worker reads it like any other."""
    out = [f"# A conversation from {s['label']} (session {s['id']}, in {s['cwd']})\n"]
    if summary:
        out.append(f"## Summary of the earlier conversation ({s['label']} compacted it)\n\n{summary.strip()}\n")
    for n, t in enumerate(turns, 1):
        acts = t["actions"][:actions] + ([f"… and {len(t['actions']) - actions} more"] if len(t["actions"]) > actions else [])
        reply = "\n\n".join(t["reply"]) or "(no reply in words)"
        if acts:
            reply += "\n\nActions:\n" + "\n".join(f"- {a}" for a in acts)
        if t.get("stopped"):
            reply += f"\n\n[{s['label']} stopped here: {t['stopped']}]"
        out.append(f"## Turn {n} ({s['from']}, {'ok' if t['reply'] or acts else 'no reply'})\n\n### Prompt\n\n"
                   f"{t['prompt']}\n\n### Reply\n\n{reply}\n")
    return "\n".join(out)
