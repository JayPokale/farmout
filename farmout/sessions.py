"""Sessions: named, saved conversations with one worker, resumed through the worker CLI's own session id.

A message goes into the session's inbox. The session's runner, a background process holding the session's lock,
works through the inbox in order, so a busy session queues new messages instead of refusing them. Commands that
wait for an answer only watch the turn records, so any terminal can attach to any session, and `farmout stop`
stops exactly one session's runner and its worker.
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import engine, hooks, isolate, proc
from .config import STATE
from .workers import ENABLED

ROOT = STATE / "sessions"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
PKG = str(Path(__file__).resolve().parent.parent)
NEW_ID = ("claude", "copilot")  # CLIs that take a session id we choose; codex and agy report theirs
RUNNER = "import sys; sys.path.insert(0, sys.argv.pop(1)); from farmout.cli import main; sys.exit(main(sys.argv[1:]))"


def path(name):
    if not NAME.fullmatch(name or ""):
        raise ValueError(f"bad session name {name!r}: use letters, digits, '.', '_' and '-'")
    return ROOT / name


def write_json(p, data):
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def load(name):
    meta = read_json(path(name) / "meta.json")
    if meta is None:
        raise KeyError(f"no session named {name!r} (`farmout ls` lists them)")
    return meta


def save(meta):
    write_json(path(meta["name"]) / "meta.json", meta)


def names():
    if not ROOT.exists():
        return []
    metas = [m for m in (read_json(p / "meta.json") for p in ROOT.iterdir()) if m]
    return [m["name"] for m in sorted(metas, key=lambda m: m.get("created", 0))]


def reserve(root, kind):
    """Atomically claim the next free <kind>-<n> directory under root; returns its name."""
    root.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for m in (re.match(re.escape(kind) + r"-(\d+)(?:-|$)", p.name) for p in root.iterdir()) if m]
    n = max(nums, default=0) + 1
    while True:
        try:
            (root / f"{kind}-{n}").mkdir()
            return f"{kind}-{n}"
        except FileExistsError:
            n += 1


def new_group(kind):
    """A fresh group id such as review-3; its sessions are named review-3-codex and so on."""
    return reserve(ROOT / ".groups", kind)


def create(worker, cwd, name=None, write=False, isolate=False, model=None, effort=None, group=None, parent=None,
           fork_of=None, fallback=None):
    if worker not in ENABLED:
        raise ValueError(f"unknown or unavailable worker {worker!r}; available: {', '.join(ENABLED) or 'none'}")
    ROOT.mkdir(parents=True, exist_ok=True)
    if name:
        try:
            path(name).mkdir()
        except FileExistsError:
            raise ValueError(f"a session named {name!r} already exists") from None
    else:
        name = reserve(ROOT, worker)
    d = path(name)
    (d / "inbox").mkdir()
    (d / "turns").mkdir()
    meta = {"name": name, "worker": worker, "cwd": os.path.realpath(os.path.expanduser(cwd or os.getcwd())),
            "write": bool(write or isolate), "isolate": bool(isolate), "model": model, "effort": effort,
            "group": group, "parent": parent, "fork_of": fork_of, "fallback": fallback, "native_id": None,
            "iso": None, "turns": 0, "created": time.time(), "last": None}
    save(meta)
    event(name, "SessionStart", {k: meta[k] for k in ("worker", "cwd", "write", "isolate", "group", "parent")})
    return meta


def event(name, kind, payload):
    """Fire a session event's hooks; what they report goes to the session's hooks.log."""
    try:
        _, notes = hooks.fire(kind, {"session": name, **payload}, load(name)["cwd"], subject=name)
    except Exception as e:
        notes = [f"{kind} hooks: {type(e).__name__}: {e}"]
    if notes:
        with open(path(name) / "hooks.log", "a") as f:
            f.write("".join(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {kind}: {n}\n" for n in notes))


def pending(name):
    d = path(name) / "inbox"
    return sorted(d.glob("[0-9]*.json")) if d.exists() else []


def turns(name):
    d = path(name) / "turns"
    return [r for r in (read_json(p) for p in sorted(d.glob("[0-9]*.json"))) if r] if d.exists() else []


def find(name, msg_id):
    return next((r for r in reversed(turns(name)) if r.get("id") == msg_id), None)


def last_output(name):
    """The newest successful turn's output file."""
    rec = next((r for r in reversed(turns(name)) if r.get("status") == "ok"), None)
    if not rec:
        raise ValueError(f"session {name!r} has no finished turn to attach")
    return rec["output"]


