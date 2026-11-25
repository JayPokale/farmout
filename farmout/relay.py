"""The limit relay. When Claude Code hits its usage limit, what you type next goes to another subscription with the
whole conversation, and its answer shows in Claude Code; once the limit is over, Claude is told what was asked and
answered meanwhile. It runs as Claude Code's UserPromptSubmit hook (the plugin has it; `farmout relay on` turns it
on) and reads the limit, with its reset time, from the session file Claude Code keeps. While it's off, a prompt at
the limit only gets a line saying it exists. `farmout continue` uses carry() to start its sessions the same way."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import sys
import time

from . import native
from .config import CONFIG, STATE

RECORDS = STATE / "relay"
WAIT = 600  # seconds the hook waits for an answer before pointing at `farmout attach`
HOOK_TIMEOUT = 900  # the plugin's timeout for the hook; Claude Code stops it after this
TAIL = 512 * 1024  # bytes of the session file read for its newest reply
SHOW = 4000  # characters of an answer the user hadn't seen, shown when Claude is back (all of it: farmout log)
GRACE = 300  # a relay record this long past its reset belongs to an earlier limit
MODES = ("read", "write", "isolate")
CARRY_ON = "Carry on from where this conversation stopped."
USAGE = re.compile(r"hit your (?:[\w-]+ )?limit|usage limit|limit reached", re.I)


def same_login(source):
    """Is farmout's worker for this CLI on the login the session ran out on? A second Claude account isn't."""
    from .workers import CLAUDE_DIR
    return source != "claude" or not CLAUDE_DIR or os.path.realpath(CLAUDE_DIR) == os.path.realpath(native.claude_home())


def pick(s, cwd, asked=None):
    """(worker, model) to carry session s on: the one asked for, else the first ready one not on the login that ran
    out."""
    from . import engine
    from .workers import lane
    if asked:
        return lane(asked)
    try:
        return engine.auto_worker(cwd, skip={s["from"]} if same_login(s["from"]) else set())
    except ValueError:
        raise ValueError(f"no worker but {s['from']} is ready here (`farmout doctor`); name one with -w, "
                         f"e.g. -w {s['from']}:<another model>") from None


def carry(s, worker, model=None, cwd=None, name=None, write=False, isolate=False, effort=None, fallback=None,
          empty_ok=False):
    """A new farmout session that carries on native session s: its prompts, replies and actions (credentials
    replaced) become the session's context.md, and its turns are numbered on from them. (meta, summary, turns,
    secrets replaced). empty_ok: a session with nothing in it yet (the relay, right after `relay test`) starts
    a session without context instead of failing."""
    from . import hooks, sessions
    try:
        summary, turns = native.read(s)
    except OSError:  # Claude Code writes the file with the first prompt
        if not empty_ok:
            raise
        summary, turns = None, []
    if not turns and not summary and not empty_ok:
        raise ValueError(f"{s['label']} session {s['id']} has nothing to carry on from yet")
    text, secrets = hooks.redact(native.transcript(s, summary, turns)) if turns or summary else ("", 0)
    cwd = s["cwd"] if os.path.isdir(s["cwd"]) else cwd
    meta = sessions.create(worker, cwd, name=name, write=write, isolate=isolate, model=model, effort=effort,
                           fallback=fallback, base=len(turns))
    meta["imported"] = {"from": s["from"], "label": s["label"], "id": s["id"], "file": s["file"], "turns": len(turns)}
    sessions.save(meta)
    if text:
        (sessions.path(meta["name"]) / "context.md").write_text(text)
    return meta, summary, turns, secrets


def config():
    """Your relay setting ({"on", "worker", "mode", "wait"}), from ~/.config/farmout/relay.json."""
    try:
        data = json.loads((CONFIG / "relay.json").read_text())
    except (OSError, ValueError):
        return {"on": False}
    return data if isinstance(data, dict) else {"on": False}


def configure(**changes):
    data = {**config(), **changes}
    CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG / f"relay.json.{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(CONFIG / "relay.json")
    return data


def record_path(sid):
    return RECORDS / (re.sub(r"[^A-Za-z0-9-]", "", sid)[:80] + ".json")


def record(sid):
    try:
        data = json.loads(record_path(sid).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def records(now=None):
    """Every relay record; one untouched for a week (a limit long over that nobody was caught up on) goes."""
    out, now = [], now or time.time()
    for p in sorted(RECORDS.glob("*.json")) if RECORDS.is_dir() else []:
        rec = record(p.stem)
        if not rec:
            continue
        if max(rec.get("until") or 0, rec.get("test") or 0, rec.get("since") or 0) < now - 7 * 86400:
            forget(p.stem)
            continue
        out.append(rec)
    return out


def test(sid, file, cwd, minutes, now=None):
    """`farmout relay test`: this Claude Code session acts as if at its usage limit for a while, so the relay can be
    tried before it's needed. `farmout relay back` (or the time running out) ends it, and Claude catches up."""
    now = now or time.time()
    rec = record(sid) or {"session": sid, "file": file, "cwd": cwd, "since": now, "told": 0, "shown": []}
    keep(sid, {**rec, "test": now + minutes * 60, "back": 0, "since": rec.get("since") or now})
    return now + minutes * 60


