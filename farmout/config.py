"""Paths and settings.

State (sessions, pipeline runs, the usage log) lives in ~/.local/state/farmout, one-shot `delegate` results in
~/.cache/farmout, and your own settings and pipelines in ~/.config/farmout (XDG variables are respected).
"""
import json
import os
import subprocess
from pathlib import Path


def _dir(var, default):
    return Path(os.environ.get(var) or default).expanduser() / "farmout"


STATE = Path(os.environ["FARMOUT_STATE"]).expanduser() if os.environ.get("FARMOUT_STATE") else _dir("XDG_STATE_HOME", "~/.local/state")
OUT_DIR = _dir("XDG_CACHE_HOME", "~/.cache")
CONFIG = _dir("XDG_CONFIG_HOME", "~/.config")
SETTINGS = {"workers": list, "write": bool, "web": bool, "fallback": list, "timeout": int}


def read(path):
    """A .toml (Python 3.11+) or .json file, as a dict."""
    path = Path(path)
    if path.suffix == ".toml":
        try:
            import tomllib
        except ImportError:
            raise RuntimeError(f"{path}: reading TOML needs Python 3.11+; use a .json file instead") from None
        with open(path, "rb") as f:
            data = tomllib.load(f)
    else:
        data = json.loads(path.read_text())
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


def settings(cwd):
    """~/.config/farmout/settings, then the repo's .farmout/settings (.toml or .json). Neither can widen what the
    other allows: worker lists intersect, write or web access is off if either turns it off, and the shorter
    timeout wins. The repo's fallback order replaces yours, but only allowed workers are ever used."""
    out = {}
    for directory in (CONFIG, project_root(cwd) / ".farmout"):
        p = find(directory, "settings")
        if not p:
            continue
        data = read(p)
        for k, v in data.items():
            kind = SETTINGS.get(k)
            if kind is None:
                raise ValueError(f"{p}: unknown setting {k!r} (known: {', '.join(SETTINGS)})")
            if not isinstance(v, kind) or (kind is int and isinstance(v, bool)):
                raise ValueError(f"{p}: {k} must be a {kind.__name__}")
        if "workers" in data:
            out["workers"] = [w for w in data["workers"] if w in out.get("workers", data["workers"])]
        for k in ("write", "web"):
            if k in data:
                out[k] = out.get(k, True) and data[k]
        if "timeout" in data:
            out["timeout"] = min(out.get("timeout", data["timeout"]), data["timeout"])
        if "fallback" in data:
            out["fallback"] = data["fallback"]
    return out
