"""Running one task: the repo's settings, redaction and hooks, isolation, the worker call, fallback when a
subscription hits its limit, and the usage log. The MCP server, the CLI, sessions and pipelines all go through
execute()."""
import os
import shlex
import time
import uuid

from . import hooks, isolate, usage
from .config import settings
from .workers import ENABLED, SETUP, lane, limited_for, logged_out, lost_conversation

LOGIN_PAUSE = 1800  # a worker that can't log in is left alone this long (or until it works again)


def lane_key(worker, model=None, s=None):
    """What a usage limit is recorded under: worker:model when the model is known (named, or the worker's default
    in the settings s), else the worker; so Opus at its limit doesn't keep Sonnet from being tried."""
    model = model or ((s or {}).get("models") or {}).get(worker)
    return f"{worker}:{model}" if model else worker


def held_by(key, worker, held, why):
    """Is farmout leaving this lane alone? Its own limit, or its worker isn't logged in (a login is per worker)."""
    return key in held or (worker in held and why.get(worker) == "login")


def check(t):
    """(the task with this repo's settings applied, the settings); raises if they forbid it."""
    s = settings(os.path.expanduser(t.get("cwd") or os.getcwd()))
    w = t["worker"]
    if "workers" in s and w not in s["workers"]:
        raise PermissionError(f"this repo's farmout settings don't allow {w} (allowed: {', '.join(s['workers']) or 'none'})")
    if (t.get("write") or t.get("isolate")) and s.get("write") is False:
        raise PermissionError("this repo's farmout settings forbid write access")
    if s.get("web") is False and not ENABLED[w].web_off:
        raise PermissionError(f"this repo's farmout settings forbid web access, and {w} can't turn its web search off")
    return {**t, "web": s.get("web", True), "timeout": t.get("timeout") or s.get("timeout"),
            "model": t.get("model") or (s.get("models") or {}).get(w)}, s


def prepare(t, s, notes):
    """Redaction (if the settings ask for it), then the TaskStart hooks, which may rewrite or block the brief."""
    if s.get("redact"):
        t["prompt"], n = hooks.redact(t["prompt"])
        if n:
            notes.append(f"redacted {n} secret{'s' * (n != 1)} from the brief")
    if s.get("untrusted") and not any("haven't trusted" in n for n in notes):
        notes.append(f"{s['untrusted']} defines hooks you haven't trusted; they didn't run (`farmout trust`)")
    payload = {k: t.get(k) for k in ("worker", "prompt", "files", "cwd", "write", "isolate", "model", "effort", "session")}
    payload, more = hooks.fire("TaskStart", payload, t.get("cwd") or os.getcwd(), subject=t["worker"], s=s)
    notes += more
    t["prompt"] = payload["prompt"]
    return t


LOST_NOTE = "{} couldn't find its own conversation, so it got the conversation so far as a file"


def fallbacks(t):
    """Lanes (worker, model) to try, in order, if this one hits a usage limit or can't log in: the task's list, else
    the settings' list, else every other ready worker except `api` (no tools, and often a much smaller model). Only
    workers this repo allows, and when it forbids web search, only ones that can turn theirs off."""
    try:
        s = settings(os.path.expanduser(t.get("cwd") or os.getcwd()))
    except Exception:
        return []
    if t.get("fallback") is not None:
        order = t["fallback"]
    elif "fallback" in s:
        order = s["fallback"]
    else:
        order = [w for w in ENABLED if w not in (t.get("worker"), "api")]
    out = []
    for x in order:
        w, m = lane(x)
        if (w in ENABLED and w in s.get("workers", ENABLED) and (s.get("web") is not False or ENABLED[w].web_off)
                and (w, m) not in out):
            out.append((w, m))
    return out


def switch(t, worker, model):
    """The task moved to another lane. The same worker keeps its conversation and only changes model. Another
    worker can't resume what this one holds, so it starts a conversation of its own and gets the earlier turns as
    a file (t["handoff"]() returns the files and a note to add)."""
    if worker == t.get("worker"):  # a new conversation also gets a new id: the failed run may have taken the old one
        fresh = t.get("session_id") and not t.get("resume")
        return {**t, "model": model, **({"session_id": str(uuid.uuid4())} if fresh else {})}
    nt = {**t, "worker": worker, "model": model, "resume": None, "fork": None, "history": None,
          "session_id": str(uuid.uuid4()) if t.get("persist") and worker in ("claude", "copilot") else None}
    if t.get("handoff") and (t.get("resume") or t.get("history")):
        files, note = t["handoff"]()
        nt.update(files=list(t.get("files") or []) + files, prompt=t["prompt"] + note, handoff=None)
    return nt