def keep(sid, rec):
    """Save a relay record. A `farmout relay back` written meanwhile by another process stays."""
    now = record(sid)
    if now and (now.get("back") or 0) > (rec.get("back") or 0):
        rec = {**rec, "back": now["back"]}
    RECORDS.mkdir(parents=True, exist_ok=True)
    tmp = record_path(sid).with_suffix(f".{os.getpid()}")
    tmp.write_text(json.dumps(rec))
    tmp.replace(record_path(sid))


def forget(sid):
    try:
        record_path(sid).unlink()
    except FileNotFoundError:
        pass


def dig(o, *keys):
    for k in keys:
        o = o.get(k) if isinstance(o, dict) else None
    return o


def stamp(o):
    """An entry's time as Unix seconds (Claude Code writes ISO times in UTC), or None."""
    try:
        return datetime.datetime.fromisoformat(str(o.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def limit_in(path):
    """If the newest reply in this Claude Code session file is a usage-limit error: {"until", "text", "at", "uuid"},
    until being when the limit resets (Unix seconds). False if it's anything else, None if there's no reply in the
    end of the file (or no file)."""
    last = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL:
                fh.seek(size - TAIL)
                fh.readline()  # most likely cut in half
            for raw in fh:
                if b"assistant" not in raw:  # cheap: most lines are tool results and other entries
                    continue
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("type") == "assistant" and not o.get("isSidechain"):
                    last = o
    except (OSError, TypeError, ValueError):
        return None
    if not last:
        return None
    if not last.get("isApiErrorMessage") or str(last.get("entrypoint") or "").startswith("sdk"):
        return False  # `claude -p` runs (farmout's own among them) handle their limits themselves
    text = " ".join(native.text_of(b.get("text")) for b in native.blocks_of(dig(last, "message", "content"))
                    if b.get("type") == "text").strip()
    if last.get("apiError") != "usage_limit_reached" and not (last.get("error") == "rate_limit" and USAGE.search(text)):
        return False
    at = stamp(last) or os.path.getmtime(path)
    until = next((v for v in (dig(last, "apiErrorParams", "rate_limit_info", "resetsAt"), dig(last, "quotaLimits", "resetsAt"))
                  if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    if until is None:
        from .workers import reset_in
        secs = reset_in(text, now=datetime.datetime.fromtimestamp(at).astimezone())
        until = at + (secs if secs is not None else 900)
    kind = dig(last, "apiErrorParams", "rate_limit_info", "rateLimitType") or dig(last, "quotaLimits", "rateLimitType")
    return {"until": until, "text": text, "at": at, "uuid": str(last.get("uuid") or ""),
            "kind": kind if isinstance(kind, str) else None}


def heading(limit):
    """"Claude is at its usage limit until 15:00", and, for one model's weekly limit, how to stay on Claude."""
    if limit.get("kind") == "test":
        return (f"farmout relay test: Claude acts as if at its usage limit until {clock(limit['until'])} "
                f"(`farmout relay back` ends it)")
    head = f"Claude is at its usage limit until {clock(limit['until'])}"
    model = {"seven_day_opus": "Opus", "seven_day_sonnet": "Sonnet"}.get(limit.get("kind") or "")
    if model:
        other = "sonnet" if model == "Opus" else "opus"
        head += (f" (its weekly {model} limit: `/model {other}` and then `!farmout relay back` keep you on Claude)")
    return head


def clock(t):
    return time.strftime("%H:%M" if t - time.time() < 86400 else "%a %H:%M", time.localtime(t))


@contextlib.contextmanager
def only_one(sid, wait=0.0):
    """The session's lock, waiting up to `wait` seconds for it; yields whether this process got it. A prompt waits
    (the StopFailure hook may be starting the carry-on); the StopFailure hook doesn't (a relay is under way)."""
    RECORDS.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait
    with open(record_path(sid).with_suffix(".lock"), "a") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def current(path, rec, now):
    """The limit this Claude Code session is under now, if any: from its session file, `farmout relay test`, or the
    relay record (relayed answers can push Claude's last reply out of the part of the file read); none after
    `farmout relay back`."""
    limit = limit_in(path)
    if rec and (rec.get("test") or 0) > now and not rec.get("back"):  # `farmout relay test`
        limit = {"until": rec["test"], "text": "", "at": rec.get("since") or now, "kind": "test"}
    if limit is None and rec and (rec.get("until") or 0) > now and not rec.get("back"):
        limit = {"until": rec["until"], "text": rec.get("limit") or "", "at": rec.get("since") or now}
    if limit and rec and rec.get("back", 0) >= limit["at"]:  # Claude is back before the reset
        limit = None
    return limit


def hook(event, now=None):
    """What Claude Code's UserPromptSubmit hook answers, as a dict; None lets the prompt go on as usual."""
    now = now or time.time()
    sid, path = event.get("session_id"), event.get("transcript_path")
    if not isinstance(sid, str) or not sid or not isinstance(path, str):
        return None
    prompt = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
    if not prompt.strip() or prompt.lstrip().startswith(("/", "!")):
        return None  # Claude Code's own commands: never relayed, and the catch-up waits for a real prompt
    started = now
    rec, limit = record(sid), current(path, record(sid), now)
    if not (limit and limit["until"] > now) and not (rec and rec.get("name")):
        return None
    said = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    with only_one(sid, wait=HOOK_TIMEOUT - 60) as mine:
        if not mine:
            return None
        now = time.time()  # again, after any wait: the reset may have come, or `relay back`
        rec = record(sid)
        limit = current(path, rec, now)
        if rec and rec.get("said") == said and started < (rec.get("said_done") or float("inf")):
            return None  # the other hook on this same prompt (the plugin's and one added by hand) had it
        if limit and limit["until"] > now:
            cfg = config()
            if not cfg.get("on"):
                return {"systemMessage": f"farmout: {heading(limit)}. `farmout relay on` sends what you type next to "
                                         f"another subscription, with this conversation; or run `!farmout continue "
                                         f"--write`."}
            cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isdir(event["cwd"]) else os.getcwd()
            return relay(event, sid, cwd, prompt, rec, limit, cfg, now, said)
        if not (rec and rec.get("name")):
            return None  # told already, by the other hook
        return catch_up(sid, rec)


def git_changes(cwd):
    """{path: status} of what git sees changed or new in cwd's checkout (paths from its top), and the top; or
    ({}, None) outside git."""
    import subprocess
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             errors="surrogateescape", timeout=10).stdout.strip()
        out = subprocess.run(["git", "-C", cwd, "status", "--porcelain", "-uall", "-z"], capture_output=True,
                             text=True, errors="surrogateescape", timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if out.returncode or not top:
        return {}
    fields, found = out.stdout.split("\0"), {}
    i = 0
    while i < len(fields):
        f = fields[i]
        if len(f) > 3 and f[2] == " ":
            found[f[3:]] = f[:2]
            if f[0] in "RC":  # a rename or copy: the next field is the path it came from
                i += 1
        i += 1
    return {"top": top, "paths": found}


def changed_since(cwd, since, before=None):
    """Paths git sees changed or new now that weren't before the relay began, or that were touched since `since`."""
    now = git_changes(cwd)
    if not now:
        return []
    old, keep = (before or {}).get("paths") or {}, []
    for p, st in now["paths"].items():
        try:
            touched = os.path.getmtime(os.path.join(now["top"], p)) >= since - 1
        except OSError:  # gone: deleted since, unless it was deleted before
            touched = p not in old
        if touched or p not in old or old[p] != st:
            keep.append(p)
    return keep


def turn_text(r, base):
    from . import flows
    return (f"## Turn {base + r['n']} ({r.get('worker')}, {r['status']})\n\n### Prompt\n\n{r['prompt']}\n\n"
            f"### Reply\n\n{flows.text(r).strip()}\n")


def session_for(path, sid, cwd, rec, limit, cfg, now):
    """(name, record) of the farmout session carrying this Claude Code conversation on: this limit's, or a new one
    started from the session file. Raises ValueError, KeyError or OSError when nothing can take over."""
    from . import sessions
    name = (rec or {}).get("name")
    if name and (not sessions.path(name).is_dir() or (rec.get("until") or 0) < now - GRACE):
        name = None  # removed, or carried on from an earlier limit
    if not name:
        s = {"from": "claude", "label": native.LABELS["claude"], "file": path, "cwd": cwd, "id": sid, "updated": now}
        mode = cfg.get("mode") if cfg.get("mode") in MODES else "read"
        worker, model = pick(s, cwd, cfg.get("worker"))
        meta = carry(s, worker, model, cwd, write=mode == "write", isolate=mode == "isolate", empty_ok=True)[0]
        old = rec or {}
        rec = {"session": sid, "file": path, "cwd": cwd, "since": now, "told": 0, "shown": [],
               "before": git_changes(cwd)}  # what was changed already, so the catch-up names only the new
        if (old.get("test") or 0) > now and not old.get("back"):
            rec["test"] = old["test"]  # `relay test` goes on for its minutes
        name = meta["name"]
    return name, {**rec, "name": name, "until": limit["until"], "limit": limit["text"]}


def stopped(event, now=None):
    """Claude Code's StopFailure hook. With `farmout relay on --auto`, a turn the usage limit cut off is carried on at
    once by another worker, in the background; the next prompt typed in Claude Code goes to the same farmout session
    and brings its answer. Claude Code ignores what this hook prints."""
    from . import sessions
    now = now or time.time()
    cfg = config()
    sid, path = event.get("session_id"), event.get("transcript_path")
    if not (cfg.get("on") and cfg.get("auto")) or not isinstance(sid, str) or not sid or not isinstance(path, str):
        return None
    limit = limit_in(path)
    said = event.get("last_assistant_message")
    if (not limit or limit["until"] <= now) and event.get("error") == "rate_limit" and isinstance(said, str) \
            and USAGE.search(said):
        from .workers import reset_in  # the session file doesn't show it yet: Claude Code's own words
        secs = reset_in(said)
        limit = {"until": now + (secs if secs is not None else 900), "text": said, "at": now, "kind": None}
    if not limit or limit["until"] <= now:
        return None
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isdir(event["cwd"]) else os.getcwd()
    with only_one(sid) as mine:  # held: a relay is under way already
        if not mine:
            return None
        rec = record(sid)
        if rec and (rec.get("back") or 0) >= limit["at"]:
            return None  # `farmout relay back` came after this limit
        if (rec and rec.get("name") and sessions.path(rec["name"]).is_dir()
                and (rec.get("until") or 0) >= now - GRACE and not rec.get("back")):
            return None  # this limit is being relayed already
        try:
            name, rec = session_for(path, sid, cwd, rec, limit, cfg, now)
        except (ValueError, KeyError, OSError):
            return None
        keep(sid, rec)
        sessions.deliver(name, CARRY_ON, cwd=cwd, wait=False)
    return None


def relay(event, sid, cwd, prompt, rec, limit, cfg, now, said=None):
    """Send the prompt to the farmout session carrying this conversation on (started on the first one) and answer
    with its reply. Claude Code shows the prompt as sent and the reply under it, and keeps both in its session file,
    so Claude sees them when it's back. When it's done, it notes so: a second hook that waited on this same prompt
    gives way, and the same prompt typed again later doesn't."""
    try:
        return relaying(event, sid, cwd, prompt, rec, limit, cfg, now, said)
    finally:
        done = record(sid)
        if done and said and done.get("said") == said:
            keep(sid, {**done, "said_done": time.time()})


def relaying(event, sid, cwd, prompt, rec, limit, cfg, now, said=None):
    from . import flows, hooks, sessions
    head = heading(limit)
    try:
        name, rec = session_for(event["transcript_path"], sid, cwd, rec, limit, cfg, now)
    except (ValueError, KeyError, OSError) as e:
        return {"continue": False, "stopReason": f"{head}, and nothing could take over: {e}"}
    rec = {**rec, "said": said, "said_at": now}
    base, shown = sessions.load(name).get("base") or 0, set(rec.get("shown") or [])
    late = [r for r in sessions.turns(name) if r.get("id") not in shown and r["status"] != "running"]
    msg = sessions.deliver(name, hooks.redact(prompt)[0], cwd=cwd, wait=False)  # like the conversation, always
    earlier = "".join(f"(An earlier answer, which came after Claude Code stopped waiting:)\n\n{turn_text(r, base)}\n"
                      for r in late)
    rec["shown"] = sorted(shown | {r["id"] for r in late})
    keep(sid, rec)
    wait = cfg.get("wait") if isinstance(cfg.get("wait"), int) and cfg["wait"] > 0 else WAIT
    try:
        r = sessions.wait_msg(name, msg["id"], timeout=min(wait, HOOK_TIMEOUT - 60))
    except TimeoutError:
        return {"continue": False, "stopReason": f"{earlier}{head}; {sessions.load(name)['worker']} is still on it "
                                                 f"(farmout session {name}). `!farmout attach {name}` shows it as it "
                                                 f"goes, and your next prompt here brings the answer."}
    keep(sid, {**rec, "shown": sorted(set(rec["shown"]) | {msg["id"]})})
    who, said = r.get("worker") or sessions.load(name)["worker"], flows.text(r).strip()
    if r["status"] != "ok":
        return {"continue": False, "stopReason": f"{earlier}{head}, and {who} couldn't answer ({r['status']}): {said}"}
    return {"continue": False, "stopReason": f"{earlier}{head}, so {who} answered (farmout session {name}):\n\n{said}"}


def catch_up(sid, rec):
    """Claude is back from its limit. Its session file already holds the relayed prompts and replies (as hook
    messages); tell it once whose replies those were and what may have changed in the files, and, as they come, any
    reply that arrived after Claude Code stopped waiting (the user sees those too)."""
    from . import flows, sessions
    name = rec["name"]
    try:
        meta = sessions.load(name)
    except (KeyError, OSError, ValueError):
        forget(sid)
        return None
    busy = sessions.busy(name) or bool(sessions.pending(name))  # before the turns: one could end in between
    base, told, shown = meta.get("base") or 0, rec.get("told") or 0, set(rec.get("shown") or [])
    done = [r for r in sessions.turns(name) if r["n"] > told and r["status"] != "running"]
    unseen = [r for r in done if r.get("id") not in shown]
    worker, parts, notes = meta["worker"], [], []
    if not rec.get("announced"):
        files = {"write": f"{worker} could edit files in {meta['cwd']}: `git status` and `git diff` show what it changed.",
                 "isolate": f"{worker} worked in its own git worktree, so its changes aren't in the user's files yet: "
                            f"`farmout diff {name}` shows them and `farmout apply {name}` applies them.",
                 "read": f"{worker} had read-only access and changed no files."}
        mode = "isolate" if meta.get("isolate") else "write" if meta.get("write") else "read"
        if mode == "write":
            changed = changed_since(meta["cwd"], rec.get("since") or 0, rec.get("before"))
            if changed:
                files["write"] += (" Changed since then: " + ", ".join(changed[:30]) + (" …" if len(changed) > 30 else "")
                                   + ".")
        parts.append(f"While you were at your usage limit (until {clock(rec.get('until') or 0)}), the user's prompts "
                     f"went to {worker} through farmout (farmout session {name}, which had this whole conversation): the "
                     f"replies marked \"Operation stopped by hook\" since then are {worker}'s, not yours. {files[mode]}")
        notes.append(f"farmout: Claude is back and knows {worker} answered meanwhile (farmout session {name}).")
    if unseen:
        body = "\n".join(turn_text(r, base) for r in unseen)
        full = sessions.path(name) / f"relayed-{unseen[-1]['n']:04d}.md"
        short = sessions.shorten(body, full)
        if short:
            full.write_text(body)
        parts.append(f"These replies from {worker} came after Claude Code stopped waiting, so they aren't above:\n\n"
                     f"{short or body}")
        for r in unseen:
            said = flows.text(r).strip()
            notes.append(f"{worker} answered «{sessions.gist(r['prompt'], 80)}»:\n" + (
                said if len(said) <= SHOW else f"{said[:SHOW]}\n… (`farmout log {name}` has all of it)"))
    if busy:
        running = next((r["prompt"] for r in sessions.turns(name) if r["status"] == "running"), None)
        if not rec.get("announced") or unseen:
            parts.append(f"{worker} is still working on " + (f"«{sessions.gist(running, 200)}»" if running else
                                                           "a message") + f" (`farmout attach {name}` shows it).")
        keep(sid, {**rec, "announced": True, "told": done[-1]["n"] if done else told,
                   "shown": sorted(shown | {r["id"] for r in done})})
    else:
        forget(sid)
    if not parts:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n\n".join(parts)},
            "systemMessage": "\n\n".join(notes)}


def main(stdin=None, stdout=None):
    """`farmout relay hook`: Claude Code's UserPromptSubmit and StopFailure hooks. Never stops a prompt by failing.
    Inside a farmout worker (a `claude -p` farmout started) it does nothing: farmout moves its own tasks at a limit."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if os.environ.get("FARMOUT_WORKER"):
        return 0
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    try:
        out = ((stopped if event.get("hook_event_name") == "StopFailure" else hook)(event)
               if isinstance(event, dict) else None)
    except Exception as e:
        out = {"systemMessage": f"farmout relay: {type(e).__name__}: {e}"}
    if out:
        stdout.write(json.dumps(out))
    return 0
