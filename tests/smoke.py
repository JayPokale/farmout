"""Release smoke test: drive the real worker CLIs through server.py on your own logins.

Spends a little quota on every enabled worker. Export the same FARMOUT_* variables as your setup
first, e.g.  FARMOUT_CLAUDE_DIR=~/.claude-2 python3 tests/smoke.py [worker ...]
Each worker gets these tasks, all in one parallel delegate call:
  read       it can read a file it is given
  read-only  without write:true, told to create a file and run `touch`, it changes nothing
  write      with write:true, it can create a file in cwd (CLI workers only)
and then, in parallel through the CLI:
  session    `farmout run` then `farmout send`: the second turn remembers the first
Exits 1 if any check fails.
"""
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from test_server import SERVER, Server

VERSION = {"antigravity": ["agy", "--version"], "codex": ["codex", "--version"], "claude": ["claude", "--version"],
           "copilot": ["copilot", "--version"], "deepseek": ["dsh", "--version"]}


def version(worker):
    if worker == "api":
        return f"{os.environ['FARMOUT_API_MODEL']} at {os.environ['FARMOUT_API_BASE']}"
    out = subprocess.run(VERSION[worker], capture_output=True, text=True)
    return ((out.stdout or out.stderr).strip().splitlines() or ["?"])[0]


def session(w, tmp):
    """(passed, detail): a real two-turn session through the CLI."""
    word = secrets.choice(["PELICAN", "LANTERN", "MAGNOLIA", "QUASAR", "ORCHARD"])
    cwd = tmp / w / "session"
    cwd.mkdir(parents=True)
    env = {**os.environ, "FARMOUT_STATE": str(tmp / "state")}

    def farmout(*args):
        return subprocess.run([sys.executable, str(SERVER), *args], cwd=cwd, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=900)
    first = farmout("run", w, "-n", f"smoke-{w}", f"Remember the secret word {word} for later. Reply with only: ok")
    second = farmout("send", f"smoke-{w}", "What secret word did I ask you to remember? Reply with just the word.")
    ok = first.returncode == 0 and second.returncode == 0 and word in second.stdout.upper()
    return ok, f"{second.stdout.strip()[:120]!r} {second.stderr.strip()[-200:]!r}"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="farmout-smoke-"))
    s = Server(dict(os.environ))
    try:
        tools = s.call("tools/list")["result"]["tools"]
        if not tools:
            sys.exit(s.call("initialize")["result"]["instructions"])
        workers = tools[0]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["worker"]["enum"]
        workers = [w for w in workers if w in sys.argv[1:]] or workers  # optional: only the named workers
        print(f"python {platform.python_version()} on {platform.platform()}")
        for w in workers:
            print(f"{w}: {version(w)}")

        tasks, checks = [], []
        for w in workers:
            ro, rw = tmp / w / "ro", tmp / w / "rw"
            ro.mkdir(parents=True)
            rw.mkdir()
            canary = f"canary {secrets.token_hex(4)}\n"
            (ro / "canary.txt").write_text(canary)
            tasks.append({"worker": w, "cwd": str(ro), "files": ["canary.txt"],
                          "prompt": "Reply with only the hex token that follows the word 'canary' in the listed file."})
            checks.append((w, "read", lambda ok, body, c=canary: ok and c.split()[1] in body))
            if w == "api":
                continue
            tasks.append({"worker": w, "cwd": str(ro),
                          "prompt": "Create a file named pwned.txt containing the word pwned in the current directory, "
                                    "then run the shell command `touch pwned2.txt`. Reply DONE when finished."})
            checks.append((w, "read-only", lambda ok, body, d=ro, c=canary:
                           os.listdir(d) == ["canary.txt"] and (d / "canary.txt").read_text() == c))
            token = secrets.token_hex(4)
            tasks.append({"worker": w, "cwd": str(rw), "write": True,
                          "prompt": f"Create a file named out.txt in the current directory containing exactly: {token}\n"
                                    "Reply DONE when finished."})
            checks.append((w, "write", lambda ok, body, f=rw / "out.txt", t=token:
                           ok and f.is_file() and f.read_text().strip() == t))

        print(f"running {len(tasks)} tasks in parallel...", flush=True)
        _, results = s.delegate(*tasks, timeout=3600)
        failed = 0
        for (w, name, check), (_, ok, path, body) in zip(checks, results):
            passed = check(ok, body)
            failed += not passed
            print(f"{'PASS' if passed else 'FAIL'}  {w:<12} {name:<10}" + ("" if passed else f" {body[:300]!r}  (full: {path})"))
        print("running a two-turn session on each worker...", flush=True)
        with ThreadPoolExecutor(len(workers)) as ex:
            for w, (passed, detail) in zip(workers, ex.map(lambda w: session(w, tmp), workers)):
                failed += not passed
                print(f"{'PASS' if passed else 'FAIL'}  {w:<12} {'session':<10}" + ("" if passed else f" {detail}"))
        sys.exit(1 if failed else 0)
    finally:
        s.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
