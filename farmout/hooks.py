"""farmout's own hooks: commands that run on farmout's events.

They come from your settings (`[[hooks]]` with event, command, optional matcher and timeout), from the store
(~/.config/farmout/hooks/<name>/hook.json), or from a repository's .farmout/settings once you trust it
(`farmout trust`). A hook reads the event as JSON on stdin. Exit 0 to go on; a TaskStart hook may print
{"prompt": "..."} to rewrite the brief. Exit 2, or print {"decision": "block", "reason": "..."}, to stop a
TaskStart. Any other failure is reported and ignored.

Events: SessionStart, MessageReceived, TaskStart, TaskEnd, StageEnd, PipelineEnd.
"""
import json
import os
import re
import signal
import subprocess
import tempfile

from . import VERSION
from .config import CONFIG, project_root, settings


class Blocked(Exception):
    pass


SECRETS = [re.compile(p) for p in (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\bAKIA[0-9A-Z]{16}\b",                                # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", r"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b",            # OpenAI / Anthropic style keys
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",                    # Slack
    r"\bAIza[0-9A-Za-z_-]{35}\b",                           # Google API key
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",  # JWT
)]
ASSIGNED = re.compile(r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\w*\s*[:=]\s*['\"]?)([^\s'\"]{8,})")


def redact(text):
    """(text with credentials replaced by [REDACTED], how many were found)."""
    count = 0
    for pat in SECRETS:
        text, n = pat.subn("[REDACTED]", text)
        count += n
    text, n = ASSIGNED.subn(lambda m: m.group(1) + "[REDACTED]", text)
    return text, count + n


def installed():
    """Hooks installed from the store."""
    out, d = [], CONFIG / "hooks"
    for f in sorted(d.glob("*/hook.json")) if d.is_dir() else []:
        try:
            spec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out += [{"event": ev, "command": spec["command"], "matcher": spec.get("matcher"), "timeout": spec.get("timeout"),
                 "origin": str(f.parent)} for ev in spec.get("events", []) if isinstance(spec.get("command"), str)]
    return out


def configured(cwd, s=None):
    s = settings(cwd) if s is None else s
    return s.get("hooks", []) + installed()


def run_hook(command, data, cwd, timeout):
    """(exit code, stdout, stderr). Output goes to files, not pipes, so a hook that starts something in the
    background (`notify-send ... &`) doesn't hold farmout up; a hook still running at its timeout is killed."""
    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        p = subprocess.Popen(command, shell=True, cwd=cwd, text=True, start_new_session=True, stdin=subprocess.PIPE,
                             stdout=out, stderr=err, env={**os.environ, "FARMOUT_EVENT": data["event"]})
        try:
            p.stdin.write(json.dumps(data) + "\n")  # one line, newline-terminated, for jq or read
            p.stdin.close()
        except BrokenPipeError:
            pass
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return None, "", f"timed out after {timeout}s"
        out.seek(0)
        err.seek(0)
        return p.returncode, out.read(), err.read()


def fire(event, payload, cwd, subject="", s=None):
    """Run the hooks for event. Returns (payload, notes); a blocked TaskStart raises Blocked."""
    notes = []
    try:
        hooks = [h for h in configured(cwd, s) if h["event"] == event]
    except Exception as e:  # bad settings: the task itself reports those
        return payload, [f"hooks not run: {e}"]
    for h in hooks:
        name = h["command"] if len(h["command"]) <= 60 else h["command"][:57] + "..."
        try:
            if h.get("matcher") and not re.search(h["matcher"], subject or ""):
                continue
            code, out, err = run_hook(h["command"], {"event": event, "farmout": VERSION, **payload},
                                      str(project_root(cwd)), h.get("timeout") or 30)
        except Exception as e:  # a broken hook is reported; it never takes the task down
            notes.append(f"hook `{name}` failed: {type(e).__name__}: {e}")
            continue
        reply = {}
        if out.strip().startswith("{"):
            try:
                reply = json.loads(out)
            except ValueError:
                pass
        if event == "TaskStart" and (code == 2 or reply.get("decision") == "block"):
            raise Blocked(f"hook `{name}`: {reply.get('reason') or err.strip()[-500:] or 'blocked'}")
        if code not in (0, 2):
            notes.append(f"hook `{name}` failed: {(err.strip() or f'exit {code}')[-200:]}")
        if event == "TaskStart" and isinstance(reply.get("prompt"), str) and reply["prompt"] != payload.get("prompt"):
            payload = {**payload, "prompt": reply["prompt"]}
            notes.append(f"hook `{name}` rewrote the brief")
    return payload, notes
