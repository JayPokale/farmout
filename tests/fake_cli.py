"""Stand-in for agy, codex, claude, copilot, dsh and pdftotext in the offline tests.

The tests install a copy under each CLI's name, with a shebang for the current Python. Each run is logged to
$FAKE_LOG/<pid>.json. Sessions work like the real CLIs': claude and copilot take the id farmout gives them, codex and
agy report a new one, and every CLI resumes by id (see sid()). claude finds a conversation only from the folder it
began in, as older Claude Code did (newer versions find it by id from anywhere), so farmout's recovery is tested: a
resume that can't find its conversation gets it as a file. The first line of the brief says what to do:
  say <text>       reply <text>
  big <n>          reply n 'x' characters
  fail <message>   print <message> to stderr and exit 1
  limit            fail like a subscription at its usage limit
  limited <cli> <cmd> that CLI is at its limit; the others run <cmd> (just `say x`)
  loggedout <cli> <cmd> that CLI isn't logged in; the others run <cmd>
  busymodel <model> <cmd> a run on that model is at its limit; other models run <cmd>
  nap <secs>       sleep that long, then reply "napped"
  sleep <tag>      start a grandchild, write both pids to $FAKE_LOG/<tag>.pids, sleep until killed
  write <f> <text> write <text> to file <f> in cwd
  commit <f> <text> the same, then git commit it
  meet <dir> <n>   check in to <dir>, wait until n workers have (fail after 10s), reply "met"
  lines a|b|c      reply "- a", "- b" and "- c" on three lines
  recall           reply with the first line of this session's previous brief, or "none"
farmout's review brief is answered from $FAKE_LOG/review-<cli>-<model>.txt, else review-<cli>.txt (default: NO
FINDINGS; "EXIT <message>" fails the run).
  agyerror         agy reports a non-SUCCESS status
  agyempty         agy reports SUCCESS with an empty answer, as when headless mode denied a tool it wanted
  claudeout        claude prints its JSON report of a failed login and exits 1, as the real one does
With FAKE_EMPTY_LOGIN set to its CLAUDE_CONFIG_DIR, claude acts on an empty login folder: a resume finds no
conversation, anything else isn't logged in.
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
               "env": {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE", "ANTHROPIC", "FARMOUT_WORKER"))}}, f)

store = os.path.join(log, "store")
if name == "claude":  # ~/.claude/projects/<folder>/, searched only from that folder (older Claude Code)
    store = os.path.join(store, "".join(c if c.isalnum() else "-" for c in os.getcwd()))
os.makedirs(store, exist_ok=True)
history_file = os.path.join(store, f"{session}.json") if session else None
source = os.path.join(store, f"{flag('--resume')}.json") if "--fork-session" in args else history_file  # claude fork
if name == "claude" and os.environ.get("FAKE_EMPTY_LOGIN") and os.environ.get("CLAUDE_CONFIG_DIR") == os.environ["FAKE_EMPTY_LOGIN"]:
    # a login folder with nothing in it: no conversations to resume, and no login
    sys.exit(f"No conversation found with session ID: {flag('--resume')}" if flag("--resume") else
             "Not logged in · Please run /login")
if name == "claude" and flag("--resume") and not os.path.exists(source):
    sys.exit(f"No conversation found with session ID: {flag('--resume')}")
history = json.load(open(source)) if source and os.path.exists(source) else []

cmd, _, reply = brief.split("\n")[0].partition(" ")
if brief.startswith("Review a code change"):  # farmout's review brief: answer from $FAKE_LOG/review-<cli>.txt
    found = os.path.join(log, f"review-{name}-{flag('--model') or flag('-m')}.txt")  # or review-<cli>-<model>.txt
    found = found if os.path.exists(found) else os.path.join(log, f"review-{name}.txt")
    reply = open(found).read() if os.path.exists(found) else "NO FINDINGS"
    if reply.startswith("EXIT "):  # a reviewer that fails
        sys.exit(reply[5:])
elif cmd == "big":
    reply = "x" * int(reply)
elif cmd == "fail":
    sys.exit(reply)
elif cmd == "claudeout" and name == "claude":
    print(json.dumps({"type": "result", "is_error": True, "usage": {"input_tokens": 0}, "terminal_reason": "api_error",
                      "result": "Not logged in · Please run /login", "session_id": "x"}))
    sys.exit(1)
elif cmd == "limit" or (cmd == "limited" and reply.split()[0] == name):
    sys.exit("Error: you've hit your usage limit. Try again in 2 minutes.")
elif cmd == "limited":
    reply = reply.split(" ", 2)[2]  # limited <cli> say <text>
if cmd in ("loggedout", "busymodel"):  # a new chain: the inner command runs through the branches below
    kind, (who, cmd, reply) = cmd, (reply.split(" ", 2) + ["", ""])[:3]
    if who == (name if kind == "loggedout" else flag("--model") or flag("-m")):
        sys.exit("Not logged in · Please run /login" if kind == "loggedout"
                 else "Error: you've hit your usage limit. Try again in 2 minutes.")
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
    print(json.dumps({"status": "ERROR" if cmd == "agyerror" else "SUCCESS", "response": "" if cmd == "agyempty" else reply,
                      "conversation_id": session, "usage": {"input_tokens": 10, "output_tokens": 5},
                      **({"denied_actions": [{"action": "command"}]} if cmd == "agyempty" else {})}))
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
