"""The workers: one function per CLI or API. Each takes a task dict and a process key and returns
{"text", "native_id", "usage"}, where native_id is the CLI's own session id, used to resume the conversation.

Task keys: prompt, files, cwd, write, model, effort, web (False: no web tools), timeout; for sessions also
persist (keep the CLI's session), session_id (a new id to create), resume (an id to continue), fork (claude).
"""
import datetime
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zoneinfo
from collections import namedtuple
from pathlib import Path

from .proc import TIMEOUT, run

CLAUDE_DIR = os.path.expanduser(os.environ.get("FARMOUT_CLAUDE_DIR", ""))
# One Claude account? FARMOUT_CLAUDE_MAIN=1 makes your main login the claude worker (it spends that account's
# quota). Never inside Claude Code: there it's the head's own account.
CLAUDE_MAIN = not CLAUDE_DIR and os.environ.get("FARMOUT_CLAUDE_MAIN") == "1" and not os.environ.get("CLAUDECODE")
API_BASE, API_MODEL, API_KEY = (os.environ.get(f"FARMOUT_API_{k}") for k in ("BASE", "MODEL", "KEY"))
AGY_ANSWER = ("\n\nReply with the complete answer itself in this message. "
              "Do not write a plan, create files or ask for approval.")


def lane(text):
    """`codex` or `claude:opus` -> (worker, model or None); an api model may hold colons (`api:qwen3:32b`)."""
    worker, _, model = text.partition(":")
    return worker, model or None


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
    text = res.get("response") or ""
    if res.get("status") == "SUCCESS" and not text.strip() and not t.get("write"):  # nothing to show for the run
        denied = ", ".join(str(d.get("action")) for d in res.get("denied_actions") or [] if isinstance(d, dict))
        raise RuntimeError("agy returned an empty answer" + (f" (it wanted a tool headless mode denies: {denied})"
                                                             if denied else ""))
    if res.get("status") != "SUCCESS":
        if not text.strip() or t.get("write"):  # a write task that ended in ERROR may have edited only half
            raise RuntimeError(json.dumps(res)[:1500])
        # agy sometimes ends with ERROR after writing its whole answer: keep the answer, and say how it ended
        rest = {k: v for k, v in res.items() if k not in ("response", "conversation_id", "usage")}
        text += f"\n\n[farmout] agy ended with {json.dumps(rest)[:500]}"
    return {"text": text, "native_id": res.get("conversation_id") or t.get("resume"), "usage": res.get("usage")}


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
    # FARMOUT_CLAUDE_MAIN (never inside Claude Code): the login `claude` uses in your shell, its config dir included
    config = CLAUDE_DIR or os.environ.get("CLAUDE_CONFIG_DIR")
    if config:
        env["CLAUDE_CONFIG_DIR"] = config
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
    try:
        out = run(cmd, key, cwd, stdin=brief, env=env, timeout=t.get("timeout"))
    except RuntimeError as e:  # it exits 1 with its JSON on stdout ("Not logged in"): show the message, not the JSON
        said = re.search(r'"result":\s*("(?:[^"\\]|\\.)*")', str(e))
        if said:
            raise RuntimeError(f"claude: {json.loads(said.group(1))}"[:1500]) from None
        raise
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


def deepseek(t, key):
    """DeepSeek Harness (`dsh`), headless. Its own profile and DEEPSEEK_API_KEY pick the model, so `model` and
    `effort` don't apply. Read-only is the `read-only` permission preset; with approvals set to `never`, anything
    it would have asked about is refused rather than left waiting for an answer nobody can give."""
    cwd, _, dirs, brief = prep(t)
    mode = "workspace-write" if t.get("write") else "read-only"
    cmd = ["dsh", "headless", "--json", "--permission-mode", mode, "--approval-policy", "never"]
    cmd += dirs + opt("--session-id", t.get("resume")) + ["-"]
    out = run(cmd, key, cwd, stdin=brief, timeout=t.get("timeout"))
    events = [parse_json(line) for line in out.splitlines() if line.startswith("{")]
    final = next((e for e in reversed(events) if e.get("type") == "final"), None)
    if final is None:
        error = next((e.get("message") for e in events if e.get("type") == "error"), None)
        raise RuntimeError(f"dsh: {error or out.strip()[-1500:] or 'no answer'}")
    sid = final.get("sessionId") or next((e.get("sessionId") for e in events if e.get("type") == "session"), None)
    return {"text": final.get("text") or "", "native_id": sid or t.get("resume"), "usage": None}


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
    "claude": Worker(claude, (CLAUDE_DIR or CLAUDE_MAIN) and shutil.which("claude"), True,
                     "a second Claude Code account (separate quota), Sonnet by default (model: haiku|sonnet|opus). "
                     "For general mid-complexity work."),
    "copilot": Worker(copilot, shutil.which("copilot"), True,
                      "GitHub Copilot CLI on your Copilot plan (spends its AI credits); model accepts any id Copilot "
                      "offers, across vendors. For code review, focused coding questions, second opinions. No shell."),
    "deepseek": Worker(deepseek, shutil.which("dsh"), False,
                       "DeepSeek Harness (`dsh`) on your DeepSeek API key: DeepSeek V4 models, cheap per token, with "
                       "its own tools and web search. For bulk reading, summaries, routine coding and a further "
                       "opinion. Experimental: its model comes from dsh's own profile."),
    "api": Worker(api, API_BASE and API_MODEL, True,
                  f"{API_MODEL} via an OpenAI-compatible API; no tools, listed files are inlined as text (PDFs via "
                  "pdftotext). For bulk/simple work: extract, classify, reformat, translate, short summaries, boilerplate."),
}
ENABLED = {name: w for name, w in WORKERS.items() if w.ready}


