"""Worker processes. Each runs in its own process group, so a timeout, a cancel, `farmout stop` or shutdown
kills its whole tree, not just the CLI at the top."""
import os
import signal
import subprocess
import threading
import time

TIMEOUT = int(os.environ.get("FARMOUT_TIMEOUT", 1200))
GRACE = 10  # seconds a stopped worker gets after TERM before what's left of its process group is killed
running, cancelled = {}, set()  # key (an MCP request id, a session name) -> live processes; cancelled keys
lock = threading.Lock()


def signal_group(p, sig=signal.SIGTERM):
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass


def group_alive(p):
    try:
        os.killpg(p.pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop(p, grace=None):
    """TERM the worker's process group, give it `grace` seconds, then KILL what's left of the group: a CLI that
    ignores TERM, and children that ignore it or outlive the CLI (they'd otherwise run on, unowned)."""
    grace = GRACE if grace is None else grace
    signal_group(p)
    deadline = time.time() + grace
    try:
        p.wait(grace)
    except subprocess.TimeoutExpired:
        pass
    while group_alive(p) and time.time() < deadline:
        time.sleep(0.1)
    signal_group(p, signal.SIGKILL)
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        pass


def drain(p):
    """Close a stopped worker's pipes (what an interrupted communicate() leaves open)."""
    try:
        p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        for f in (p.stdin, p.stdout, p.stderr):
            if f:
                f.close()


def run(cmd, key, cwd, stdin=None, env=None, timeout=None):
    """stdout of cmd; raises RuntimeError on a non-zero exit, a timeout or a cancel."""
    timeout = timeout or TIMEOUT
    with lock:
        if key in cancelled:
            raise RuntimeError("cancelled")
        env = {**(os.environ if env is None else env), "FARMOUT_WORKER": "1"}  # hooks in a worker's CLI can tell
        p = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, start_new_session=True,
                             stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        running.setdefault(key, set()).add(p)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(p)
        drain(p)
        raise RuntimeError(f"timed out after {timeout}s") from None
    except BaseException:  # Ctrl-C or SIGTERM in a CLI process: take the worker down too
        stop(p)
        drain(p)
        raise
    finally:
        with lock:
            running.get(key, set()).discard(p)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} exited {p.returncode}: {(err or out).strip()[-1500:]}")
    return out


def shell(cmd, cwd, timeout=None):
    """A user's command (tests, build) in its own process group: (passed, last 2000 chars of output)."""
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, text=True, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out = p.communicate(timeout=timeout or TIMEOUT)[0]
    except subprocess.TimeoutExpired:
        stop(p)
        drain(p)
        return False, f"timed out after {timeout or TIMEOUT}s"
    except BaseException:
        stop(p)
        drain(p)
        raise
    return p.returncode == 0, out[-2000:]


def cancel(key):
    with lock:
        cancelled.add(key)
        procs = list(running.get(key, ()))
    for p in procs:  # TERM now; KILL whatever ignores it, without holding up the caller
        threading.Thread(target=stop, args=(p,), daemon=True).start()


def kill_all(grace=3):
    """Shutdown: TERM every worker's group, then KILL what's left after `grace` seconds."""
    with lock:
        procs = [p for ps in running.values() for p in ps]
    threads = [threading.Thread(target=stop, args=(p, grace), daemon=True) for p in procs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(grace + 6)
