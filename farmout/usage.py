"""The usage log (one JSON line per worker run, no prompts or outputs) and the usage limits farmout has seen,
for `farmout stats` and for falling back to another worker when a subscription runs out."""
import json
import os
import threading
import time

from .config import STATE

_lock = threading.Lock()


def log(**rec):
    STATE.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": round(time.time()), **rec}) + "\n"
    with _lock, open(STATE / "usage.jsonl", "a") as f:  # short appends are atomic on POSIX
        f.write(line)


def tokens(usage):
    """Total tokens in a CLI's usage report, whatever its shape, or 0."""
    if not isinstance(usage, dict):
        return 0
    keys = ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "inputTokens", "outputTokens")
    return sum(v for k, v in usage.items() if k in keys and isinstance(v, int))


def _held():
    """{worker or worker:model: {"until", "why"}} for the ones farmout is leaving alone right now. why is "limit" (a
    usage limit) or "login" (not logged in, or the subscription ended). An older file holds bare times."""
    try:
        data = json.loads((STATE / "limits.json").read_text())
    except (OSError, ValueError):
        return {}
    now, out = time.time(), {}
    for k, v in (data.items() if isinstance(data, dict) else ()):
        until, why = (v.get("until"), v.get("why")) if isinstance(v, dict) else (v, "limit")
        if isinstance(until, (int, float)) and not isinstance(until, bool) and until > now:
            out[k] = {"until": until, "why": "login" if why == "login" else "limit"}
    return out


def limits():
    """worker (or worker:model) -> time until which farmout leaves it alone."""
    return {k: v["until"] for k, v in _held().items()}


def reasons():
    """worker (or worker:model) -> "limit" or "login", for the ones in limits()."""
    return {k: v["why"] for k, v in _held().items()}


def describe(key, until=None, why=None):
    """"codex is at a usage limit until 15:00", or that it isn't logged in."""
    held = _held().get(key) or {}
    until, why = until or held.get("until"), why or held.get("why", "limit")
    at = time.strftime("%H:%M" if (until or 0) - time.time() < 86400 else "%a %H:%M", time.localtime(until or 0))
    if why == "login":
        return f"{key} isn't logged in, or its subscription ended (farmout retries it after {at})"
    return f"{key} is at a usage limit until {at}"


def _save(data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / f"limits.json.{os.getpid()}.{threading.get_ident()}"
    tmp.write_text(json.dumps(data))
    tmp.replace(STATE / "limits.json")


def mark_limited(key, secs, why="limit"):
    with _lock:
        _save({**_held(), key: {"until": time.time() + secs, "why": why}})


def clear_limit(*keys):
    with _lock:
        data = _held()
        if any([data.pop(k, None) is not None for k in keys]):
            _save(data)


def stats(days=7):
    """Per worker over the last `days`: runs, ok, failed, limited, seconds, tokens."""
    since, rows = time.time() - days * 86400, {}
    try:
        lines = (STATE / "usage.jsonl").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("ts", 0) < since or not rec.get("worker"):
            continue
        r = rows.setdefault(rec["worker"], {"runs": 0, "ok": 0, "failed": 0, "limited": 0, "secs": 0.0, "tokens": 0})
        r["runs"] += 1
        r["ok" if rec.get("ok") else "failed"] += 1
        r["limited"] += bool(rec.get("limited"))
        r["secs"] += rec.get("secs") or 0
        r["tokens"] += rec.get("tokens") or 0
    return rows
