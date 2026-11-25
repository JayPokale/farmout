"""Sessions: named, saved conversations with one worker, resumed through the worker CLI's own session id.

A message goes into the session's inbox. The session's runner, a background process holding the session's lock,
works through the inbox in order, so a busy session queues new messages instead of refusing them. Commands that
wait for an answer only watch the turn records, so any terminal can attach to any session, and `farmout stop`
stops exactly one session's runner and its worker.

Sessions are found by name, never by folder, and each remembers where its folder sits in its git repository, so a
renamed or moved project keeps its conversations (rehome). A fork branches off any turn and keeps the conversation
up to there; the branches of a conversation form a tree (`farmout tree`, `farmout ui`).
"""
import contextlib
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
GONE = ("{name}'s folder {old} is gone (renamed or moved?). Run this from its new place, or from anywhere in the same "
        "git repository, and the session follows it there; or `farmout send {name} --here \"...\"` continues in the "
        "current folder.")


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


def home_of(cwd):
    """Where a folder sits in its git repository, so it can be found again once the repository is renamed or moved:
    the commit checked out (the repository keeps it wherever it goes), the main working tree, and the folder's path
    inside that. None outside a repository with commits."""
    p = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD", "--git-common-dir", "--show-toplevel"],
                       capture_output=True, text=True)
    lines = p.stdout.splitlines()
    if p.returncode or len(lines) < 3:
        return None
    common = Path(cwd, lines[1]).resolve()
    top = str(common.parent) if common.name == ".git" else os.path.realpath(lines[2])  # from a worktree: the main one
    return {"head": lines[0], "top": top, "rel": os.path.relpath(os.path.realpath(cwd), top)}


def moved_to(home, cwd):
    """(main working tree, folder) where a session's folder is now, when cwd is in the same repository (one that has
    the commit the session recorded); else None. The folder is the same path inside the repository, or None if that
    isn't there (renamed too): never some other folder, where a writer would edit the wrong files."""
    now = home_of(cwd) if home else None
    if not now or subprocess.run(["git", "-C", now["top"], "cat-file", "-e", home["head"] + "^{commit}"],
                                 capture_output=True).returncode:
        return None
    folder = os.path.normpath(os.path.join(now["top"], home["rel"]))
    return now["top"], folder if os.path.isdir(folder) else None


def repair(top):
    """Re-link a moved repository's farmout worktrees: git records their paths, both ways, as absolute paths."""
    d = Path(top, ".farmout", "worktrees")
    trees = [str(p) for p in d.iterdir() if p.is_dir()] if d.is_dir() else []
    if trees:
        subprocess.run(["git", "-C", top, "worktree", "repair", *trees], capture_output=True)


def rehome(name, cwd, force=False):
    """Point a session at its folder's new place; returns the new folder, or None if nothing changed. Unless force,
    only a session whose folder is gone moves, and cwd must be in the same git repository: the session's folder is
    found there by its path inside the repository. force (`--here`) makes cwd its folder. An isolated session's
    worktree moves with its repository. The worker still resumes its own conversation (by id, from the new folder),
    and its next message says where the folder went."""
    meta = load(name)
    old = meta["cwd"]
    if not force and os.path.isdir(old):
        return None
    cwd = os.path.realpath(os.path.expanduser(cwd or os.getcwd()))
    got = moved_to(meta.get("home"), cwd)
    if not got and not force:
        raise ValueError(GONE.format(name=name, old=old))
    if not force and not got[1]:
        raise ValueError(f"{name}'s folder {old} is gone, and {got[0]} has no {meta['home']['rel']} either (renamed "
                         f"too?). From the folder it should use: farmout send {name} --here \"...\"")
    if not got and meta.get("iso"):
        raise ValueError(f"{name} edits in a worktree of its own git repository: run this from inside that repository")
    new = cwd if force else got[1]
    if new == old:
        return None
    iso = meta.get("iso")
    if iso and got and iso["top"] != got[0]:
        was = iso["top"]
        iso.update({k: got[0] + iso[k][len(was):] for k in ("top", "worktree", "patch")
                    if iso.get(k) and (iso[k] == was or iso[k].startswith(was + os.sep))})
    meta.update(cwd=new, home=home_of(new) or meta.get("home"), moved_from=meta.get("moved_from") or old)
    save(meta)
    if got:
        repair(got[0])
    return new


