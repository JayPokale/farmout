"""The limit relay. When Claude Code hits its usage limit, what you type next goes to another subscription with the
whole conversation, and its answer shows in Claude Code; once the limit is over, Claude is told what was asked and
answered meanwhile. It runs as Claude Code's UserPromptSubmit hook (the plugin has it; `farmout relay on` turns it
on) and reads the limit, with its reset time, from the session file Claude Code keeps. While it's off, a prompt at
the limit only gets a line saying it exists. `farmout continue` uses carry() to start its sessions the same way."""
import datetime
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
CATCH_UP = 24_000  # characters of relayed turns Claude is told inline; past it, shortened with the rest in a file
TAIL = 512 * 1024  # bytes of the session file read for its newest reply
GRACE = 300  # a relay record this long past its reset belongs to an earlier limit
MODES = ("read", "write", "isolate")
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


def carry(s, worker, model=None, cwd=None, name=None, write=False, isolate=False, effort=None, fallback=None):
    """A new farmout session that carries on native session s: its prompts, replies and actions (credentials
    replaced) become the session's context.md, and its turns are numbered on from them. (meta, summary, turns,
    secrets replaced)."""
    from . import hooks, sessions
    summary, turns = native.read(s)
    if not turns and not summary:
        raise ValueError(f"{s['label']} session {s['id']} has nothing to carry on from yet")
    text, secrets = hooks.redact(native.transcript(s, summary, turns))
    cwd = s["cwd"] if os.path.isdir(s["cwd"]) else cwd
    meta = sessions.create(worker, cwd, name=name, write=write, isolate=isolate, model=model, effort=effort,
                           fallback=fallback, base=len(turns))
    meta["imported"] = {"from": s["from"], "label": s["label"], "id": s["id"], "file": s["file"], "turns": len(turns)}
    sessions.save(meta)
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


def records():
    out = []
    for p in sorted(RECORDS.glob("*.json")) if RECORDS.is_dir() else []:
        rec = record(p.stem)
        if rec:
            out.append(rec)
    return out


def keep(sid, rec):
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
    until being when the limit resets (Unix seconds), else None."""
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
    if not last or not last.get("isApiErrorMessage") or str(last.get("entrypoint") or "").startswith("sdk"):
        return None  # `claude -p` runs (farmout's own among them) handle their limits themselves
    text = " ".join(native.text_of(b.get("text")) for b in native.blocks_of(dig(last, "message", "content"))
                    if b.get("type") == "text").strip()
    if last.get("apiError") != "usage_limit_reached" and not (last.get("error") == "rate_limit" and USAGE.search(text)):
        return None
    at = stamp(last) or os.path.getmtime(path)
    until = next((v for v in (dig(last, "apiErrorParams", "rate_limit_info", "resetsAt"), dig(last, "quotaLimits", "resetsAt"))
                  if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    if until is None:
        from .workers import reset_in
        secs = reset_in(text, now=datetime.datetime.fromtimestamp(at).astimezone())
        until = at + (secs if secs is not None else 900)
    return {"until": until, "text": text, "at": at, "uuid": str(last.get("uuid") or "")}


def clock(t):
    return time.strftime("%H:%M" if t - time.time() < 86400 else "%a %H:%M", time.localtime(t))


def hook(event, now=None):
    """What Claude Code's UserPromptSubmit hook answers, as a dict; None lets the prompt go on as usual."""
    now = now or time.time()
    sid, path = event.get("session_id"), event.get("transcript_path")
    if not isinstance(sid, str) or not sid or not isinstance(path, str):
        return None
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isdir(event["cwd"]) else os.getcwd()
    prompt = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
    rec, limit = record(sid), limit_in(path)
    if limit and rec and rec.get("back", 0) >= limit["at"]:  # `farmout relay back`: Claude is back before the reset
        limit = None
    if limit and limit["until"] > now:
        if not prompt.strip() or prompt.lstrip().startswith(("/", "!")):
            return None  # Claude Code's own commands
        cfg = config()
        if not cfg.get("on"):
            return {"systemMessage": f"farmout: Claude is at its usage limit until {clock(limit['until'])}. `farmout "
                                     f"relay on` sends what you type next to another subscription, with this "
                                     f"conversation; or run `!farmout continue --write`."}
        return relay(event, sid, cwd, prompt, rec, limit, cfg, now)
    if rec and rec.get("name"):
        return catch_up(sid, rec)
    return None


def turn_text(r, base):
    from . import flows
    return (f"## Turn {base + r['n']} ({r.get('worker')}, {r['status']})\n\n### Prompt\n\n{r['prompt']}\n\n"
            f"### Reply\n\n{flows.text(r).strip()}\n")


