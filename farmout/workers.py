"""The workers: one function per CLI or API. Each takes a task dict and a process key and returns
{"text", "native_id", "usage"}, where native_id is the CLI's own session id, used to resume the conversation.

Task keys: prompt, files, cwd, write, model, effort, web (False: no web tools), timeout; for sessions also
persist (keep the CLI's session), session_id (a new id to create), resume (an id to continue), fork (claude).
"""
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

from .proc import TIMEOUT, run

CLAUDE_DIR = os.path.expanduser(os.environ.get("FARMOUT_CLAUDE_DIR", ""))
API_BASE, API_MODEL, API_KEY = (os.environ.get(f"FARMOUT_API_{k}") for k in ("BASE", "MODEL", "KEY"))
AGY_ANSWER = ("\n\nReply with the complete answer itself in this message. "
              "Do not write a plan, create files or ask for approval.")


def opt(flag, value):
    return [flag, value] if value else []


def prep(t):
    """Resolve cwd and files; directories outside cwd must be granted to the worker explicitly."""
    cwd = os.path.abspath(os.path.expanduser(t.get("cwd") or os.getcwd()))
    files = [os.path.abspath(os.path.join(cwd, os.path.expanduser(f))) for f in t.get("files") or []]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    extra = sorted({f if os.path.isdir(f) else os.path.dirname(f) for f in files} - {cwd})
    brief = t["prompt"] + ("\n\nFiles:\n" + "\n".join(f"- {f}" for f in files) if files else "")
    return cwd, files, [x for d in extra for x in ("--add-dir", d)], brief


def parse_json(out):
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def antigravity(t, key):
    cwd, _, dirs, brief = prep(t)
    if not t.get("write"):  # plan mode otherwise sometimes returns a plan stub and waits for an approval that never comes
        brief += AGY_ANSWER
    cmd = ["agy", "-p", brief, "--output-format", "json"]
    # Plan mode answers questions but executes nothing, even under toolPermission=always-proceed,
    # where print mode would otherwise run shell commands unasked.
    cmd += ["--mode", "accept-edits", "--sandbox"] if t.get("write") else ["--mode", "plan"]
    cmd += dirs + opt("--model", t.get("model")) + opt("--effort", t.get("effort")) + opt("--conversation", t.get("resume"))
    res = parse_json(run(cmd, key, cwd, timeout=t.get("timeout")))
    if res.get("status") != "SUCCESS":
        raise RuntimeError(json.dumps(res)[:1500])
    return {"text": res.get("response") or "", "native_id": res.get("conversation_id") or t.get("resume"),
            "usage": res.get("usage")}


def codex(t, key):
    cwd, _, _, brief = prep(t)
    mode = "workspace-write" if t.get("write") else "read-only"
    with tempfile.TemporaryDirectory() as tmp:
        last = os.path.join(tmp, "last.md")
        if t.get("resume"):  # `exec resume` takes no -s or -C: the sandbox comes from config, cwd from the process
            cmd = ["codex", "exec", "resume", "--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{mode}"', "-o", last]
        else:
            cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--color", "never", "-s", mode, "-C", cwd, "-o", last]
            cmd += [] if t.get("persist") else ["--ephemeral"]
        cmd += opt("-m", t.get("model"))
        cmd += ["-c", f'model_reasoning_effort="{t["effort"]}"'] if t.get("effort") else []
        cmd += [] if t.get("web", True) else ["-c", 'web_search="disabled"']  # in case your config turns it on
        cmd += [t["resume"]] if t.get("resume") else []
        out = run(cmd + ["-"], key, cwd, stdin=brief, timeout=t.get("timeout"))
        events = [parse_json(line) for line in out.splitlines() if line.startswith("{")]
        tid = next((e["thread_id"] for e in events if e.get("type") == "thread.started" and e.get("thread_id")), None)
        usage = next((e.get("usage") for e in reversed(events) if e.get("type") == "turn.completed"), None)
        return {"text": Path(last).read_text() if os.path.exists(last) else "",
                "native_id": tid or t.get("resume"), "usage": usage}


def claude(t, key):
    cwd, _, dirs, brief = prep(t)
    # Drop the head session's CLAUDE*/ANTHROPIC* vars (session ids, effort, API key) so the
    # worker is an independent session on the other account's subscription login.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env["CLAUDE_CONFIG_DIR"] = CLAUDE_DIR
    cmd = ["claude", "-p", "--output-format", "json", "--model", t.get("model") or "sonnet"]
    if t.get("resume"):
        cmd += ["--resume", t["resume"]] + (["--fork-session", "--session-id", t["session_id"]] if t.get("fork") else [])
    elif t.get("session_id"):
        cmd += ["--session-id", t["session_id"]]
    else:
        cmd += ["--no-session-persistence"]
    web = t.get("web", True)
    if t.get("write"):
        cmd += ["--permission-mode", "acceptEdits"]
    else:
        cmd += ["--tools", "Read,Grep,Glob" + (",WebSearch" if web else "")]
    # Headless, nobody can answer a permission prompt, so a tool that asks is simply denied: pre-approve search.
    cmd += ["--allowedTools", "WebSearch"] if web else []
    cmd += dirs + opt("--effort", t.get("effort"))
    out = run(cmd, key, cwd, stdin=brief, env=env, timeout=t.get("timeout"))
    res = parse_json(out)
    if not res:
        return {"text": out, "native_id": t.get("session_id") or t.get("resume"), "usage": None}
    if res.get("is_error"):
        raise RuntimeError(f"claude: {str(res.get('result') or res.get('subtype') or out)[-1500:]}")
    return {"text": res.get("result") or "", "native_id": res.get("session_id") or t.get("session_id") or t.get("resume"),
            "usage": res.get("usage")}


def copilot(t, key):
    cwd, _, dirs, brief = prep(t)
    # --allow-all-tools (required headless) would also auto-approve the user's MCP servers, so the
    # model only gets an allowlist: read/search/fetch, plus file edits under write. Never a shell.
    tools = ["view", "rg", "glob"] + (["web_fetch"] if t.get("web", True) else [])
    tools += ["apply_patch", "edit", "create"] if t.get("write") else []
    sid = t.get("resume") or t.get("session_id")
    cmd = ["copilot", "-p", brief, "-s", "--no-color", "--allow-all-tools", "--available-tools", *tools]
    cmd += dirs + opt("--model", t.get("model")) + opt("--reasoning-effort", t.get("effort")) + opt("--session-id", sid)
    return {"text": run(cmd, key, cwd, timeout=t.get("timeout")), "native_id": sid, "usage": None}


def api(t, key):
    """No tools on this worker, so file contents go inline. A session's history comes in t["history"]."""
    cwd, files, _, _ = prep(t)
    parts = [t["prompt"]]
    for f in files:
        text = (run(["pdftotext", "-layout", f, "-"], key, cwd) if f.lower().endswith(".pdf")
                else Path(f).read_text(errors="replace"))
        parts.append(f'<file path="{f}">\n{text}\n</file>')
    messages = list(t.get("history") or []) + [{"role": "user", "content": "\n\n".join(parts)}]
    body = {"model": t.get("model") or API_MODEL, "messages": messages}
    if t.get("effort"):
        body["reasoning_effort"] = t["effort"]
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})}
    req = urllib.request.Request(API_BASE.rstrip("/") + "/chat/completions", json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=t.get("timeout") or TIMEOUT) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1500]}") from None
    text = re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"]["content"] or "", flags=re.S)
    return {"text": text, "native_id": None, "usage": data.get("usage"),
            "messages": messages + [{"role": "assistant", "content": text}]}