def create(worker, cwd, name=None, write=False, isolate=False, model=None, effort=None, group=None, parent=None,
           fork_of=None, fallback=None, lane=None, fork_at=None, base=0, kind=None, announce=True):
    """A new session, named <kind>-<n> (kind: the worker) unless named. A branch (fork) names its parent, the
    parent's turn it continues from (fork_at) and how many turns came before its first one (base), which numbers its
    turns along the whole conversation. announce=False leaves firing its SessionStart hooks to the caller."""
    if worker not in ENABLED:
        raise ValueError(f"unknown or unavailable worker {worker!r}; available: {', '.join(ENABLED) or 'none'}")
    ROOT.mkdir(parents=True, exist_ok=True)
    if name:
        try:
            path(name).mkdir()
        except FileExistsError:
            raise ValueError(f"a session named {name!r} already exists") from None
    else:
        name = reserve(ROOT, kind or worker)
    d = path(name)
    (d / "inbox").mkdir()
    (d / "turns").mkdir()
    cwd = os.path.realpath(os.path.expanduser(cwd or os.getcwd()))
    meta = {"name": name, "worker": worker, "cwd": cwd, "home": home_of(cwd),
            "write": bool(write or isolate), "isolate": bool(isolate), "model": model, "effort": effort,
            "group": group, "parent": parent, "fork_of": fork_of, "fork_at": fork_at, "base": base,
            "fallback": fallback, "lane": lane, "native_id": None,
            "iso": None, "turns": 0, "created": time.time(), "last": None}
    save(meta)
    if announce:
        started(meta)
    return meta


def started(meta):
    event(meta["name"], "SessionStart", {k: meta[k] for k in ("worker", "cwd", "write", "isolate", "group", "parent")})


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


def post(name, prompt, files=(), attach=(), cwd=None, switch=None, note=None):
    """Queue a message. Relative files resolve against cwd; attach names sessions whose last output goes along.
    switch {"worker", "model"} moves the conversation to that worker or model from this message on. note goes to the
    worker after the prompt but stays out of the conversation's record (a branch's pointer to its context.md)."""
    d = path(name)
    meta = load(name)
    if not os.path.isdir(meta["cwd"]):  # callers rehome() first; a message queued now would only fail
        raise ValueError(GONE.format(name=name, old=meta["cwd"]))
    base = cwd or os.getcwd()
    files = [os.path.realpath(os.path.join(base, os.path.expanduser(f))) for f in files]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    files += [last_output(a) for a in attach]
    msg = {"id": uuid.uuid4().hex[:12], "prompt": prompt, "files": files, "posted": time.time()}
    if switch:
        msg["switch"] = {k: v for k, v in switch.items() if v}
    if note:
        msg["note"] = note
    try:
        with guarded(name):
            load(name)  # still here: a removal didn't get in first
            write_json(d / "inbox" / f"{time.time_ns()}-{msg['id']}.json", msg)
    except FileNotFoundError:
        raise KeyError(f"no session named {name!r} (it was just removed)") from None
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
def guarded(name):
    """A short lock on a session for the steps that mustn't interleave: queuing a message, branching off it, and
    removing it. A remove holds it throughout, so a message or a branch either lands before it (and stops it) or
    finds the session gone."""
    with open(path(name) / "guard.lock", "a") as f:  # a session removed meanwhile: FileNotFoundError
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if os.fstat(f.fileno()).st_ino != os.stat(path(name) / "guard.lock").st_ino:
                raise FileNotFoundError(name)  # removed while this waited (and maybe made again under its name)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


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


