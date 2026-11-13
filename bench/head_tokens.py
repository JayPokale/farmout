"""Head-token benchmark: one reading task, Claude Code alone vs Claude Code + farmout.

Runs `claude -p` twice on copies of the given PDFs: once with only Read/Glob (the head reads the
papers itself), once with only Glob plus the farmout delegate tool (Antigravity reads them).
Reports what the head spent, from --output-format json, and saves both answers for a quality check.
Spends real quota on the head account and on Antigravity.

Run:  python3 bench/head_tokens.py a.pdf b.pdf ...      (writes bench/results/<date>/head_tokens.*)
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


def run(arm, cwd):
    extra, tools, nudge = ARMS[arm]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    if os.environ.get("HEAD_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(os.environ["HEAD_CONFIG_DIR"])
    cmd = ["claude", "-p", TASK + nudge, "--model", os.environ.get("HEAD_MODEL", "sonnet"), "--output-format", "json",
           "--strict-mcp-config", "--no-session-persistence", "--tools", tools, *extra]
    t0 = time.time()
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1800)
    res = json.loads(out.stdout)
    u = res.get("usage", {})
    return {"arm": arm, "seconds": round(time.time() - t0), "turns": res.get("num_turns"),
            "cost_usd": res.get("total_cost_usd"), "input": u.get("input_tokens", 0),
            "cache_write": u.get("cache_creation_input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
            "output": u.get("output_tokens", 0), "answer": res.get("result", ""), "error": res.get("is_error")}


def main():
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
    lines = ["| head | total tokens | input | cache write | cache read | output | cost | turns | time |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        total = r["input"] + r["cache_write"] + r["cache_read"] + r["output"]
        lines.append(f"| {r['arm']} | {total:,} | {r['input']:,} | {r['cache_write']:,} | {r['cache_read']:,} | "
                     f"{r['output']:,} | ${r['cost_usd'] or 0:.3f} | {r['turns']} | {r['seconds']}s |")
    answers = "\n\n".join(f"### {r['arm']}\n\n{r['answer']}" for r in rows)
    report = (f"Task ({len(papers)} PDFs: {', '.join(p.name for p in papers)}): {TASK}\n\n" + "\n".join(lines)
              + "\n\n" + answers + "\n")
    (out / "head_tokens.md").write_text(report)
    (out / "head_tokens.json").write_text(json.dumps(rows, indent=1))
    print(report)


if __name__ == "__main__":
    main()
