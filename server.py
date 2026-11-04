#!/usr/bin/env python3
"""modelmux: MCP server that lets a head model (e.g. Claude Code) hand self-contained subtasks to
your other AI subscriptions in parallel, through their official CLIs: Antigravity (`agy`),
Codex CLI, GitHub Copilot CLI, a second Claude Code account, plus any OpenAI-compatible endpoint.
Stdlib only, Python 3.9+, Linux/macOS. Speaks MCP over stdio (newline-delimited JSON-RPC 2.0).

Register:  claude mcp add -s user modelmux -- python3 /path/to/modelmux/server.py
Check:     python3 server.py doctor    (which workers are ready, how to enable the rest)
Env:       MODELMUX_CLAUDE_DIR   config dir of a second Claude Code account (enables `claude`)
           MODELMUX_API_BASE, MODELMUX_API_MODEL, MODELMUX_API_KEY
                                 OpenAI-compatible endpoint, e.g. http://localhost:11434/v1 (enables `api`)
           MODELMUX_TIMEOUT      seconds per task (default 1200)
Outputs:   ~/.cache/modelmux/<call>/<n>-<worker>.md
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "0.1.0"
TIMEOUT = int(os.environ.get("MODELMUX_TIMEOUT", 1200))
CLAUDE_DIR = os.path.expanduser(os.environ.get("MODELMUX_CLAUDE_DIR", ""))
API_BASE, API_MODEL, API_KEY = (os.environ.get(f"MODELMUX_API_{k}") for k in ("BASE", "MODEL", "KEY"))
OUT_DIR = Path("~/.cache/modelmux").expanduser()
BUDGET = 60_000  # chars of worker output returned per call; full text always stays on disk

INTRO = """\
modelmux: offload self-contained subtasks to other model subscriptions with the `delegate` tool, so this session's quota goes to planning, judgment and integration.

Workers:
"""
RULES = """\
- Keep for yourself: architecture, root-cause debugging, anything needing this conversation's context, final integration, and tasks so small that writing the brief costs more than doing them.

