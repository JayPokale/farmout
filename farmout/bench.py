"""`farmout bench`: which of your subscriptions reviews best on your own code.

Recent small bug-fix commits are undone in a scratch worktree, so each bug is back as an uncommitted change in its
real codebase; every worker reviews it with farmout's review brief, and a worker scores when one of its findings
points within 3 lines of the reintroduced change. Costs one review per worker per commit.
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

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


def run(cwd, n=3, workers=None, scan=400, progress=print, lanes=None):
    """lanes(group, title), if given, returns a context manager that shows each commit's reviews as they run
    (the CLI's live lanes); otherwise progress(line) hears about each commit."""
    top = repo_root(cwd)
    if not top:
        raise ValueError("farmout bench works on a git repository; run it inside one")
    picks = candidates(str(top), n, scan)
    if not picks:
        raise ValueError(f"no small bug-fix commits in the last {scan} (subjects like 'fix ...', at most 80 changed lines)")
    group, rows = sessions.new_group("bench"), []
    for i, c in enumerate(picks, 1):
        title = f"[{i}/{len(picks)}] {c['sha'][:9]} {c['subject'][:70]}"
        if not lanes:
            progress(title)
        tmp = Path(tempfile.mkdtemp(prefix="farmout-bench-"))
        wt = tmp / "wt"
        git(top, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-q", "--detach", str(wt), c["sha"])
        try:
            fix = git(top, "diff", "--binary", f"{c['sha']}^", c["sha"], binary=True)
            git(wt, "apply", "-R", "--whitespace=nowarn", stdin=fix, binary=True)
            changed = changed_lines(str(wt))
            t0 = time.time()
            with lanes(f"{group}-{i}", title) if lanes else contextlib.nullcontext():
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


# `farmout bench --share`: the result as something people can post. Scores only: no repository name, file names,
# commit subjects or code, so sharing it can't leak a private codebase.
NAMES = {"claude": "Claude", "codex": "Codex", "antigravity": "Gemini", "copilot": "Copilot", "deepseek": "DeepSeek"}
COLOURS = {"claude": "#f47c7c", "codex": "#f2a65a", "antigravity": "#5ec4b6", "copilot": "#a78bfa", "deepseek": "#4d6bfe",
           "api": "#ece8df"}
REPO = "github.com/JayPokale/farmout"


def display(worker):
    return NAMES.get(worker) or (os.environ.get("FARMOUT_API_MODEL") if worker == "api" else None) or worker


def card_text(board, commits):
    """A post that fits on X: one bar per worker."""
    width = max(1, min(commits, 10))
    pad = max(len(display(w)) for w, *_ in board)
    lines = []
    for w, found, of, failed in board:
        filled = round(width * found / of) if of else 0
        score = f"{found}/{of}" if of else "failed"
        lines.append(f"{display(w):<{pad}}  {'█' * filled}{'░' * (width - filled)} {score}")
    fixes = f"{commits} real bug fix{'es' * (commits != 1)}"
    return (f"Which AI reviews my code best? I replayed {fixes} from my own git history:\n\n"
            + "\n".join(lines) + "\n\nRanked with farmout: uvx farmout bench")


def card_svg(board, commits, day):
    """A 1200x630 image card in the site's colours."""
    rows, top = [], 250
    for i, (w, found, of, failed) in enumerate(board[:5]):
        y = top + i * 68
        frac = found / of if of else 0
        colour = COLOURS.get(w, "#ece8df")
        score = f"{found}/{of}" if of else "failed"
        rows.append(
            f'<text x="80" y="{y + 30}" font-size="32" font-weight="700" fill="{colour}">{escape(display(w))}</text>'
            f'<rect x="330" y="{y + 6}" width="660" height="34" rx="8" fill="#171a23"/>'
            f'<rect x="330" y="{y + 6}" width="{max(0.0, 660 * frac):.0f}" height="34" rx="8" fill="{colour}"/>'
            f'<text x="1120" y="{y + 32}" font-size="30" font-weight="700" fill="#ece8df" text-anchor="end">{score}</text>')
    fixes = f"{commits} real bug fix{'es' * (commits != 1)} from my git history, replayed, and each AI asked to find {'them' if commits != 1 else 'it'}"
    lanes = "".join(f'<path d="M104 256H196C{250 + (i in (0, 3)) * 6} 256 262 {y} 326 {y}H384" stroke="{c}" stroke-width="26" '
                    'stroke-linecap="round" fill="none"/>'
                    for i, (y, c) in enumerate(((140, "#f2a65a"), (218, "#5ec4b6"), (294, "#a78bfa"), (372, "#f47c7c"))))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#0b0c11"/>
<g font-family="Hubot Sans, Mona Sans, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">
<g transform="translate(80 60) scale(0.11)"><rect width="512" height="512" rx="112" fill="#14161f"/>{lanes}
<path d="M104 256H204" stroke="#ece8df" stroke-width="26" stroke-linecap="round"/><circle cx="104" cy="256" r="40" fill="#ece8df"/></g>
<text x="152" y="102" font-size="30" font-weight="700" fill="#ece8df">farmout bench</text>
<text x="1120" y="102" font-size="24" fill="#9a9eaf" text-anchor="end">{escape(day)}</text>
<text x="80" y="180" font-size="56" font-weight="800" fill="#ece8df">Which AI reviews my code best?</text>
<text x="80" y="222" font-size="24" fill="#9a9eaf">{escape(fixes)}</text>
{"".join(rows)}
<text x="80" y="{min(590, top + len(board[:5]) * 68 + 50)}" font-size="26" fill="#9a9eaf">Ranked with farmout · uvx farmout bench · {REPO}</text>
</g>
</svg>
"""


def png(svg_path):
    """Render the card to PNG with whichever converter is installed (X and LinkedIn don't take SVG); None if none."""
    out = svg_path.with_suffix(".png")
    for cmd in (["rsvg-convert", "-o", str(out), str(svg_path)], ["magick", str(svg_path), str(out)],
                ["convert", str(svg_path), str(out)], ["inkscape", str(svg_path), "--export-filename", str(out)]):
        if shutil.which(cmd[0]):
            try:
                if subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0 and out.exists():
                    return out
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


def share(board, rows, report):
    """Write the cards next to the report; returns {text, svg, png, url}."""
    text = card_text(board, len(rows))
    svg = Path(report).with_suffix(".svg")
    svg.write_text(card_svg(board, len(rows), time.strftime("%Y-%m-%d")))
    image = png(svg)
    url = "https://x.com/intent/tweet?text=" + urllib.parse.quote(f"{text}\n\n{REPO}")
    return {"text": text, "svg": str(svg), "png": str(image) if image else None, "url": url}