def deliver(name, prompt, files=(), attach=(), cwd=None, wait=True, timeout=None, switch=None, note=None):
    """Queue a message and start the runner; wait for the turn's record, or return the queued message."""
    msg = post(name, prompt, files, attach, cwd, switch, note)
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
    message leaves the inbox, so a crash in between can't lose it unseen (at most once, never silently). A message
    may move the conversation to another worker or model (msg["switch"]); so may a usage limit or a lost login,
    when there's a worker to fall back to. Another worker gets the earlier turns as a file."""
    meta, d = load(name), path(name)
    sw = msg.get("switch") or {}
    worker = sw.get("worker") or meta["worker"]
    model = sw.get("model") if sw.get("model") or worker != meta["worker"] else meta["model"]
    # after the newest turn file too: a runner killed between writing one and saving meta mustn't have it overwritten
    n = max([meta["turns"]] + [int(p.stem) for p in (d / "turns").glob("[0-9]*.json") if p.stem.isdigit()]) + 1
    rec = {"n": n, "id": msg["id"], "prompt": msg["prompt"], "files": msg.get("files") or [],
           "worker": worker, "started": time.time(), "status": "running"}
    write_json(d / "turns" / f"{n:04d}.json", rec)
    if inbox_file:
        inbox_file.unlink(missing_ok=True)
    meta["turns"] = n
    save(meta)

    def keep_iso(iso):  # a worktree made in a turn that is then stopped must still be known next turn
        m = load(name)
        m["iso"] = iso
        save(m)
    t = {"worker": meta["worker"], "prompt": msg["prompt"] + (msg.get("note") or ""), "files": rec["files"], "cwd": meta["cwd"],
         "write": meta["write"], "isolate": meta["isolate"], "model": meta["model"], "effort": meta["effort"],
         "persist": True, "fallback": meta.get("fallback"), "session": name, "handoff": handoff(name, n)}
    if meta["native_id"]:
        t["resume"] = meta["native_id"]
    elif meta.get("fork_of") and meta["worker"] == "claude":
        t.update(resume=meta["fork_of"], fork=True, session_id=str(uuid.uuid4()))
    elif meta["worker"] in NEW_ID:
        t["session_id"] = str(uuid.uuid4())
    if meta["worker"] == "api":  # its history, before deciding whether the earlier turns still have to go along
        t["history"] = read_json(d / "messages.json", [])
    if not t.get("resume") and not t.get("history") and not msg.get("note") and (d / "context.md").exists():
        # a branch whose first turn didn't take: nothing holds its earlier turns yet, so this message carries them
        files, note = t["handoff"]()
        t.update(files=t["files"] + files, prompt=t["prompt"] + note)
    if meta.get("moved_from"):  # paths in the conversation so far point at the old folder
        t["prompt"] += (f"\n\n(This project's folder moved from {meta['moved_from']} to {meta['cwd']} since the last "
                        "message: files from earlier in the conversation are now under the new folder.)")
    if (worker, model) != (meta["worker"], meta["model"]):
        t = engine.switch(t, worker, model)
    where = meta["iso"] or ({"label": name, "branch": f"farmout/{name}"} if meta["isolate"] else None)
    try:
        if not os.path.isdir(meta["cwd"]):  # moved since the message was queued
            raise FileNotFoundError(GONE.format(name=name, old=meta["cwd"]))
        res = engine.execute(t, key, where, on_iso=keep_iso)
    except Exception as e:  # a bug or an I/O error: record it, and the runner goes on to the next message
        res = {"ok": False, "worker": worker, "text": f"{type(e).__name__}: {e}", "notes": [], "iso": None,
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
        if res["worker"] != meta["worker"]:  # the conversation moved: what the old worker held is left behind
            meta.update(native_id=res.get("native_id"), fork_of=None)
            (d / "messages.json").unlink(missing_ok=True)
        else:
            meta["native_id"] = res.get("native_id") or meta["native_id"]
        meta["worker"], meta["model"] = res["worker"], res.get("model")
        meta.pop("moved_from", None)  # the worker has been told
        if res.get("messages"):
            write_json(d / "messages.json", res["messages"])
    meta["iso"] = res.get("iso") or meta["iso"]
    rec.update(status="ok" if res["ok"] else "failed", ended=time.time(), secs=res["secs"], worker=res["worker"],
               model=res.get("model"), notes=res["notes"], output=str(out),
               patch=(res.get("iso") or {}).get("patch") if res["ok"] else None)
    write_json(d / "turns" / f"{n:04d}.json", rec)
    meta["last"] = {"n": n, "status": rec["status"], "ended": rec["ended"]}
    save(meta)
    return rec


HANDOFF_FULL = 48_000  # characters (about 12k tokens): a longer conversation is handed over shortened
KEEP_TURNS, TURN_CAP, INDEX_MAX = 3, 10_000, 100
TURN = re.compile(r"## Turn (\d+) \(.*\)")


def gist(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def shorten(text, full):
    """A conversation too long to hand over whole, as what to read first: the older turns one line each, with their
    line range in `full` (the file that holds all of text), and the newest turns in full. None if text is short
    enough to go as it is, or shortening wouldn't save anything."""
    if len(text) <= HANDOFF_FULL:
        return None
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if TURN.fullmatch(line) and lines[i + 1:i + 3] == ["", "### Prompt"]
              and (i == 0 or lines[i - 1] == "")]  # a heading quoted inside an answer seldom has all three
    spans = list(zip(starts, starts[1:] + [len(lines)]))  # [start, end) of each turn
    older, newest = spans[:-KEEP_TURNS], spans[-KEEP_TURNS:]

    def at(a, b):
        return f"lines {a + 1}–{b}"

    def capped(a, b):  # a long part keeps its start and its end; the middle stays in the full file
        part = "\n".join(lines[a:b])
        if len(part) <= TURN_CAP:
            return part
        h, t = TURN_CAP // 3, len(part) - 2 * TURN_CAP // 3
        first, last = a + part[:h].count("\n"), a + part[:t].count("\n") + (part[t - 1] != "\n")
        return f"{part[:h]}\n[… {at(first, last)} of {full} are left out here, in part or whole …]\n{part[t:]}"

    pre_end = starts[0] if starts else len(lines)
    if not older and all(len("\n".join(lines[a:b])) <= TURN_CAP for a, b in [(0, pre_end)] + newest):
        return None

    def entry(a, b):
        part = lines[a + 1:b]
        reply = next((i for i, line in enumerate(part) if line == "### Reply"), None)
        if part[:1] == [""] and part[1:2] == ["### Prompt"] and reply is not None:
            said, got = "\n".join(part[2:reply]), "\n".join(part[reply + 1:])
        else:
            said, got = "\n".join(part), ""
        return f"- {lines[a][3:]}, {at(a, b)}: {gist(said, 140)}" + (f" → {gist(got, 220)}" if got.strip() else "")

    index = [entry(a, b) for a, b in older]
    if len(older) > INDEX_MAX:  # the first turn (usually the task) and the newest ones; a range for the rest
        cut = len(older) - (INDEX_MAX - 2)
        first, last = TURN.fullmatch(lines[older[1][0]]).group(1), TURN.fullmatch(lines[older[cut - 1][0]]).group(1)
        index = index[:1] + [f"- Turns {first}–{last}, {at(older[1][0], older[cut - 1][1])}: not listed one by one"] + index[cut:]
    pre = "\n".join(lines[:pre_end]).strip()
    out = ["# The conversation so far, shortened", "",
           f"{len(spans)} turns, about {len(text) // 4000}k tokens in full. The older turns are one line each, the newest "
           f"{len(newest)} in full. Word for word, everything is in {full}: open the lines given for any turn you need.", ""]
    if pre:
        out += [capped(0, pre_end).strip(), ""]
    if index:
        out += ["## Older turns, one line each", ""] + index + [""]
    return "\n".join(out + [capped(a, b) for a, b in newest])


def shortened(listed, full):
    return (f"The listed file {listed.name} has it shortened: the older turns one line each, the newest in full. Read "
            f"it first; for any older turn you need word for word, open the lines it gives in {full}.")


def pack(text, ctx, full, who):
    """Write the conversation so far for a worker to read: (the file to list, the note for the message). A long one
    goes shortened in ctx and whole in full."""
    short = shorten(text, full)
    if short is None:
        ctx.write_text(text)
        return ctx, f"Context: this message continues an earlier conversation ({who}), which is in the listed file {ctx.name}. Read it first."
    full.write_text(text)
    ctx.write_text(short)
    return ctx, f"Context: this message continues an earlier conversation ({who}). " + shortened(ctx, full)


def handoff(name, n):
    """For turn n, if it can't resume the worker's own conversation (another worker, or a folder its CLI doesn't
    know): () -> (files, note). The file holds the conversation so far, a branch's earlier turns included (shortened
    when long), and the note, added to the message, points the worker at it."""
    def give():
        meta, text = load(name), transcript(name, before=n)
        if not text.strip():
            return [], ""
        d, imported = path(name), meta.get("imported") or {}
        mine = any(r["n"] < n and r["status"] == "ok" for r in turns(name))
        who = f"in {imported['label']}" if imported and not mine else f"with {meta['worker']}"
        ctx, note = pack(text, d / f"context-{n:04d}.md", d / f"context-{n:04d}-full.md", who)
        return [str(ctx)], "\n\n" + note
    return give


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
    """Delete a session, its worktree and branch. It holds the session's runner lock throughout, so no runner starts
    meanwhile, and moves the folder aside in one step before deleting it, so a file that won't delete can't leave
    half a session behind (what's left sits in the state folder's trash)."""
    load(name)
    with holding(name) as mine, guarded(name):
        if not mine or busy(name):
            raise RuntimeError(f"{name} is running; `farmout stop {name}` first")
        meta = load(name)  # as it is now: a turn that just ended may have made a worktree
        if pending(name):  # a message that arrived moments ago would go with it, unanswered
            raise RuntimeError(f"{name} has messages waiting; `farmout stop {name}` drops them")
        if meta.get("iso"):
            top = os.path.realpath(meta["iso"]["worktree"])
            inside = [m["name"] for m in (read_json(path(n) / "meta.json") for n in names() if n != name)
                      if m and (os.path.realpath(m["cwd"]) + os.sep).startswith(top + os.sep)]
            if inside:  # its branches work in its worktree, which goes with it
                raise RuntimeError(f"{', '.join(inside)} work{'s' * (len(inside) == 1)} in {name}'s worktree; remove "
                                   f"{'it' if len(inside) == 1 else 'them'} first (`farmout rm {' '.join(inside)} {name}`)")
            isolate.remove(meta["iso"])
        trash = STATE / "trash"
        trash.mkdir(exist_ok=True)
        gone = trash / f"{name}-{uuid.uuid4().hex[:8]}"
        os.replace(path(name), gone)
    for old in trash.iterdir():  # this one, and anything an earlier removal couldn't delete
        shutil.rmtree(old, ignore_errors=True)


def transcript(name, before=None):
    """The conversation as Markdown from its very start: a branch's context.md holds the turns it branched off with,
    and its own turns are numbered on from there. before=n stops at this session's turn n."""
    meta, ctx = load(name), path(name) / "context.md"
    parts = [ctx.read_text().rstrip("\n") + "\n"] if ctx.exists() else []
    for r in turns(name):
        if before is not None and r["n"] >= before:
            break
        out = Path(r["output"]).read_text() if r.get("output") and os.path.exists(r["output"]) else ""
        parts.append(f"## Turn {(meta.get('base') or 0) + r['n']} ({r.get('worker')}, {r['status']})\n\n"
                     f"### Prompt\n\n{r['prompt']}\n\n### Reply\n\n{out}\n")
    return "\n".join(parts)


def fork(name, new_name=None, worker=None, at=None, model=None):
    """A new session that branches off this conversation after turn `at`, keeping the conversation up to there.
    Turns are numbered along the whole conversation, as `farmout tree` shows them, so a turn from before this branch
    began belongs to a session it branched off, and the new branch starts from that one. Default: the newest finished
    turn; 0 keeps none. Claude branches natively off the end of its own conversation; otherwise the conversation so
    far goes along as a file. It works in that session's folder (its worktree, if any), read-only. Returns the new
    session's meta, and the files and note for its first message."""
    parent = here = load(name)
    while at is not None and at <= (parent.get("base") or 0) and parent.get("parent"):
        try:
            parent = load(parent["parent"])
        except KeyError:  # removed: its turns live on only in this branch's context.md
            break
    if parent is not here and not os.path.isdir(parent["cwd"]):  # moved with the branch it was reached from
        rehome(parent["name"], here["cwd"])
        parent = load(parent["name"])
    name, base = parent["name"], parent.get("base") or 0
    done = [r["n"] for r in turns(name) if r["status"] != "running"]
    at = (done[-1] if done else 0) if at is None else at - base
    if at != 0 and at not in done:
        raise ValueError(f"{name} has no finished turn {base + at} to branch from"
                         + (f"; its turns are {base + 1} to {base + done[-1]}" if done else "; it has none yet"))
    worker = worker or parent["worker"]
    native = worker == parent["worker"] == "claude" and parent["native_id"] and at == parent["turns"]
    owner = parent
    while not owner.get("iso") and owner.get("parent"):  # whose worktree, if any, the branch will work in
        try:
            owner = load(owner["parent"])
        except KeyError:
            break
    owner = owner["name"] if owner.get("iso") and owner["name"] != name else None
    try:
        with guarded(name), (guarded(owner) if owner else contextlib.nullcontext()):  # removals wait, or went first
            parent = load(name)
            iso = parent.get("iso")
            cwd = os.path.join(iso["worktree"], iso.get("rel", ".")) if iso else parent["cwd"]
            if not os.path.isdir(cwd):  # say so before making a branch that could never run
                raise ValueError(f"can't branch off {name}: its {'worktree' if iso else 'folder'} {cwd} is gone")
            root = parent
            while root.get("parent"):  # branches are named after their conversation: cache-1, cache-2, ...
                try:
                    root = load(root["parent"])
                except KeyError:
                    break
            meta = create(worker, cwd, name=new_name, effort=parent["effort"], group=parent.get("group"), parent=name,
                          kind=root["name"] if len(root["name"]) <= 56 else None,
                          model=model or (parent["model"] if worker == parent["worker"] else None),
                          fork_of=parent["native_id"] if native else None, fork_at=at, base=(parent.get("base") or 0) + at,
                          announce=False)
    except FileNotFoundError:
        raise KeyError(f"no session named {name!r} (it was just removed)") from None
    started(meta)  # its hooks run outside the lock: one that messages the parent mustn't wait on it
    if parent.get("moved_from"):  # its folder moved and nobody has been told yet: the branch tells its worker
        meta["moved_from"] = parent["moved_from"]
        save(meta)
    files, note, text = [], None, transcript(name, before=at + 1)
    if text.strip():  # kept even for a native fork: a later move to another worker or folder hands it over
        ctx = path(meta["name"]) / "context.md"
        ctx.write_text(text)
        short = None if native else shorten(text, ctx)
        if short is not None:  # a long conversation: what to read first, with line ranges into context.md
            brief = path(meta["name"]) / "context-brief.md"
            brief.write_text(short)
            note = f"\n\nContext: this continues an earlier conversation (with {parent['worker']}). " + shortened(brief, ctx)
            files.append(str(brief))
        elif not native:
            note = (f"\n\nContext: this continues an earlier conversation (with {parent['worker']}), which is in "
                    "the listed file context.md. Read it first.")
            files.append(str(ctx))
    return meta, files, note


def summary(meta):
    name, q = meta["name"], len(pending(meta["name"]))
    state = "running" if busy(name) else "queued" if q else (meta.get("last") or {}).get("status") or "new"
    return {"name": name, "worker": meta["worker"], "model": meta.get("model"), "state": state, "queued": q,
            "turns": meta["turns"], "created": meta.get("created"),
            "updated": (meta.get("last") or {}).get("ended") or meta.get("created"), "cwd": meta["cwd"],
            "group": meta.get("group"), "parent": meta.get("parent"), "fork_at": meta.get("fork_at"),
            "base": meta.get("base") or 0, "isolate": meta.get("isolate"), "top": (meta.get("home") or {}).get("top"),
            "branch": (meta.get("iso") or {}).get("branch"), "imported": (meta.get("imported") or {}).get("label")}
