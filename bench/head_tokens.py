"""Head-token benchmark: one task, Claude Code alone vs Claude Code + farmout.

Papers: runs `claude -p` twice on copies of the given PDFs: once with only Read/Glob (the head reads the
papers itself), once with only Glob plus the farmout delegate tool (Antigravity reads them).
Code: runs one coding task twice, each time in a fresh clone of a repository, with the same tools (read, edit,
and Bash only for the check command); the farmout run may also hand parts to the delegate tool. Afterwards the
check command runs in each clone, so a run that saved tokens by doing less shows as a failure.
Reports what the head spent, from --output-format json, and saves the answers for a quality check.
Spends real quota on the head account and on the workers.

Run:  python3 bench/head_tokens.py a.pdf b.pdf ...      (writes bench/results/<date>/head_tokens.*)
      python3 bench/head_tokens.py --code REPO --task "..." --check "python3 -m pytest -q"
                                                       (writes bench/results/<date>/head_tokens_code.*)
Env:  HEAD_CONFIG_DIR  Claude config dir for the head (default: your normal login)
      HEAD_MODEL       head model (default: sonnet)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK = ("For each PDF in the current directory, give: title; the core method in one sentence; datasets; the headline "
        "result with its number. At most 60 words per paper. Reply with the list only.")
MCP = {"mcpServers": {"farmout": {"command": sys.executable, "args": [str(ROOT / "server.py")]}}}
ARMS = {
    "alone": ([], "Read,Glob", ""),
    "farmout": (["--mcp-config", json.dumps(MCP), "--allowedTools", "mcp__farmout__delegate"], "Glob",
                 " Don't read the PDFs yourself: use the farmout delegate tool, one antigravity task per paper."),
}


CODE_NUDGE = (" Hand self-contained parts (reading long files, writing tests or boilerplate) to the farmout delegate "
              "tool, with write: true when the worker should edit; then check and integrate what comes back yourself.")


def code_arms(check):
    """Both coding runs get the same tools: read, edit, and Bash only for the check command."""
    allow = ["--permission-mode", "acceptEdits", "--allowedTools", f"Bash({check}:*)"]
    tools = "Read,Edit,Write,Glob,Grep,Bash"
    return {"alone": (allow, tools, ""),
            "farmout": (allow + ["--mcp-config", json.dumps(MCP), "--allowedTools", "mcp__farmout__delegate"], tools,
                        CODE_NUDGE)}


def run(arm, cwd, task=TASK, arms=None):
    extra, tools, nudge = (arms or ARMS)[arm]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    if os.environ.get("HEAD_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(os.environ["HEAD_CONFIG_DIR"])
    cmd = ["claude", "-p", task + nudge, "--model", os.environ.get("HEAD_MODEL", "sonnet"), "--output-format", "json",
           "--strict-mcp-config", "--no-session-persistence", "--tools", tools, *extra]
    t0 = time.time()
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1800)
    res = json.loads(out.stdout)
    u = res.get("usage", {})
    return {"arm": arm, "seconds": round(time.time() - t0), "turns": res.get("num_turns"),
            "cost_usd": res.get("total_cost_usd"), "input": u.get("input_tokens", 0),
            "cache_write": u.get("cache_creation_input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
            "output": u.get("output_tokens", 0), "answer": res.get("result", ""), "error": res.get("is_error")}


def table(rows):
    lines = ["| head | total tokens | input | cache write | cache read | output | cost | turns | time |" +
             (" check |" if "check" in rows[0] else ""),
             "|---|---|---|---|---|---|---|---|---|" + ("---|" if "check" in rows[0] else "")]
    for r in rows:
        total = r["input"] + r["cache_write"] + r["cache_read"] + r["output"]
        lines.append(f"| {r['arm']} | {total:,} | {r['input']:,} | {r['cache_write']:,} | {r['cache_read']:,} | "
                     f"{r['output']:,} | ${r['cost_usd'] or 0:.3f} | {r['turns']} | {r['seconds']}s |"
                     + (f" {'pass' if r['check'] else 'FAIL'} |" if "check" in r else ""))
    return lines


def code(repo, task, check):
    """The coding variant: each arm in a fresh clone, then the check command in that clone."""
    out = ROOT / "bench" / "results" / time.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    arms, rows = code_arms(check), []
    for arm in arms:
        cwd = Path(tempfile.mkdtemp(prefix=f"farmout-head-{arm}-")) / "repo"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(repo), str(cwd)], check=True)
        print(f"{arm}: running...", flush=True)
        row = run(arm, cwd, task, arms)
        test = subprocess.run(check, shell=True, cwd=cwd, capture_output=True, text=True, timeout=1800)
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)  # new files count too
        stat = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=cwd, capture_output=True, text=True).stdout.strip()
        rows.append({**row, "check": test.returncode == 0, "check_tail": (test.stdout + test.stderr)[-1500:],
                     "diffstat": stat.splitlines()[-1] if stat else "no changes"})
        shutil.rmtree(cwd.parent, ignore_errors=True)
    report = (f"Task in a clone of {repo}: {task}\nCheck: `{check}`\n\n" + "\n".join(table(rows)) + "\n\n"
              + "\n\n".join(f"### {r['arm']} ({r['diffstat']})\n\n{r['answer']}\n\n```\n{r['check_tail']}\n```"
                            for r in rows) + "\n")
    (out / "head_tokens_code.md").write_text(report)
    (out / "head_tokens_code.json").write_text(json.dumps(rows, indent=1))
    print(report)


def main():
    if sys.argv[1:2] == ["--code"]:
        args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
        if not {"--code", "--task", "--check"} <= set(args):
            sys.exit(__doc__)
        return code(Path(args["--code"]).resolve(), args["--task"], args["--check"])
    papers = [Path(p).resolve() for p in sys.argv[1:]]
    if not papers:
        sys.exit(__doc__)
    out = ROOT / "bench" / "results" / time.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for arm in ARMS:
        cwd = Path(tempfile.mkdtemp(prefix=f"farmout-head-{arm}-"))
        for p in papers:
            shutil.copy(p, cwd)
        print(f"{arm}: running...", flush=True)
        rows.append(run(arm, cwd))
        shutil.rmtree(cwd, ignore_errors=True)
    lines = table(rows)
    answers = "\n\n".join(f"### {r['arm']}\n\n{r['answer']}" for r in rows)
    report = (f"Task ({len(papers)} PDFs: {', '.join(p.name for p in papers)}): {TASK}\n\n" + "\n".join(lines)
              + "\n\n" + answers + "\n")
    (out / "head_tokens.md").write_text(report)
    (out / "head_tokens.json").write_text(json.dumps(rows, indent=1))
    print(report)


if __name__ == "__main__":
    main()
