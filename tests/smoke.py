"""Release smoke test: drive the real worker CLIs through server.py on your own logins.

Spends a little quota on every enabled worker. Export the same MODELMUX_* variables as your MCP
registration first, e.g.  MODELMUX_CLAUDE_DIR=~/.claude-2 python3 tests/smoke.py [worker ...]
Each worker gets these tasks, all in one parallel delegate call:
  read       it can read a file it is given
  read-only  without write:true, told to create a file and run `touch`, it changes nothing
  write      with write:true, it can create a file in cwd (CLI workers only)
Exits 1 if any check fails.
"""
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from test_server import Server

VERSION = {"antigravity": ["agy", "--version"], "codex": ["codex", "--version"], "claude": ["claude", "--version"],
           "copilot": ["copilot", "--version"]}


def version(worker):
    if worker == "api":
        return f"{os.environ['MODELMUX_API_MODEL']} at {os.environ['MODELMUX_API_BASE']}"
    out = subprocess.run(VERSION[worker], capture_output=True, text=True)
    return ((out.stdout or out.stderr).strip().splitlines() or ["?"])[0]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="modelmux-smoke-"))
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
        sys.exit(1 if failed else 0)
    finally:
        s.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