def stash(text, kind="stdin"):
    """Save text (e.g. piped stdin) as a file a worker can be handed."""
    d = STATE / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{kind}.txt"
    p.write_text(text)
    return str(p)


def post(name, prompt, files=(), attach=(), cwd=None):
    """Queue a message. Relative files resolve against cwd; attach names sessions whose last output goes along."""
    d = path(name)
    load(name)
    base = cwd or os.getcwd()
    files = [os.path.realpath(os.path.join(base, os.path.expanduser(f))) for f in files]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    files += [last_output(a) for a in attach]
    msg = {"id": uuid.uuid4().hex[:12], "prompt": prompt, "files": files, "posted": time.time()}
    write_json(d / "inbox" / f"{time.time_ns()}-{msg['id']}.json", msg)
    event(name, "MessageReceived", {"message": msg["id"], "prompt": prompt, "files": files})
    return msg


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def is_farmout(pid, *argv):
    """True if pid is a live process whose arguments include argv in a row (e.g. "_runner", name). A pid left
    behind by a killed runner may since belong to an unrelated process, which must never be signalled."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except FileNotFoundError:
        if Path("/proc/self").exists():  # Linux, and no such process
            return False
        # macOS and the BSDs: ps lives in /bin whatever PATH says; if it can't be asked, assume the pid isn't ours
        ps = "/bin/ps" if os.path.exists("/bin/ps") else shutil.which("ps")
        try:
            args = subprocess.run([ps, "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
        except (OSError, TypeError):
            return False
    except OSError:
        return False
    return any(args[i:i + len(argv)] == list(argv) for i in range(len(args)))


def runner_pid(name):
    try:
        pid = int((path(name) / "runner.pid").read_text())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) and is_farmout(pid, "_runner", name) else None


def busy(name):
    return runner_pid(name) is not None


@contextmanager
def holding(name):
    """The session's runner lock; yields whether this process got it."""
    d = path(name)
    f = open(d / "runner.lock", "a")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        (d / "runner.pid").write_text(str(os.getpid()))
        try:
            yield True
        finally:
            (d / "runner.pid").unlink(missing_ok=True)
            fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


def spawn(args, log):
    """A detached farmout process (a session runner or a pipeline run)."""
    with open(log, "ab") as out:
        p = subprocess.Popen([sys.executable, "-c", RUNNER, PKG, *args], stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True, cwd=str(STATE))
    threading.Thread(target=p.wait, daemon=True).start()  # reap it, so a long-lived parent keeps no zombies
    return p


def kick(name):
    """Start a runner if messages wait and none is running. A runner that loses the race for the lock just exits,
    and every lock holder checks the inbox again after letting go, so no message is stranded."""
    if pending(name) and not busy(name):
        spawn(["_runner", name], path(name) / "runner.log")


def deliver(name, prompt, files=(), attach=(), cwd=None, wait=True, timeout=None):
    """Queue a message and start the runner; wait for the turn's record, or return the queued message."""
    msg = post(name, prompt, files, attach, cwd)
    kick(name)
    return wait_msg(name, msg["id"], timeout) if wait else msg