Worker = namedtuple("Worker", "fn ready web_off hint")
WORKERS = {  # web_off: can farmout turn its web access off when a repo's settings forbid it?
    "antigravity": Worker(antigravity, shutil.which("agy"), False,
                          "Antigravity CLI (`agy`) on your Google account. Gemini models; reads PDFs/images natively, "
                          "large context, web search. For long PDFs/papers/docs, many-file reading, summarizing big "
                          "inputs, current-info research. model accepts any id from `agy models`."),
    "codex": Worker(codex, shutil.which("codex"), True,
                    "Codex CLI on your ChatGPT account, sandboxed shell. For well-specified coding: implement a "
                    "function, write/fix tests, focused refactor, code review."),
    "claude": Worker(claude, CLAUDE_DIR and shutil.which("claude"), True,
                     "a second Claude Code account (separate quota), Sonnet by default (model: haiku|sonnet|opus). "
                     "For general mid-complexity work."),
    "copilot": Worker(copilot, shutil.which("copilot"), True,
                      "GitHub Copilot CLI on your Copilot plan (spends its AI credits); model accepts any id Copilot "
                      "offers, across vendors. For code review, focused coding questions, second opinions. No shell."),
    "api": Worker(api, API_BASE and API_MODEL, True,
                  f"{API_MODEL} via an OpenAI-compatible API; no tools, listed files are inlined as text (PDFs via "
                  "pdftotext). For bulk/simple work: extract, classify, reformat, translate, short summaries, boilerplate."),
}
ENABLED = {name: w for name, w in WORKERS.items() if w.ready}
SETUP = {  # name: (executable, how to enable), for `doctor`
    "antigravity": ("agy", "install the Antigravity CLI (`agy`) and log in"),
    "codex": ("codex", "install the Codex CLI and run `codex login`"),
    "claude": ("claude", "log in a second account once (`CLAUDE_CONFIG_DIR=<dir> claude`), then set FARMOUT_CLAUDE_DIR=<dir>"),
    "copilot": ("copilot", "install the GitHub Copilot CLI and run `copilot login`"),
    "api": (None, "set FARMOUT_API_BASE and FARMOUT_API_MODEL (and FARMOUT_API_KEY if needed)"),
}

LIMIT = re.compile(r"rate[ _-]?limit|usage limit|quota|too many requests|\b429\b|resource[ _-]?exhausted|"
                   r"limit (?:reached|exceeded)|out of credits|credits? (?:exhausted|exceeded)|try again (?:in|at|later)", re.I)


def limited_for(error):
    """Seconds to leave a worker alone if its error says the subscription hit a limit, else None."""
    if not LIMIT.search(error):
        return None
    m = re.search(r"try again in (\d+)\s*(second|minute|hour|day)", error, re.I)
    return int(m.group(1)) * {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[m.group(2).lower()] if m else 900
