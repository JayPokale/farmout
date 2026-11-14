"""Worker processes. Each runs in its own process group, so a timeout, a cancel, `farmout stop` or shutdown
kills its whole tree, not just the CLI at the top."""
import os
import signal
import subprocess
import threading

TIMEOUT = int(os.environ.get("FARMOUT_TIMEOUT", 1200))
running, cancelled = {}, set()  # key (an MCP request id, a session name) -> live processes; cancelled keys
lock = threading.Lock()


def signal_group(p, sig=signal.SIGTERM):
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass


def stop(p):
    signal_group(p)
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        signal_group(p, signal.SIGKILL)


def run(cmd, key, cwd, stdin=None, env=None, timeout=None):
    """stdout of cmd; raises RuntimeError on a non-zero exit, a timeout or a cancel."""
    timeout = timeout or TIMEOUT
    with lock:
        if key in cancelled:
            raise RuntimeError("cancelled")
        p = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, start_new_session=True,
                             stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        running.setdefault(key, set()).add(p)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(p)
        raise RuntimeError(f"timed out after {timeout}s") from None
    except BaseException:  # Ctrl-C or SIGTERM in a CLI process: take the worker down too
        stop(p)
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
        return False, f"timed out after {timeout or TIMEOUT}s"
    except BaseException:
        stop(p)
        raise
    return p.returncode == 0, out[-2000:]


def cancel(key):
    with lock:
        cancelled.add(key)
        procs = list(running.get(key, ()))
    for p in procs:
        signal_group(p)


def kill_all():
    with lock:
        procs = [p for ps in running.values() for p in ps]
    for p in procs:
        signal_group(p)
