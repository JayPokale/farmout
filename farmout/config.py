"""Paths and settings.

State (sessions, pipeline runs, the usage log) lives in ~/.local/state/farmout, one-shot `delegate` results in
~/.cache/farmout, and your own settings and pipelines in ~/.config/farmout (XDG variables are respected).
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def _dir(var, default):
    return Path(os.environ.get(var) or default).expanduser() / "farmout"


STATE = Path(os.environ["FARMOUT_STATE"]).expanduser() if os.environ.get("FARMOUT_STATE") else _dir("XDG_STATE_HOME", "~/.local/state")
OUT_DIR = _dir("XDG_CACHE_HOME", "~/.cache")
CONFIG = _dir("XDG_CONFIG_HOME", "~/.config")
SETTINGS = {"workers": list, "write": bool, "web": bool, "fallback": list, "timeout": int, "hooks": list,
            "redact": bool, "store": str, "models": dict, "rules": bool}
EVENTS = ("SessionStart", "MessageReceived", "TaskStart", "TaskEnd", "StageEnd", "PipelineEnd")
HOOK_KEYS = {"event", "command", "matcher", "timeout"}


def read(path, raw=None):
    """A .toml (Python 3.11+) or .json file, as a dict; raw: its bytes, already read."""
    path = Path(path)
    raw = path.read_bytes() if raw is None else raw
    if path.suffix == ".toml":
        try:
            import tomllib
        except ImportError:
            raise RuntimeError(f"{path}: reading TOML needs Python 3.11+; use a .json file instead") from None
        data = tomllib.loads(raw.decode())
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a table at the top level")
    return data


def find(directory, stem):
    for ext in (".toml", ".json"):
        p = Path(directory) / (stem + ext)
        if p.is_file():
            return p
    return None


def repo_root(cwd):
    p = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(p.stdout.strip()) if p.returncode == 0 else None


def project_root(cwd):
    """Where .farmout/ (settings, pipelines) lives: the main working tree of the git repository, also from inside
    one of its worktrees, so a worker that edits its worktree's .farmout/settings changes nothing; outside a
    repository, cwd itself."""
    cwd = os.path.realpath(os.path.expanduser(str(cwd)))
    p = subprocess.run(["git", "-C", cwd, "rev-parse", "--git-common-dir", "--show-toplevel"], capture_output=True, text=True)
    if p.returncode:
        return Path(cwd)
    common, top = p.stdout.strip().splitlines()[:2]
    common = Path(cwd, common).resolve()
    return common.parent if common.name == ".git" else Path(top)


def check_hooks(p, hooks):
    for i, h in enumerate(hooks, 1):
        if not isinstance(h, dict) or h.get("event") not in EVENTS or not isinstance(h.get("command"), str):
            raise ValueError(f"{p}: hooks entry {i} needs an event ({', '.join(EVENTS)}) and a command")
        if set(h) - HOOK_KEYS:
            raise ValueError(f"{p}: hooks entry {i}: unknown key(s) {', '.join(sorted(set(h) - HOOK_KEYS))}")
        if "timeout" in h and (not isinstance(h["timeout"], int) or isinstance(h["timeout"], bool) or h["timeout"] <= 0):
            raise ValueError(f"{p}: hooks entry {i}: timeout must be a positive number of seconds")
        if h.get("matcher") is not None:
            try:
                re.compile(h["matcher"])
            except (re.error, TypeError) as e:
                raise ValueError(f"{p}: hooks entry {i}: matcher isn't a valid regex: {e}") from None


def digest(path, raw=None):
    return hashlib.sha256(Path(path).read_bytes() if raw is None else raw).hexdigest()


def trust_file():
    return CONFIG / "trusted.json"


def trusted(root, path, raw=None):
    """Whether you trusted this repository's settings file as it is now (`farmout trust`). raw: the bytes the
    caller parsed, so the hash and the hooks come from one read (no swapping the file in between)."""
    try:
        data = json.loads(trust_file().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get(str(root)) == digest(path, raw)


def settings(cwd):
    """~/.config/farmout/settings, then the repo's .farmout/settings (.toml or .json). Neither can widen what the
    other allows: worker lists intersect, write or web access is off if either turns it off, redaction is on if
    either turns it on, and the shorter timeout wins. The repo's fallback order replaces yours, but only allowed
    workers are ever used; its models (a default model per worker) override yours. A repo's hooks run only once you
    trust its settings file (`farmout trust`), and only your own settings can name a store."""
    out, root = {"hooks": []}, project_root(cwd)
    for directory, mine in ((CONFIG, True), (root / ".farmout", False)):
        p = find(directory, "settings")
        if not p:
            continue
        raw = p.read_bytes()
        data = read(p, raw)
        for k, v in data.items():
            kind = SETTINGS.get(k)
            if kind is None:
                raise ValueError(f"{p}: unknown setting {k!r} (known: {', '.join(SETTINGS)})")
            if not isinstance(v, kind) or (kind is int and isinstance(v, bool)):
                raise ValueError(f"{p}: {k} must be a {kind.__name__}")
        if "workers" in data:
            out["workers"] = [w for w in data["workers"] if w in out.get("workers", data["workers"])]
        for k in ("write", "web", "rules"):
            if k in data:
                out[k] = out.get(k, True) and data[k]
        if "timeout" in data:
            out["timeout"] = min(out.get("timeout", data["timeout"]), data["timeout"])
        if "fallback" in data:
            if not all(isinstance(w, str) for w in data["fallback"]):
                raise ValueError(f"{p}: fallback must be a list of workers, like [\"codex\", \"claude:sonnet\"]")
            out["fallback"] = data["fallback"]
        if "models" in data:
            if not all(isinstance(m, str) and m for m in data["models"].values()):
                raise ValueError(f"{p}: models maps a worker to its model, like {{\"claude\": \"sonnet\"}}")
            out["models"] = {**out.get("models", {}), **data["models"]}
        if "redact" in data:
            out["redact"] = out.get("redact", False) or data["redact"]
        if "store" in data and mine:
            out["store"] = data["store"]
        if "hooks" in data:
            check_hooks(p, data["hooks"])
            if mine or trusted(root, p, raw):
                out["hooks"] += [{**h, "origin": str(p)} for h in data["hooks"]]
            else:
                out["untrusted"] = str(p)
    return out