def use_main_claude():
    """For `farmout bench`: with no second Claude account, rank your main login too. True if it now takes part."""
    global CLAUDE_MAIN
    if "claude" in ENABLED or os.environ.get("CLAUDECODE") or not shutil.which("claude"):
        return False
    os.environ["FARMOUT_CLAUDE_MAIN"] = "1"  # the session runners read it when they start
    CLAUDE_MAIN = True
    WORKERS["claude"] = WORKERS["claude"]._replace(ready=shutil.which("claude"))
    ENABLED["claude"] = WORKERS["claude"]
    return True


SETUP = {  # name: (executable, how to enable), for `doctor`
    "antigravity": ("agy", "install the Antigravity CLI (`agy`) and log in"),
    "codex": ("codex", "install the Codex CLI and run `codex login`"),
    "claude": ("claude", "log a second account in once (`CLAUDE_CONFIG_DIR=<dir> claude`), then set FARMOUT_CLAUDE_DIR=<dir>;"
                         " or FARMOUT_CLAUDE_MAIN=1 uses your main login (bench does that by itself)"),
    "copilot": ("copilot", "install the GitHub Copilot CLI and run `copilot login`"),
    "deepseek": ("dsh", "install DeepSeek Harness (`npm i -g @deepseek-ai/dsh`) and set DEEPSEEK_API_KEY"),
    "api": (None, "set FARMOUT_API_BASE and FARMOUT_API_MODEL (and FARMOUT_API_KEY if needed)"),
}

LIMIT = re.compile(r"rate[ _-]?limit|usage limit|quota|too many requests|\b429\b|resource[ _-]?exhausted|"
                   r"limit (?:reached|exceeded)|hit your (?:\w+ )?limit|out of credits|credits? (?:exhausted|exceeded)|"
                   r"allowance|try again (?:in|at|later)", re.I)
# Not logged in, a login that expired, or a subscription that ended: the worker can't run until you log in again.
LOGIN = re.compile(r"not logged in|log ?in (?:required|first|again)|please (?:run )?/?log ?in|sign ?in (?:required|first|"
                   r"again)|\b401\b|unauthori[sz]ed|no authentication|authentication (?:failed|required|error)|"
                   r"invalid (?:api[ _-]?key|credentials|(?:access |auth )?token)|(?:token|session|credentials?|login) "
                   r"(?:has )?expired|subscription (?:has )?(?:expired|ended|lapsed|inactive|is not active)|"
                   r"no active subscription|credit balance is too low", re.I)
UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*(w(?:eeks?)?|d(?:ays?)?|h(?:(?:ou)?rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)\b",
                  re.I)
WAIT = re.compile(r"(?:try again|retry|resets?|available again|wait)\s+(?:in|after)\s+"
                  r"((?:\d+(?:\.\d+)?\s*[a-z]+[,\s]*(?:and\s+)?)+)", re.I)
AT = re.compile(r"(?:resets?|try again|available again)\b(.{0,60})", re.I | re.S)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def reset_in(error, now=None):
    """Seconds until a limit resets, read from the error's wording, or None. Understands durations ("try again in
    4 days 2 hours", "retry in 32.5s"), a Unix time after a | ("usage limit reached|1763467200"), an ISO time, and a
    clock time with an optional weekday and time zone ("resets 3pm (Asia/Kolkata)", "try again at 3:04 PM")."""
    now = now or datetime.datetime.now().astimezone()
    m = WAIT.search(error)
    if m and SPAN.search(m.group(1)):
        return sum(float(n) * UNITS[u[0].lower()] for n, u in SPAN.findall(m.group(1)))
    m = re.search(r"\|(\d{10})\b", error)
    if m:
        return int(m.group(1)) - now.timestamp()
    m = AT.search(error)
    if not m:
        return None
    tail = m.group(1)
    iso = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?", tail)
    if iso:
        try:
            when = datetime.datetime.fromisoformat(iso.group(0).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (when if when.tzinfo else when.astimezone()).timestamp() - now.timestamp()
    clock = (re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?", tail, re.I)
             or re.search(r"\b(\d{1,2}):(\d{2})\b()", tail))
    if not clock:
        return None
    hour, minute = int(clock.group(1)) % 24, int(clock.group(2) or 0)
    if clock.group(3):
        hour = hour % 12 + (12 if clock.group(3).lower() == "p" else 0)
    zone = re.search(r"\(([A-Za-z_]+(?:/[A-Za-z_+-]+)+|UTC)\)", tail)
    if zone:
        try:
            now = now.astimezone(zoneinfo.ZoneInfo(zone.group(1)))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    day = re.search(r"\b(" + "|".join(DAYS) + r")[a-z]*\b", tail, re.I)
    if day:
        when += datetime.timedelta(days=(DAYS.index(day.group(1).lower()) - when.weekday()) % 7)
    while when <= now:
        when += datetime.timedelta(days=7 if day else 1)
    return (when - now).total_seconds()


def limited_for(error):
    """Seconds to leave a worker alone if its error says the subscription hit a limit, else None: until the reset
    time the error names (at least a minute, at most 8 days), otherwise 15 minutes."""
    if not LIMIT.search(error):
        return None
    secs = reset_in(error)
    return 900 if secs is None else round(min(max(secs, 60), 8 * 86400))


def logged_out(error):
    """True if the error says the worker isn't logged in, its login expired or its subscription ended."""
    return bool(LOGIN.search(error))