def wait_msg(name, msg_id, timeout=None, poll=0.15):
    deadline, kicked, gone = time.time() + timeout if timeout else None, time.time(), 0
    while True:
        rec = find(name, msg_id)
        if rec and rec["status"] != "running":
            return rec
        if not busy(name):
            # the runner may have finished the turn and exited between the two checks: it writes the final record
            # before it exits, so read it once more before calling the turn interrupted
            rec = find(name, msg_id) or rec
            if rec and rec["status"] != "running":
                return rec
            if rec:  # its runner died mid-turn
                return {**rec, "status": "interrupted", "error": f"the runner stopped mid-turn; see {path(name) / 'runner.log'}"}
            if not any(f.name.endswith(f"-{msg_id}.json") for f in pending(name)):
                gone += 1  # neither queued nor run: `farmout stop` dropped it (checked a few times, to be sure)
                if gone > 3:
                    return {"id": msg_id, "n": None, "status": "cancelled", "worker": None,
                            "error": "the message was dropped before it ran (farmout stop)"}
            elif time.time() - kicked > 3:  # a runner that died before taking the message: start another
                kick(name)
                kicked = time.time()
        if deadline and time.time() > deadline:
            raise TimeoutError(f"no answer from {name} within {timeout}s; it keeps running (`farmout attach {name}`)")
        time.sleep(poll)


def wait_idle(name, timeout=None, poll=0.15):
    deadline, kicked = time.time() + timeout if timeout else None, 0
    while busy(name) or pending(name):
        if deadline and time.time() > deadline:
            raise TimeoutError(f"{name} is still busy after {timeout}s")
        if pending(name) and not busy(name) and time.time() - kicked > 3:
            kick(name)
            kicked = time.time()
        time.sleep(poll)
    return (turns(name) or [None])[-1]


def drain(name, key):
    while True:
        queue = pending(name)
        if not queue:
            return
        msg = read_json(queue[0])
        if not msg or find(name, msg["id"]):  # unreadable, or taken by a runner that died before tidying up
            queue[0].unlink(missing_ok=True)
            continue
        turn(name, msg, key, queue[0])


def turn(name, msg, key, inbox_file=None):
    """Run one message through the session's worker, resuming its conversation. The turn is recorded before the
    message leaves the inbox, so a crash in between can't lose it unseen (at most once, never silently)."""
    meta, d = load(name), path(name)
    # after the newest turn file too: a runner killed between writing one and saving meta mustn't have it overwritten
    n = max([meta["turns"]] + [int(p.stem) for p in (d / "turns").glob("[0-9]*.json") if p.stem.isdigit()]) + 1
    rec = {"n": n, "id": msg["id"], "prompt": msg["prompt"], "files": msg.get("files") or [],
           "worker": meta["worker"], "started": time.time(), "status": "running"}
    write_json(d / "turns" / f"{n:04d}.json", rec)
    if inbox_file:
        inbox_file.unlink(missing_ok=True)
    meta["turns"] = n
    save(meta)

    def keep_iso(iso):  # a worktree made in a turn that is then stopped must still be known next turn
        m = load(name)
        m["iso"] = iso
        save(m)
    t = {"worker": meta["worker"], "prompt": msg["prompt"], "files": rec["files"], "cwd": meta["cwd"],
         "write": meta["write"], "isolate": meta["isolate"], "model": meta["model"], "effort": meta["effort"],
         "persist": True, "fallback": meta.get("fallback") if n == 1 else [], "session": name}
    if meta["native_id"]:
        t["resume"] = meta["native_id"]
    elif meta.get("fork_of") and meta["worker"] == "claude":
        t.update(resume=meta["fork_of"], fork=True, session_id=str(uuid.uuid4()))
    elif meta["worker"] in NEW_ID:
        t["session_id"] = str(uuid.uuid4())
    if meta["worker"] == "api":
        t["history"] = read_json(d / "messages.json", [])
    where = meta["iso"] or ({"label": name, "branch": f"farmout/{name}"} if meta["isolate"] else None)
    try:
        res = engine.execute(t, key, where, on_iso=keep_iso)
    except Exception as e:  # a bug or an I/O error: record it, and the runner goes on to the next message
        res = {"ok": False, "worker": meta["worker"], "text": f"{type(e).__name__}: {e}", "notes": [], "iso": None,
               "secs": round(time.time() - rec["started"], 1)}
    except BaseException:  # stopped
        rec.update(status="stopped", ended=time.time())
        write_json(d / "turns" / f"{n:04d}.json", rec)
        meta = load(name)
        meta["last"] = {"n": n, "status": "stopped", "ended": rec["ended"]}
        save(meta)
        raise
    out = d / "turns" / f"{n:04d}.md"
    out.write_text(res["text"])
    meta = load(name)
    if res["ok"]:
        meta["native_id"] = res.get("native_id") or meta["native_id"]
        meta["worker"] = res["worker"]  # it changes after a fallback
        if res.get("messages"):
            write_json(d / "messages.json", res["messages"])
    meta["iso"] = res.get("iso") or meta["iso"]
    rec.update(status="ok" if res["ok"] else "failed", ended=time.time(), secs=res["secs"], worker=res["worker"],
               notes=res["notes"], output=str(out), patch=(res.get("iso") or {}).get("patch") if res["ok"] else None)
    write_json(d / "turns" / f"{n:04d}.json", rec)
    meta["last"] = {"n": n, "status": rec["status"], "ended": rec["ended"]}
    save(meta)
    return rec


