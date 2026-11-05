"""Stand-in for agy, codex, claude and pdftotext in the offline tests.

test_server.py installs a copy under each CLI's name, with a shebang for the current Python.
Each run is logged to $FAKE_LOG/<pid>.json; then the first line of the brief says what to do:
  say <text>      reply <text>
  big <n>         reply n 'x' characters
  fail <message>  print <message> to stderr and exit 1
  sleep <tag>     start a grandchild, write both pids to $FAKE_LOG/<tag>.pids, sleep until killed
  write <f> <text> write <text> to file <f> in cwd
  meet <dir> <n>  check in to <dir>, wait until n workers have (fail after 10s), reply "met"
  agyerror        agy reports a non-SUCCESS status
"""
import json
import os
import subprocess
import sys
import time

name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
if name == "pdftotext":  # pdftotext -layout <file> -
    print(f"PDF TEXT OF {args[-2]}")
    sys.exit()
if args == ["--version"]:
    print(f"{name} 9.9.9")
    sys.exit()

log = os.environ["FAKE_LOG"]
brief = args[args.index("-p") + 1] if name in ("agy", "copilot") else sys.stdin.read()
with open(os.path.join(log, f"{os.getpid()}.json"), "w") as f:
    json.dump({"name": name, "argv": args, "cwd": os.getcwd(), "brief": brief,
               "env": {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE", "ANTHROPIC"))}}, f)

cmd, _, reply = brief.split("\n")[0].partition(" ")
if cmd == "big":
    reply = "x" * int(reply)
elif cmd == "fail":
    sys.exit(reply)
elif cmd == "sleep":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pids = os.path.join(log, f"{reply}.pids")
    with open(pids + ".tmp", "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    os.replace(pids + ".tmp", pids)
    time.sleep(120)
elif cmd == "write":
    fname, _, content = reply.partition(" ")
    with open(fname, "w") as f:
        f.write(content)
    reply = f"wrote {fname}"
elif cmd == "meet":
    room, n = reply.split()
    open(os.path.join(room, str(os.getpid())), "w").close()
    deadline = time.time() + 10
    while len(os.listdir(room)) < int(n):
        if time.time() > deadline:
            sys.exit("nobody else came: tasks did not run in parallel")
        time.sleep(0.05)
    reply = "met"

if name == "agy":
    print(json.dumps({"status": "ERROR" if cmd == "agyerror" else "SUCCESS", "response": reply}))
elif name == "codex":  # the final message goes to the -o file; stdout is progress noise
    with open(args[args.index("-o") + 1], "w") as f:
        f.write(reply)
    print("codex: working...")
else:
    print(reply)