def relay(event, sid, cwd, prompt, rec, limit, cfg, now):
    """Send the prompt to the farmout session carrying this conversation on (started on the first one) and answer
    with its reply. Claude Code shows the prompt as sent and the reply under it, and keeps both in its session file,
    so Claude sees them when it's back."""
    from . import flows, hooks, sessions
    from .config import settings
    name = (rec or {}).get("name")
    if name and (not sessions.path(name).is_dir() or (rec.get("until") or 0) < now - GRACE):
        name = None  # removed, or carried on from an earlier limit
    head = f"Claude is at its usage limit until {clock(limit['until'])}"
    if not name:
        s = {"from": "claude", "label": native.LABELS["claude"], "file": event["transcript_path"], "cwd": cwd, "id": sid,
             "updated": now}
        mode = cfg.get("mode") if cfg.get("mode") in MODES else "read"
        try:
            worker, model = pick(s, cwd, cfg.get("worker"))
            meta = carry(s, worker, model, cwd, write=mode == "write", isolate=mode == "isolate")[0]
        except (ValueError, KeyError, OSError) as e:
            return {"continue": False, "stopReason": f"{head}, and nothing could take over: {e}"}
        name, rec = meta["name"], {"session": sid, "file": event["transcript_path"], "cwd": cwd, "since": now,
                                   "told": 0, "shown": []}
    rec = {**rec, "name": name, "until": limit["until"], "limit": limit["text"]}
    base, shown = sessions.load(name).get("base") or 0, set(rec.get("shown") or [])
    late = [r for r in sessions.turns(name) if r.get("id") not in shown and r["status"] != "running"]
    try:
        redact = bool(settings(cwd).get("redact"))
    except Exception:
        redact = False
    msg = sessions.deliver(name, hooks.redact(prompt)[0] if redact else prompt, cwd=cwd, wait=False)
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
    messages); tell it once whose replies those were, what may have changed in the files, and any reply that came
    after Claude Code stopped waiting."""
    from . import sessions
    name = rec["name"]
    try:
        meta = sessions.load(name)
    except (KeyError, OSError, ValueError):
        forget(sid)
        return None
    base, told, shown = meta.get("base") or 0, rec.get("told") or 0, set(rec.get("shown") or [])
    done = [r for r in sessions.turns(name) if r["n"] > told and r["status"] != "running"]
    busy = sessions.busy(name) or bool(sessions.pending(name))
    if not done and not busy:
        forget(sid)
        return None
    worker = meta["worker"]
    unseen = [r for r in done if r.get("id") not in shown]
    body = "\n".join(turn_text(r, base) for r in unseen)
    if len(body) > CATCH_UP:
        full = sessions.path(name) / f"relayed-{unseen[-1]['n']:04d}.md"
        full.write_text(body)
        body = sessions.shorten(body, full) or body
    files = {"write": f"{worker} could edit files in {meta['cwd']}: `git status` and `git diff` show what it changed.",
             "isolate": f"{worker} worked in its own git worktree, so its changes aren't in the user's files yet: "
                        f"`farmout diff {name}` shows them and `farmout apply {name}` applies them.",
             "read": f"{worker} had read-only access and changed no files."}
    mode = "isolate" if meta.get("isolate") else "write" if meta.get("write") else "read"
    context = (f"While you were at your usage limit (until {clock(rec.get('until') or 0)}), the user's prompts went to "
               f"{worker} through farmout (farmout session {name}, which had this whole conversation): the replies "
               f"marked \"Operation stopped by hook\" since then are {worker}'s, not yours. {files[mode]}")
    if unseen:
        context += (f"\n\nThese replies came after Claude Code stopped waiting, so they aren't above:\n\n{body}")
    if busy:
        running = next((r["prompt"] for r in sessions.turns(name) if r["status"] == "running"), None)
        context += (f"\n\n{worker} is still working on " + (f"«{sessions.gist(running, 200)}»" if running else "a message")
                    + f" (`farmout attach {name}` shows it).")
        keep(sid, {**rec, "told": done[-1]["n"] if done else told, "shown": sorted(shown | {r["id"] for r in done})})
    else:
        forget(sid)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context},
            "systemMessage": f"farmout: Claude is back and knows {worker} answered meanwhile (farmout session {name})."}


def main(stdin=None, stdout=None):
    """`farmout relay hook`: Claude Code's UserPromptSubmit hook. Never stops a prompt by failing."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    try:
        out = hook(event) if isinstance(event, dict) else None
    except Exception as e:
        out = {"systemMessage": f"farmout relay: {type(e).__name__}: {e}"}
    if out:
        stdout.write(json.dumps(out))
    return 0
