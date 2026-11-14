"""`farmout bench`: which of your subscriptions reviews best on your own code.

Recent small bug-fix commits are undone in a scratch worktree, so each bug is back as an uncommitted change in its
real codebase; every worker reviews it with farmout's review brief, and a worker scores when one of its findings
points within 3 lines of the reintroduced change. Costs one review per worker per commit.
"""
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import review, sessions
from .config import STATE, repo_root
from .isolate import git

FIX = re.compile(r"\b(fix(e[sd])?|bug|regression|crash(es)?|broken|wrong|leak|race)\b", re.I)
CODE = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp",
        ".h", ".hpp", ".cs", ".swift", ".scala", ".sh", ".lua", ".m", ".ex", ".exs", ".dart", ".vue", ".svelte"}
NEAR = 3


def candidates(top, n, scan=400):
    """Up to n recent commits that look like small bug fixes in code: a fix-like subject, at most 4 files, 1-80
    changed lines of code."""
    out = []
    for line in git(top, "log", f"-n{scan}", "--no-merges", "--format=%H%x09%P%x09%s").splitlines():
        sha, parents, subject = (line.split("\t", 2) + ["", ""])[:3]
        if not parents or " " in parents.strip() or not FIX.search(subject):
            continue
        if git(top, "show", "--diff-filter=ADR", "--name-only", "--format=", sha).strip():
            continue  # a fix that adds, deletes or renames files leaves no lines to point at once undone
        rows = [r.split("\t") for r in git(top, "show", "--numstat", "--format=", sha).splitlines() if r.strip()]
        code = [r for r in rows if len(r) == 3 and r[0].isdigit() and r[1].isdigit() and Path(r[2]).suffix.lower() in CODE]
        size = sum(int(r[0]) + int(r[1]) for r in code)
        if code and len(rows) <= 4 and 0 < size <= 80:
            out.append({"sha": sha, "subject": subject, "files": [r[2] for r in code]})
            if len(out) >= n:
                break
    return out


def changed_lines(wt):
    """file -> line numbers of the uncommitted change in the current files."""
    out, current = {}, None
    for line in git(wt, "diff", "HEAD", "-U0").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(current, []).extend(range(start, start + max(count, 1)))
    return out


def scored(result, changed, worker):
    for c in result["clusters"]:
        for f in c["findings"]:
            if f["worker"] == worker and f.get("rel") in changed and min(abs(f["line"] - x) for x in changed[f["rel"]]) <= NEAR:
                return True
    return False


def run(cwd, n=3, workers=None, scan=400, progress=print):
    top = repo_root(cwd)
    if not top:
        raise ValueError("farmout bench works on a git repository; run it inside one")
    picks = candidates(str(top), n, scan)
    if not picks:
        raise ValueError(f"no small bug-fix commits in the last {scan} (subjects like 'fix ...', at most 80 changed lines)")
    group, rows = sessions.new_group("bench"), []
    for i, c in enumerate(picks, 1):
        progress(f"[{i}/{len(picks)}] {c['sha'][:9]} {c['subject'][:70]}")
        tmp = Path(tempfile.mkdtemp(prefix="farmout-bench-"))
        wt = tmp / "wt"
        git(top, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-q", "--detach", str(wt), c["sha"])
        try:
            fix = git(top, "diff", "--binary", f"{c['sha']}^", c["sha"], binary=True)
            git(wt, "apply", "-R", "--whitespace=nowarn", stdin=fix, binary=True)
            changed = changed_lines(str(wt))
            t0 = time.time()
            r = review.review(str(wt), workers=workers, group=f"{group}-{i}")
            rows.append({"commit": c["sha"], "subject": c["subject"], "secs": round(time.time() - t0),
                         "found": {w: scored(r, changed, w) for w in r["workers"]}, "failed": r["failed"]})
        finally:
            subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(wt)], capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)
    out = STATE / "bench" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"repo": str(top), "commits": rows}, indent=1))
    return rows, out


def scoreboard(rows):
    """[(worker, found, out of, failed)] best first."""
    workers = sorted({w for r in rows for w in list(r["found"]) + list(r["failed"])})
    board = [(w, sum(r["found"].get(w, False) for r in rows), sum(w in r["found"] for r in rows),
              sum(w in r["failed"] for r in rows)) for w in workers]
    return sorted(board, key=lambda b: (-b[1], b[3], b[0]))