def runner_main(name):
    """`farmout _runner <name>`: work through the inbox while holding the lock, then check it once more."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # `farmout stop` sends SIGTERM
    try:
        with holding(name) as got:
            if got:
                drain(name, ("session", name))
    except KeyboardInterrupt:
        proc.kill_all()
        return 130
    finally:
        kick(name)  # anything that arrived as we let go
    return 0


def stop(name):
    """Drop the queued messages and stop the running turn, if any. True if something was running."""
    for f in pending(name):
        f.unlink(missing_ok=True)
    pid = runner_pid(name)
    if pid:
        os.kill(pid, signal.SIGTERM)
    return pid is not None


def remove(name):
    meta = load(name)
    if busy(name):
        raise RuntimeError(f"{name} is running; `farmout stop {name}` first")
    if meta.get("iso"):
        isolate.remove(meta["iso"])
    shutil.rmtree(path(name))


def transcript(name):
    parts = []
    for r in turns(name):
        out = Path(r["output"]).read_text() if r.get("output") and os.path.exists(r["output"]) else ""
        parts.append(f"## Turn {r['n']} ({r.get('worker')}, {r['status']})\n\n### Prompt\n\n{r['prompt']}\n\n### Reply\n\n{out}\n")
    return "\n".join(parts)


def fork(name, prompt, new_name=None, worker=None):
    """A new session that starts from this one's conversation: a native fork for claude, otherwise the transcript
    goes along as a file. It works in this session's directory (its worktree, if any), read-only."""
    parent = load(name)
    worker = worker or parent["worker"]
    native = worker == parent["worker"] == "claude" and parent["native_id"]
    iso = parent.get("iso")
    cwd = os.path.join(iso["worktree"], iso.get("rel", ".")) if iso else parent["cwd"]
    meta = create(worker, cwd, name=new_name, model=parent["model"] if worker == parent["worker"] else None,
                  effort=parent["effort"], group=parent.get("group"), parent=name,
                  fork_of=parent["native_id"] if native else None)
    files = []
    if not native and parent["turns"]:
        ctx = path(meta["name"]) / "context.md"
        ctx.write_text(transcript(name))
        prompt += (f"\n\nContext: this continues an earlier conversation (with {parent['worker']}), which is in the "
                   "listed file context.md. Read it first.")
        files.append(str(ctx))
    return meta, prompt, files


def summary(meta):
    name, q = meta["name"], len(pending(meta["name"]))
    state = "running" if busy(name) else "queued" if q else (meta.get("last") or {}).get("status") or "new"
    return {"name": name, "worker": meta["worker"], "state": state, "queued": q, "turns": meta["turns"],
            "created": meta.get("created"), "updated": (meta.get("last") or {}).get("ended") or meta.get("created"),
            "cwd": meta["cwd"], "group": meta.get("group"),
            "isolate": meta.get("isolate"), "branch": (meta.get("iso") or {}).get("branch")}
