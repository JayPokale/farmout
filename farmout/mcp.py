"""The MCP server: newline-delimited JSON-RPC over stdio. `delegate` runs one-shot tasks in parallel; `send`,
`sessions` and `pipeline` give a head model saved conversations with workers and multi-stage pipelines."""
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import VERSION, engine, flows, pipeline, proc, sessions
from .config import OUT_DIR
from .workers import ENABLED

BUDGET = 60_000  # chars of worker output returned per call; full text always stays on disk
INTRO = """\
farmout: offload self-contained subtasks to other model subscriptions with the `delegate` tool, so this session's quota goes to planning, judgment and integration.

Workers:
"""
RULES = """\
- Keep for yourself: architecture, root-cause debugging, anything needing this conversation's context, final integration, and tasks so small that writing the brief costs more than doing them.

Rules:
- Workers see only their brief. Each prompt must stand alone: goal, context, constraints, exact output format, "be concise". Pass file paths in files, not pasted content.
- Read-only by default. write:true allows edits inside cwd; parallel writers on one repo need isolate:true (a private git worktree each, returned as a patch).
- Put independent subtasks in one delegate call; they run in parallel. background:true returns at once so you can keep working; read the result files when they appear.
- Each result is saved to the file named in its header. To chain workers, pass that path in a later task's files instead of reading it yourself.
- For a back-and-forth with one worker, use `send` with a session name: the worker remembers earlier turns. `pipeline` starts multi-stage runs (review, fix, verify) in the background.
- Verify worker output before relying on it; cheaper models fail quietly. On FAILED, retry another worker or do it yourself."""

if ENABLED:
    INSTRUCTIONS = INTRO + "".join(f"- {name}: {w.hint}\n" for name, w in ENABLED.items()) + RULES
else:
    INSTRUCTIONS = ("farmout: no workers available. Install `agy`, `codex` or `copilot`, or set FARMOUT_CLAUDE_DIR "
                    "or FARMOUT_API_BASE + FARMOUT_API_MODEL, then restart. `python3 server.py doctor` explains.")

FILES = {"type": "array", "items": {"type": "string"}, "description": "Paths to read: PDFs, code, earlier result files. Relative paths resolve against cwd."}
CWD = {"type": "string", "description": "Absolute working directory. Default: where the head was launched."}
TOOLS = [
    {"name": "delegate",
     "description": "Run self-contained subtasks on other models in parallel (routing guide: farmout server instructions). "
                    "Returns each result, capped, under a header naming the file with its full output.",
     "inputSchema": {"type": "object", "required": ["tasks"], "properties": {"tasks": {
         "type": "array", "minItems": 1, "items": {"type": "object", "required": ["worker", "prompt"], "properties": {
             "worker": {"type": "string", "enum": list(ENABLED)},
             "prompt": {"type": "string", "description": "Standalone brief: goal, context, constraints, exact output format. The worker sees nothing else."},
             "files": FILES,
             "cwd": CWD,
             "write": {"type": "boolean", "description": "Allow edits in cwd; agy, claude and copilot also in the dirs of listed files outside it. Default false."},
             "model": {"type": "string", "description": "Override the worker's default model."},
             "effort": {"type": "string", "description": "Reasoning effort, e.g. low, medium, high (passed to the CLI)."},
             "isolate": {"type": "boolean", "description": "Write in a private git worktree; the result names a patch. Implies write."},
         }}}, "background": {"type": "boolean", "description": "Return at once; result files appear as tasks end, then `done`."}}}},
    {"name": "send",
     "description": "Send a message to a named session: a saved conversation with one worker, which remembers the "
                    "earlier turns. Give `worker` to start a new session. Returns the reply.",
     "inputSchema": {"type": "object", "required": ["session", "message"], "properties": {
         "session": {"type": "string", "description": "Session name: letters, digits, '.', '_' and '-'."},
         "message": {"type": "string", "description": "The next message. For a new session, a standalone brief."},
         "worker": {"type": "string", "enum": list(ENABLED), "description": "Worker for a new session."},
         "files": FILES, "cwd": CWD,
         "write": {"type": "boolean", "description": "New session only: allow edits in cwd."},
         "isolate": {"type": "boolean", "description": "New session only: work in a private git worktree (a patch)."},
         "model": {"type": "string", "description": "New session only: the worker's model."},
         "background": {"type": "boolean", "description": "Queue the message and return at once; read the reply with `sessions`."}}}},
    {"name": "sessions",
     "description": "List sessions, or show one session's turns with their output files.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "A session to show in full."}}}},
    {"name": "pipeline",
     "description": "Multi-stage pipelines that run in the background (built-in: fix = review, fix what models agree "
                    "on, review the fix; spec = requirements, design, parallel build, check). action: list, run, "
                    "status, approve, reject, resume or stop.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "enum": ["list", "run", "status", "approve", "reject", "resume", "stop"]},
         "name": {"type": "string", "description": "run: the pipeline's name or file."},
         "run": {"type": "string", "description": "status/approve/reject/resume/stop: the run id."},
         "args": {"type": "string", "description": "run: free text for {args}, e.g. the goal for spec."},
         "set": {"type": "object", "additionalProperties": {"type": "string"}, "description": "run: parameter values."},
         "cwd": CWD}}},
]

