"""Running one task: the repo's settings, isolation, the worker call, fallback when a subscription hits its limit,
and the usage log. The MCP server, the CLI, sessions and pipelines all go through execute()."""
import os
import time
import uuid

from . import isolate, usage
from .config import settings
from .workers import ENABLED, limited_for


def check(t):
    """The task with this repo's settings applied (web access, timeout); raises if they forbid it."""
    s = settings(os.path.expanduser(t.get("cwd") or os.getcwd()))
    w = t["worker"]
    if "workers" in s and w not in s["workers"]:
        raise PermissionError(f"this repo's farmout settings don't allow {w} (allowed: {', '.join(s['workers']) or 'none'})")
    if (t.get("write") or t.get("isolate")) and s.get("write") is False:
        raise PermissionError("this repo's farmout settings forbid write access")
    if s.get("web") is False and not ENABLED[w].web_off:
        raise PermissionError(f"this repo's farmout settings forbid web access, and {w} can't turn its web search off")
    return {**t, "web": s.get("web", True), "timeout": t.get("timeout") or s.get("timeout")}


def fallbacks(t):
    """Workers to try, in order, if this one hits a usage limit: the task's list, else the settings' list; only
    workers this repo allows."""
    try:
        s = settings(os.path.expanduser(t.get("cwd") or os.getcwd()))
    except Exception:
        return []
    order = s.get("fallback") or [] if t.get("fallback") is None else t["fallback"]
    return [w for w in order if w in ENABLED and w in s.get("workers", ENABLED)]


def execute(t, key, where=None, on_iso=None):
    """Run task t (a dict: worker, prompt, files, cwd, write, isolate, model, effort, fallback, ...).

    where (isolate only): {"label": dir under .farmout/worktrees, "branch": ...} for a new worktree, or an existing
    worktree's info to keep working in; on_iso(info) is called as soon as a new worktree exists, so a turn that is
    stopped halfway still leaves it findable. Returns {ok, worker, text, native_id, usage, secs, notes, iso, messages}.
    """
    t0, notes, iso, tried = time.time(), [], None, []
    order = [] if t.get("resume") else fallbacks(t)  # a resumed conversation belongs to its worker
    limited = usage.limits()
    if t.get("worker") in limited:  # known to be out of quota: go straight to the next one, if there is one
        nxt = next((f for f in order if f != t["worker"] and f not in limited), None)
        if nxt:
            until = time.strftime("%H:%M", time.localtime(limited[t["worker"]]))
            notes.append(f"{t['worker']} is at a usage limit until {until}; used {nxt}")
            t = {**t, "worker": nxt, "model": None,
                 "session_id": str(uuid.uuid4()) if t.get("persist") and nxt in ("claude", "copilot") else None}
    while True:
        w, t1 = t.get("worker"), time.time()
        try:
            if w not in ENABLED:
                raise ValueError(f"unknown or unavailable worker {w!r}; available: {', '.join(ENABLED) or 'none'}")
            run_t = check(t)
            if t.get("isolate") or (where or {}).get("worktree"):
                if not iso:
                    if (where or {}).get("worktree"):
                        iso = where
                    else:
                        label = (where or {}).get("label") or f"{time.strftime('%Y%m%d-%H%M%S')}-{w}"
                        iso = isolate.make(t.get("cwd"), label, (where or {}).get("branch") or "farmout/" + label.replace("/", "-"))
                        if on_iso:
                            on_iso(iso)
                run_t = isolate.enter(run_t, iso)
            reply = ENABLED[w].fn(run_t, key)
            res = {**reply, "ok": True, "worker": w, "text": (reply.get("text") or "").strip()}
            usage.clear_limit(w)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            wait = limited_for(err) if isinstance(e, RuntimeError) else None
            if wait:
                usage.mark_limited(w, wait)
            res = {"ok": False, "worker": w, "text": err, "limited": bool(wait)}
        usage.log(worker=w, ok=res["ok"], secs=round(time.time() - t1, 1), limited=res.get("limited", False),
                  tokens=usage.tokens(res.get("usage")), chars=len(res["text"]))
        if res["ok"] or not res.get("limited"):
            break
        tried.append(w)
        nxt = next((f for f in order if f not in tried and f not in usage.limits()), None)
        if not nxt:
            break
        notes.append(f"{w} hit a usage limit; retried on {nxt}")
        persist = t.get("persist") and nxt in ("claude", "copilot")
        t = {**t, "worker": nxt, "model": None, "session_id": str(uuid.uuid4()) if persist else None}
    if iso and res["ok"]:  # only the worker's own changes, ready for `git apply` in the user's tree
        try:
            stat = isolate.patch(iso)
            res["text"] += f"\n\n[isolated] worktree {iso['worktree']}, branch {iso['branch']}, patch {iso['patch']}:\n{stat}"
        except Exception as e:
            res.update(ok=False, text=res["text"] + f"\n\nmaking the patch failed: {type(e).__name__}: {e}")
    if iso and not res["ok"]:
        if "[isolated]" not in res["text"]:
            res["text"] += f"\n\n[isolated] worktree {iso['worktree']}, branch {iso['branch']} (kept for inspection)"
    res.update(secs=round(time.time() - t0, 1), notes=notes, iso=iso)
    return res
