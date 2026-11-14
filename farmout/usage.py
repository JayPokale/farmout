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


def limits():
    """worker -> time until which farmout leaves it alone."""
    try:
        data = json.loads((STATE / "limits.json").read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {w: until for w, until in data.items() if isinstance(until, (int, float)) and until > now}


def _save(data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / f"limits.json.{os.getpid()}.{threading.get_ident()}"
    tmp.write_text(json.dumps(data))
    tmp.replace(STATE / "limits.json")


def mark_limited(worker, secs):
    with _lock:
        _save({**limits(), worker: time.time() + secs})


def clear_limit(worker):
    with _lock:
        data = limits()
        if data.pop(worker, None) is not None:
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