out_lock = threading.Lock()
waiting = {}  # request id -> session name, so a cancel stops that session's turn


def task(i, t, key, outdir):
    t0, w = time.time(), t.get("worker")
    safe = w if isinstance(w, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", w) else "unknown"  # it names files
    where = {"label": f"{outdir.name}/{i}-{safe}", "branch": f"farmout/{outdir.name}-{i}"} if t.get("isolate") else None
    res = engine.execute(t, key, where)
    text = res["text"]
    if res["notes"]:
        text = "[farmout] " + "; ".join(res["notes"]) + "\n\n" + text
    path, part = outdir / f"{i}-{safe}.md", outdir / f"{i}-{safe}.part"
    part.write_text(text)
    part.replace(path)  # appears complete: background readers poll for it
    return f"## [{i}] {w} {'ok' if res['ok'] else 'FAILED'} {time.time() - t0:.0f}s -> {path}", text, res["ok"]


def run_all(tasks, key, outdir, done=None):
    try:
        with ThreadPoolExecutor(min(8, len(tasks))) as ex:
            return list(ex.map(lambda a: task(*a, key, outdir), enumerate(tasks, 1)))
    finally:
        if done:  # even after an error, so a poller waiting for it never hangs
            done.touch()


def capped(text, cap=BUDGET):
    return text[:cap] + ("\n...[truncated; full output in the file above]" if len(text) > cap else "")


def delegate(args, rid):
    tasks = args.get("tasks") or []
    if not tasks:
        raise ValueError("tasks is empty")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outdir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=OUT_DIR))
    if args.get("background"):  # own key: handle() forgets rid on reply, shutdown must still stop these
        threading.Thread(target=run_all, args=(tasks, ("background", rid), outdir, outdir / "done"), daemon=True).start()
        files = "\n".join(f"- {outdir}/{i}-{t.get('worker')}.md" for i, t in enumerate(tasks, 1))
        return (f"Started {len(tasks)} task(s) in the background. Each file appears when its task ends, and "
                f"{outdir}/done when all have. To be told, run in the background: "
                f"until [ -e {outdir}/done ]; do sleep 5; done\n{files}"), False
    results = run_all(tasks, rid, outdir)
    cap = BUDGET // len(tasks)
    text = "\n\n".join(f"{head}\n{capped(body, cap)}" for head, body, _ in results)
    return text, not any(ok for *_, ok in results)


def send_tool(a, rid):
    name = a.get("session") or ""
    try:
        meta = sessions.load(name)
    except KeyError:
        if not a.get("worker"):
            raise ValueError(f"no session named {name!r}; give `worker` to start one") from None
        meta = sessions.create(a["worker"], a.get("cwd") or os.getcwd(), name=name, write=a.get("write"),
                               isolate=a.get("isolate"), model=a.get("model"))
    msg = sessions.post(name, a["message"], a.get("files") or [], cwd=a.get("cwd") or meta["cwd"])
    sessions.kick(name)
    if a.get("background"):
        return f"Queued message {msg['id']} for session {name}; read the reply later with the sessions tool.", False
    waiting[rid] = name
    try:
        rec = sessions.wait_msg(name, msg["id"])
    finally:
        waiting.pop(rid, None)
    head = f"## {name} turn {rec['n']} ({rec.get('worker')}) {'ok' if rec['status'] == 'ok' else rec['status'].upper()} {rec.get('secs', '?')}s -> {rec.get('output')}"
    return f"{head}\n{capped(flows.text(rec))}", rec["status"] != "ok"


