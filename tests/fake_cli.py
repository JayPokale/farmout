"""Stand-in for agy, codex, claude, copilot, dsh and pdftotext in the offline tests.

The tests install a copy under each CLI's name, with a shebang for the current Python. Each run is logged to
$FAKE_LOG/<pid>.json. Sessions work like the real CLIs': claude and copilot take the id farmout gives them, codex and
agy report a new one, and every CLI resumes by id (see sid()). The first line of the brief says what to do:
  say <text>       reply <text>
  big <n>          reply n 'x' characters
  fail <message>   print <message> to stderr and exit 1
  limit            fail like a subscription at its usage limit
  limited <cli> <cmd> that CLI is at its limit; the others run <cmd> (just `say x`)
  nap <secs>       sleep that long, then reply "napped"
  sleep <tag>      start a grandchild, write both pids to $FAKE_LOG/<tag>.pids, sleep until killed
  write <f> <text> write <text> to file <f> in cwd
  commit <f> <text> the same, then git commit it
  meet <dir> <n>   check in to <dir>, wait until n workers have (fail after 10s), reply "met"
  lines a|b|c      reply "- a", "- b" and "- c" on three lines
  recall           reply with the first line of this session's previous brief, or "none"
farmout's review brief is answered from $FAKE_LOG/review-<cli>.txt (default: NO FINDINGS).
  agyerror         agy reports a non-SUCCESS status
"""
import json
import os
import subprocess
import sys
import time
import uuid

name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
if name == "pdftotext":  # pdftotext -layout <file> -
    print(f"PDF TEXT OF {args[-2]}")
    sys.exit()
if args == ["--version"]:
    print(f"{name} 9.9.9")
    sys.exit()


def flag(f):
    return args[args.index(f) + 1] if f in args else None


def sid():
    """The session this run continues or creates, if the CLI was told one."""
    if name == "claude":
        return flag("--session-id") or flag("--resume")
    if name == "copilot":
        return flag("--session-id")
    if name == "agy":
        return flag("--conversation")
    if name == "codex" and "resume" in args:
        return args[args.index("-") - 1]
    if name == "dsh":
        return flag("--session-id")
    return None


log = os.environ["FAKE_LOG"]
brief = args[args.index("-p") + 1] if name in ("agy", "copilot") else sys.stdin.read()
session = sid() or (str(uuid.uuid4()) if name in ("codex", "agy") else f"session-{uuid.uuid4()}" if name == "dsh" else None)
with open(os.path.join(log, f"{os.getpid()}.json"), "w") as f:
    json.dump({"name": name, "argv": args, "cwd": os.getcwd(), "brief": brief, "session": session,
               "env": {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE", "ANTHROPIC"))}}, f)

store = os.path.join(log, "store")
os.makedirs(store, exist_ok=True)
history_file = os.path.join(store, f"{session}.json") if session else None
source = os.path.join(store, f"{flag('--resume')}.json") if "--fork-session" in args else history_file  # claude fork
history = json.load(open(source)) if source and os.path.exists(source) else []

cmd, _, reply = brief.split("\n")[0].partition(" ")
if brief.startswith("Review a code change"):  # farmout's review brief: answer from $FAKE_LOG/review-<cli>.txt
    found = os.path.join(log, f"review-{name}.txt")
    reply = open(found).read() if os.path.exists(found) else "NO FINDINGS"
elif cmd == "big":
    reply = "x" * int(reply)
elif cmd == "fail":
    sys.exit(reply)
elif cmd == "limit" or (cmd == "limited" and reply.split()[0] == name):
    sys.exit("Error: you've hit your usage limit. Try again in 2 minutes.")
elif cmd == "limited":
    reply = reply.split(" ", 2)[2]  # limited <cli> say <text>
elif cmd == "halfway":  # halfway <cli> <file> <text>: that CLI edits a file, then hits its usage limit
    who, fname, content = reply.split(" ", 2)
    if who == name:
        with open(fname, "w") as f:
            f.write(content)
        sys.exit("Error: you've hit your usage limit. Try again in 2 minutes.")
    reply = "finished cleanly"
elif cmd == "nap":
    time.sleep(float(reply))
    reply = "napped"
elif cmd == "sleep":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pids = os.path.join(log, f"{reply}.pids")
    with open(pids + ".tmp", "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    os.replace(pids + ".tmp", pids)
    time.sleep(120)
elif cmd == "write":
    fname, _, content = reply.partition(" ")
    os.makedirs(os.path.dirname(fname) or ".", exist_ok=True)
    with open(fname, "w") as f:
        f.write(content)
    reply = f"wrote {fname}"
elif cmd == "commit":  # like write, then commit it, as some agents do
    fname, _, content = reply.partition(" ")
    with open(fname, "w") as f:
        f.write(content)
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.name=f", "-c", "user.email=f@localhost", "commit", "-qm", "worker"], check=True)
    reply = f"committed {fname}"
elif cmd == "meet":
    room, n = reply.split()
    open(os.path.join(room, str(os.getpid())), "w").close()
    deadline = time.time() + 10
    while len(os.listdir(room)) < int(n):
        if time.time() > deadline:
            sys.exit("nobody else came: tasks did not run in parallel")
        time.sleep(0.05)
    reply = "met"
elif cmd == "lines":
    reply = "\n".join(f"- {x}" for x in reply.split("|"))
elif cmd == "recall":
    reply = history[-1].split("\n")[0] if history else "none"

if history_file and "--no-session-persistence" not in args and "--ephemeral" not in args:
    with open(history_file, "w") as f:
        json.dump(history + [brief], f)

if name == "agy":
    print(json.dumps({"status": "ERROR" if cmd == "agyerror" else "SUCCESS", "response": reply,
                      "conversation_id": session, "usage": {"input_tokens": 10, "output_tokens": 5}}))
elif name == "codex":  # the final message goes to the -o file; stdout is progress events
    with open(args[args.index("-o") + 1], "w") as f:
        f.write(reply)
    print("codex: working...")
    if "--json" in args:
        print(json.dumps({"type": "thread.started", "thread_id": session}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}))
elif name == "dsh" and "--json" in args:  # NDJSON events: session first, final last
    print(json.dumps({"type": "session", "sessionId": session, "cwd": os.getcwd()}))
    print(json.dumps({"type": "text", "text": reply}))
    print(json.dumps({"type": "final", "sessionId": session, "reason": "completed", "text": reply}))
elif name == "claude" and flag("--output-format") == "json":
    new = flag("--session-id") if "--fork-session" in args or "--resume" not in args else flag("--resume")
    print(json.dumps({"type": "result", "is_error": False, "result": reply, "session_id": new or "no-session",
                      "usage": {"input_tokens": 4, "output_tokens": 2}}))
else:
    print(reply)