Rules:
- Workers see only their brief. Each prompt must stand alone: goal, context, constraints, exact output format, "be concise". Pass file paths in files, not pasted content.
- Read-only by default. write:true allows edits inside cwd; never give parallel tasks write access to overlapping files.
- Put independent subtasks in one delegate call; they run in parallel. background:true returns at once so you can keep working; read the result files when they appear.
- Each result is saved to the file named in its header. To chain workers, pass that path in a later task's files instead of reading it yourself.
- Verify worker output before relying on it; cheaper models fail quietly. On FAILED, retry another worker or do it yourself."""

running, cancelled = {}, set()  # MCP request id -> live worker processes; ids the client cancelled
lock, out_lock = threading.Lock(), threading.Lock()


def signal_group(p, sig=signal.SIGTERM):
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass


def run(cmd, rid, cwd, stdin=None, env=None):
    """Run a worker CLI in its own process group so timeouts and cancels kill its whole tree."""
    with lock:
        if rid in cancelled:
            raise RuntimeError("cancelled")
        p = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, start_new_session=True,
                             stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        running.setdefault(rid, set()).add(p)
    try:
        out, err = p.communicate(stdin, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        signal_group(p)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            signal_group(p, signal.SIGKILL)
        raise RuntimeError(f"timed out after {TIMEOUT}s") from None
    finally:
        with lock:
            running.get(rid, set()).discard(p)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} exited {p.returncode}: {(err or out).strip()[-1500:]}")
    return out


def prep(t):
    """Resolve cwd and files; directories outside cwd must be granted to the worker explicitly."""
    cwd = os.path.abspath(os.path.expanduser(t.get("cwd") or os.getcwd()))
    files = [os.path.abspath(os.path.join(cwd, os.path.expanduser(f))) for f in t.get("files") or []]
    if missing := [f for f in files if not os.path.exists(f)]:
        raise FileNotFoundError(", ".join(missing))
    extra = sorted({f if os.path.isdir(f) else os.path.dirname(f) for f in files} - {cwd})
    brief = t["prompt"] + ("\n\nFiles:\n" + "\n".join(f"- {f}" for f in files) if files else "")
    return cwd, files, extra, brief


def antigravity(t, rid):
    cwd, _, extra, brief = prep(t)
    cmd = ["agy", "-p", brief, "--output-format", "json"]
    # Plan mode answers questions but executes nothing, even under toolPermission=always-proceed,
    # where print mode would otherwise run shell commands unasked.
    cmd += ["--mode", "accept-edits", "--sandbox"] if t.get("write") else ["--mode", "plan"]
    cmd += [x for d in extra for x in ("--add-dir", d)]
    if t.get("model"):
        cmd += ["--model", t["model"]]
    res = json.loads(run(cmd, rid, cwd))
    if res.get("status") != "SUCCESS":
        raise RuntimeError(json.dumps(res)[:1500])
    return res["response"]


def codex(t, rid):
    cwd, _, _, brief = prep(t)
    with tempfile.TemporaryDirectory() as tmp:
        last = os.path.join(tmp, "last.md")
        cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never",
               "-s", "workspace-write" if t.get("write") else "read-only", "-C", cwd, "-o", last]
        if t.get("model"):
            cmd += ["-m", t["model"]]
        run(cmd + ["-"], rid, cwd, stdin=brief)
        return Path(last).read_text()


def claude(t, rid):
    cwd, _, extra, brief = prep(t)
    # Drop the head session's CLAUDE*/ANTHROPIC* vars (session ids, effort, API key) so the
    # worker is an independent session on the other account's subscription login.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env["CLAUDE_CONFIG_DIR"] = CLAUDE_DIR
    cmd = ["claude", "-p", "--model", t.get("model") or "sonnet", "--no-session-persistence"]
    cmd += ["--permission-mode", "acceptEdits"] if t.get("write") else ["--tools", "Read,Grep,Glob,WebSearch"]
    cmd += [x for d in extra for x in ("--add-dir", d)]
    return run(cmd, rid, cwd, stdin=brief, env=env)


def copilot(t, rid):
    cwd, _, extra, brief = prep(t)
    # --allow-all-tools (required headless) would also auto-approve the user's MCP servers, so the
    # model only gets an allowlist: read/search/fetch, plus file edits under write. Never a shell.
    tools = ["view", "rg", "glob", "web_fetch"] + (["apply_patch", "edit", "create"] if t.get("write") else [])
    cmd = ["copilot", "-p", brief, "-s", "--no-color", "--allow-all-tools", "--available-tools", *tools]
    cmd += [x for d in extra for x in ("--add-dir", d)]
    if t.get("model"):
        cmd += ["--model", t["model"]]
    return run(cmd, rid, cwd)


def api(t, rid):
    cwd, files, _, _ = prep(t)
    parts = [t["prompt"]]
    for f in files:  # no tools on this worker, so file contents go inline
        text = (run(["pdftotext", "-layout", f, "-"], rid, cwd) if f.lower().endswith(".pdf")
                else Path(f).read_text(errors="replace"))
        parts.append(f'<file path="{f}">\n{text}\n</file>')
    req = urllib.request.Request(
        API_BASE.rstrip("/") + "/chat/completions",
        json.dumps({"model": API_MODEL, "messages": [{"role": "user", "content": "\n\n".join(parts)}]}).encode(),
        {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            text = json.load(r)["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1500]}") from None
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S)


WORKERS = {  # name: (function, available?, routing hint shown to the head)
    "antigravity": (antigravity, shutil.which("agy"),
                    "Antigravity CLI (`agy`) on your Google account. Gemini models; reads PDFs/images natively, large "
                    "context, web search. For long PDFs/papers/docs, many-file reading, summarizing big inputs, "
                    "current-info research. model accepts any id from `agy models`."),
    "codex": (codex, shutil.which("codex"),
              "Codex CLI on your ChatGPT account, sandboxed shell. For well-specified coding: implement a function, "
              "write/fix tests, focused refactor, code review."),
    "claude": (claude, CLAUDE_DIR and shutil.which("claude"),
               "a second Claude Code account (separate quota), Sonnet by default (model: haiku|sonnet|opus). "
               "For general mid-complexity work."),
    "copilot": (copilot, shutil.which("copilot"),
                "GitHub Copilot CLI on your Copilot plan (spends its AI credits); model accepts any id Copilot "
                "offers, across vendors. For code review, focused coding questions, second opinions. No shell."),
    "api": (api, API_BASE and API_MODEL,
            f"{API_MODEL} via an OpenAI-compatible API; no tools, listed files are inlined as text (PDFs via "
            "pdftotext). For bulk/simple work: extract, classify, reformat, translate, short summaries, boilerplate."),
}
ENABLED = {name: w for name, w in WORKERS.items() if w[1]}
if ENABLED:
    INSTRUCTIONS = INTRO + "".join(f"- {name}: {w[2]}\n" for name, w in ENABLED.items()) + RULES
else:
    INSTRUCTIONS = ("modelmux: no workers available. Install `agy`, `codex` or `copilot`, or set MODELMUX_CLAUDE_DIR "
                    "or MODELMUX_API_BASE + MODELMUX_API_MODEL, then restart. `python3 server.py doctor` explains.")
SETUP = {  # name: (executable, how to enable), for `doctor`
    "antigravity": ("agy", "install the Antigravity CLI (`agy`) and log in"),
    "codex": ("codex", "install the Codex CLI and run `codex login`"),
    "claude": ("claude", "log in a second account once with `CLAUDE_CONFIG_DIR=<dir> claude`, "
                         "then set MODELMUX_CLAUDE_DIR=<dir>"),
    "copilot": ("copilot", "install the GitHub Copilot CLI and run `copilot login`"),
    "api": (None, "set MODELMUX_API_BASE and MODELMUX_API_MODEL (and MODELMUX_API_KEY if needed)"),
}

TOOL = {
    "name": "delegate",
    "description": "Run self-contained subtasks on other models in parallel (routing guide: modelmux server instructions). "
                   "Returns each result, capped, under a header naming the file with its full output.",
    "inputSchema": {"type": "object", "required": ["tasks"], "properties": {"tasks": {
        "type": "array", "minItems": 1, "items": {"type": "object", "required": ["worker", "prompt"], "properties": {
            "worker": {"type": "string", "enum": list(ENABLED)},
            "prompt": {"type": "string", "description": "Standalone brief: goal, context, constraints, exact output format. The worker sees nothing else."},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Paths to read: PDFs, code, earlier result files. Relative paths resolve against cwd."},
            "cwd": {"type": "string", "description": "Absolute working directory. Default: where the head was launched."},
            "write": {"type": "boolean", "description": "Allow edits inside cwd (all workers but api). Default false."},
            "model": {"type": "string", "description": "Override the worker's default model."},
        }}}, "background": {"type": "boolean", "description": "Return at once and keep working; each result file appears "
                                                             "when its task ends, then a `done` file. Default false."}}},
}


def task(i, t, rid, outdir):
    t0, w = time.time(), t.get("worker")
    try:
        if w not in ENABLED:
            raise ValueError(f"unknown or unavailable worker {w!r}; available: {', '.join(ENABLED) or 'none'}")
        text, ok = ENABLED[w][0](t, rid).strip(), True
    except Exception as e:
        text, ok = f"{type(e).__name__}: {e}", False
    path, part = outdir / f"{i}-{w}.md", outdir / f"{i}-{w}.part"
    part.write_text(text)
    part.replace(path)  # appears complete: background readers poll for it
    return f"## [{i}] {w} {'ok' if ok else 'FAILED'} {time.time() - t0:.0f}s -> {path}", text, ok


def run_all(tasks, key, outdir):
    with ThreadPoolExecutor(min(8, len(tasks))) as ex:
        return list(ex.map(lambda a: task(*a, key, outdir), enumerate(tasks, 1)))


def delegate(args, rid):
    tasks = args.get("tasks") or []
    if not tasks:
        raise ValueError("tasks is empty")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outdir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=OUT_DIR))
    if args.get("background"):  # own key: handle() forgets rid on reply, shutdown must still stop these
        threading.Thread(target=lambda: (run_all(tasks, ("background", rid), outdir), (outdir / "done").touch()),
                         daemon=True).start()
        files = "\n".join(f"- {outdir}/{i}-{t.get('worker')}.md" for i, t in enumerate(tasks, 1))
        return (f"Started {len(tasks)} task(s) in the background. Each file appears when its task ends, and "
                f"{outdir}/done when all have. To be told, run in the background: "
                f"until [ -e {outdir}/done ]; do sleep 5; done\n{files}"), False
    results = run_all(tasks, rid, outdir)
    cap = BUDGET // len(tasks)
    text = "\n\n".join(f"{head}\n{body[:cap]}" + ("\n...[truncated; full output in the file above]" if len(body) > cap else "")
                       for head, body, _ in results)
    return text, not any(ok for *_, ok in results)


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def handle(msg):
    rid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
    try:
        if method == "initialize":
            reply = {"result": {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": "modelmux", "version": VERSION},
                                "instructions": INSTRUCTIONS}}
        elif method == "tools/list":
            reply = {"result": {"tools": [TOOL] if ENABLED else []}}
        elif method == "tools/call" and params.get("name") == "delegate":
            text, is_error = delegate(params.get("arguments") or {}, rid)
            reply = {"result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
        elif method == "ping":
            reply = {"result": {}}
        else:
            reply = {"error": {"code": -32601, "message": f"unsupported: {method} {params.get('name', '')}".strip()}}
    except Exception as e:
        reply = {"error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    with lock:
        running.pop(rid, None)
        if rid in cancelled:
            return
    send({"jsonrpc": "2.0", "id": rid, **reply})


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
                with lock:
                    cancelled.add(rid)
                    procs = list(running.get(rid, ()))
                for p in procs:
                    signal_group(p)
    except KeyboardInterrupt:
        pass
    with lock:  # EOF or SIGTERM: the client went away, so stop every worker still burning quota
        procs = [p for ps in running.values() for p in ps]
    for p in procs:
        signal_group(p)
    os._exit(0)  # don't wait for worker threads still blocked in HTTP calls


def doctor():
    print(f"modelmux {VERSION} on Python {sys.version.split()[0]}; results go to {OUT_DIR}")
    for name, (_, ready, _) in WORKERS.items():
        exe, how = SETUP[name]
        detail = how
        if ready and exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
                detail = f"{shutil.which(exe)} {((out.stdout or out.stderr).strip().splitlines() or ['?'])[0]}"
            except Exception as e:
                detail = f"{shutil.which(exe)} (--version failed: {e})"
        elif ready:
            detail = f"{API_MODEL} at {API_BASE}"
        print(f"  {'ready  ' if ready else 'missing'}  {name:<12} {detail}")
    print("Logins are not checked here. Live check (spends a little quota): python3 tests/smoke.py")


if __name__ == "__main__":
    doctor() if sys.argv[1:] == ["doctor"] else main()