def execute(t, key, where=None, on_iso=None):
    """Run task t (a dict: worker, prompt, files, cwd, write, isolate, model, effort, fallback, ...).

    where (isolate only): {"label": dir under .farmout/worktrees, "branch": ...} for a new worktree, or an existing
    worktree's info to keep working in; on_iso(info) is called as soon as a new worktree exists, so a turn that is
    stopped halfway still leaves it findable. Returns {ok, worker, text, native_id, usage, secs, notes, iso, messages}.
    """
    t0, notes, iso, tried = time.time(), [], None, []
    try:
        s0 = settings(os.path.expanduser(t.get("cwd") or os.getcwd()))
    except Exception:
        s0 = {}
    # a conversation the worker holds natively moves only if the session can hand its transcript over
    order = fallbacks(t) if not t.get("resume") or t.get("handoff") else []
    held, why = usage.limits(), usage.reasons()
    here = lane_key(t.get("worker"), t.get("model"), s0)
    if held_by(here, t.get("worker"), held, why):  # known to be out of quota, or logged out: go straight on
        nxt = next(((w, m) for w, m in order if lane_key(w, m, s0) != here
                    and not held_by(lane_key(w, m, s0), w, held, why)), None)
        if nxt:
            notes.append(f"{usage.describe(here if here in held else t['worker'])}; used {lane_key(*nxt)}")
            tried.append(here)
            t = switch(t, *nxt)
    while True:
        w, t1 = t.get("worker"), time.time()
        k = lane_key(w, t.get("model"), s0)
        try:
            if w not in ENABLED:
                raise ValueError(f"unknown or unavailable worker {w!r}; available: {', '.join(ENABLED) or 'none'}")
            run_t, s = check(t)
            run_t = prepare(run_t, s, notes)
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
            res = {**reply, "ok": True, "worker": w, "model": t.get("model"), "text": (reply.get("text") or "").strip()}
            usage.clear_limit(k, *([w] if usage.reasons().get(w) == "login" else []))  # it logs in again
        except Exception as e:
            err, cause = f"{type(e).__name__}: {e}", None
            if isinstance(e, RuntimeError):
                wait = limited_for(err)
                cause = "limit" if wait else "login" if logged_out(err) else None
                if cause:  # a login is the worker's, whatever the model
                    usage.mark_limited(w if cause == "login" else k, wait or LOGIN_PAUSE, cause)
            res = {"ok": False, "worker": w, "model": t.get("model"), "text": err, "limited": bool(cause), "why": cause}
        usage.log(worker=w, ok=res["ok"], secs=round(time.time() - t1, 1), limited=res.get("limited", False),
                  tokens=usage.tokens(res.get("usage")), chars=len(res["text"]))
        if not res["ok"] and t.get("resume") and t.get("handoff") and lost_conversation(res["text"]):
            # its CLI can't find the conversation it holds (a cleared store, or an older Claude Code after the folder
            # moved): the same worker starts afresh, with the conversation so far as a file
            files, note = t["handoff"]()
            notes.append(LOST_NOTE.format(w))
            t = {**t, "resume": None, "fork": None, "handoff": None, "files": list(t.get("files") or []) + files,
                 "prompt": t["prompt"] + note,
                 "session_id": str(uuid.uuid4()) if t.get("persist") and w in ("claude", "copilot") else None}
            continue
        if res["ok"] or not res.get("limited"):
            break
        if res["why"] == "login" and notes[-1:] == [LOST_NOTE.format(w)]:
            notes.pop()  # it found no conversation because it isn't logged in (an empty login folder): say only that
        tried.append(k)
        held, why = usage.limits(), usage.reasons()
        nxt = next(((fw, fm) for fw, fm in order if lane_key(fw, fm, s0) not in tried
                    and not held_by(lane_key(fw, fm, s0), fw, held, why)), None)
        if not nxt:
            if res["why"] == "login":
                res["text"] += (f"\n\n[farmout] {w} isn't logged in, or its subscription ended. To use it again: "
                                f"{SETUP.get(w, (None, 'log in again'))[1]}. `farmout doctor --live` checks every login.")
            break
        notes.append(f"{usage.describe(w if res['why'] == 'login' else k)}; retried on {lane_key(*nxt)}")
        if iso and iso is not where:  # a worktree this task made: the next worker starts from its base, not from
            try:                      # whatever the limited one had half done
                isolate.reset(iso)
            except Exception as e:
                notes.append(f"couldn't reset the worktree for {lane_key(*nxt)} ({type(e).__name__}: {e}); stopped there")
                break
        t = switch(t, *nxt)
    if iso and res["ok"]:  # only the worker's own changes, ready for `git apply` in the user's tree
        try:
            stat = isolate.patch(iso)
            done = (f"farmout rm {t['session']} removes it with the session" if t.get("session") else  # still in use
                    f"When done with it: git -C {shlex.quote(iso['top'])} worktree remove --force {shlex.quote(iso['worktree'])}"
                    f" && git -C {shlex.quote(iso['top'])} branch -D {shlex.quote(iso['branch'])}")
            res["text"] += f"\n\n[isolated] worktree {iso['worktree']}, branch {iso['branch']}, patch {iso['patch']}:\n{stat}\n{done}"
        except Exception as e:
            res.update(ok=False, text=res["text"] + f"\n\nmaking the patch failed: {type(e).__name__}: {e}")
    if iso and not res["ok"]:
        if "[isolated]" not in res["text"]:
            res["text"] += f"\n\n[isolated] worktree {iso['worktree']}, branch {iso['branch']} (kept for inspection)"
    res.update(secs=round(time.time() - t0, 1), notes=notes, iso=iso)
    end = {"worker": res["worker"], "ok": res["ok"], "secs": res["secs"], "session": t.get("session"),
           "cwd": t.get("cwd"), "text": res["text"][:20000], "patch": (iso or {}).get("patch") if res["ok"] else None}
    try:
        _, more = hooks.fire("TaskEnd", end, t.get("cwd") or os.getcwd(), subject=res["worker"])
    except hooks.Blocked:
        more = []
    notes += more
    return res