def sessions_tool(a, rid):
    if a.get("name"):
        meta = sessions.load(a["name"])
        lines = [json.dumps(sessions.summary(meta))]
        for r in sessions.turns(a["name"]):
            first = r["prompt"].strip().splitlines()[0][:100] if r["prompt"].strip() else ""
            lines.append(f"- turn {r['n']} {r['status']} {r.get('secs', '?')}s: {first} -> {r.get('output')}")
        return "\n".join(lines), False
    rows = [sessions.summary(sessions.load(n)) for n in sessions.names()][-50:]
    return "\n".join(f"{r['name']}  {r['worker']}  {r['state']}  turns={r['turns']}  {r['cwd']}" for r in rows) or "no sessions", False


def pipeline_tool(a, rid):
    act, cwd = a.get("action"), a.get("cwd") or os.getcwd()
    if act == "list":
        return "\n".join(f"{n}: {d} ({src})" for n, d, src in pipeline.available(cwd)), False
    if act == "run":
        state = pipeline.start(pipeline.load(a.get("name") or "", cwd), cwd, a.get("args") or "", a.get("set") or {})
        pipeline.background(state["id"])
        return f"Started pipeline run {state['id']} in the background. Check it with action=status, run={state['id']}.", False
    run_id = a.get("run") or ""
    if act in ("approve", "reject"):
        state = pipeline.approve(run_id, reject=act == "reject")
        pipeline.background(run_id)
        return f"{act}d; {run_id} continues in the background.", False
    if act == "resume":
        pipeline.reset(pipeline.load_run(run_id))
        pipeline.background(run_id)
        return f"Resumed {run_id} in the background.", False
    if act == "stop":
        return ("Stopped." if pipeline.stop(run_id) else f"{run_id} wasn't running."), False
    if act == "status":
        return pipeline.describe(pipeline.load_run(run_id)), False
    raise ValueError(f"unknown action {act!r}")


TOOL_FNS = {"delegate": delegate, "send": send_tool, "sessions": sessions_tool, "pipeline": pipeline_tool}


def reply(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def handle(msg):
    rid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
    try:
        fn = TOOL_FNS.get(params.get("name")) if method == "tools/call" and ENABLED else None
        if method == "initialize":
            res = {"result": {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "farmout", "version": VERSION},
                              "instructions": INSTRUCTIONS}}
        elif method == "tools/list":
            res = {"result": {"tools": TOOLS if ENABLED else []}}
        elif fn:
            text, is_error = fn(params.get("arguments") or {}, rid)
            res = {"result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
        elif method == "ping":
            res = {"result": {}}
        else:
            res = {"error": {"code": -32601, "message": f"unsupported: {method} {params.get('name', '')}".strip()}}
    except Exception as e:
        res = {"error": {"code": -32603, "message": f"{type(e).__name__}: {e.args[0] if isinstance(e, KeyError) and e.args else e}"}}
    with proc.lock:
        proc.running.pop(rid, None)
        if rid in proc.cancelled:
            return
    reply({"jsonrpc": "2.0", "id": rid, **res})


def main():
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # SIGTERM raises KeyboardInterrupt, like Ctrl-C
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and "method" in msg:
                threading.Thread(target=handle, args=(msg,), daemon=True).start()
            elif msg.get("method") == "notifications/cancelled":
                rid = (msg.get("params") or {}).get("requestId")
                proc.cancel(rid)
                if rid in waiting:
                    sessions.stop(waiting[rid])
    except KeyboardInterrupt:
        pass
    proc.kill_all()  # EOF or SIGTERM: the client went away, so stop every one-shot worker still burning quota
    os._exit(0)  # don't wait for worker threads still blocked in HTTP calls
