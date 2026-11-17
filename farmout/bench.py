"""`farmout bench`: which of your subscriptions reviews best on your own code.

Recent small bug-fix commits are undone in a scratch worktree, so each bug is back as an uncommitted change in its
real codebase; every worker reviews it with farmout's review brief, and a worker scores when one of its findings
points within 3 lines of the reintroduced change. Costs one review per worker per commit.

Only the fix's source files are undone: its tests, docs and changelog stay as fixed, since a diff that deletes
`test_zero_division` or a "Fixed: ..." line would name the bug. The scratch copy's only commit holds the fixed tree
under a neutral message, so a worker that reads `git log` can't find the fix's subject there either.
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
TEST = re.compile(r"(^|/)(tests?|specs?|__tests__|testdata|fixtures?)/|(^|/)(test_[^/]*|conftest\.py)$|_(test|spec)\.\w+$"
                  r"|\.(test|spec)\.\w+$", re.I)
DOC = re.compile(r"(^|/)(docs?|changelog\.?d?|changes|news|releases?)/|\.(md|rst|txt|adoc)$|(^|/)(CHANGES|CHANGELOG|NEWS|HISTORY)[^/]*$",
                 re.I)
NEAR = 3
SNAPSHOT = ("-c", "user.name=farmout", "-c", "user.email=farmout@localhost", "-c", "core.hooksPath=/dev/null")

def candidates(top, n, scan=400, since=None):
    """Up to n recent commits that look like small bug fixes in code: a fix-like subject and 1-80 changed lines in at
    most 4 source files. Tests and docs may change too, and stay as fixed; anything else (a template, a config) rules
    the commit out, since undoing only its code would half-undo the fix."""
    out = []
    since = [f"--since={since}"] if since else []
    for line in git(top, "log", f"-n{scan}", *since, "--no-merges", "--format=%H%x09%P%x09%s").splitlines():
        sha, parents, subject = (line.split("\t", 2) + ["", ""])[:3]
        if not parents or " " in parents.strip() or not FIX.search(subject):
            continue
        moved = git(top, "show", "--diff-filter=ADR", "--name-only", "--format=", sha).splitlines()
        if any(f.strip() and not TEST.search(f) for f in moved):
            continue  # a fix that adds, deletes or renames source files leaves no lines to point at once undone
        rows = [r.split("\t") for r in git(top, "show", "--numstat", "--format=", sha).splitlines() if r.strip()]
        src = [r for r in rows if len(r) == 3 and r[0].isdigit() and r[1].isdigit()
               and Path(r[2]).suffix.lower() in CODE and not TEST.search(r[2])]
        if any(r not in src and not TEST.search(r[-1]) and not DOC.search(r[-1]) for r in rows):
            continue  # a template, stylesheet or config is part of the fix: undoing only the code would half-undo it
        size = sum(int(r[0]) + int(r[1]) for r in src)
        if src and len(src) <= 4 and 0 < size <= 80:
            out.append({"sha": sha, "subject": subject, "files": [r[2] for r in src]})
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


def prepare(top, c, wt, fresh=False, settings=None):
    """Make wt a scratch copy of the fixed tree, as its only commit, with the fix's source changes undone. fresh=True
    copies the files into a new repository instead of adding a worktree, so not even `git log --all` reaches the
    original history (public benchmarks); a worktree keeps your repository's .farmout/settings in force. settings,
    with fresh, is the copy's own .farmout/settings.toml (kept out of git, so no reviewer sees it as a change)."""
    if fresh:
        wt.mkdir(parents=True)
        tar = git(top, "archive", "--format=tar", c["sha"], binary=True)
        subprocess.run(["tar", "-x", "-C", str(wt)], input=tar, check=True, capture_output=True)
        git(wt, "init", "-q")
        git(wt, "add", "-A", "-f")
        git(wt, *SNAPSHOT, "commit", "-q", "--no-verify", "-m", "snapshot")
    else:
        snap = git(top, *SNAPSHOT, "commit-tree", f"{c['sha']}^{{tree}}", "-m", "snapshot").strip()
        git(top, *SNAPSHOT, "worktree", "add", "-q", "--detach", str(wt), snap)
    fix = git(top, "diff", "--binary", f"{c['sha']}^", c["sha"], "--", *c["files"], binary=True)
    git(wt, "apply", "-R", "--whitespace=nowarn", stdin=fix, binary=True)
    if fresh and settings:
        (wt / ".farmout").mkdir(exist_ok=True)
        (wt / ".farmout" / "settings.toml").write_text(settings)
        with open(wt / ".git" / "info" / "exclude", "a") as f:
            f.write("\n/.farmout/\n")

def replay(top, c, workers=None, group=None, models=None, lanes=None, title="", fresh=False, settings=None):
    """One fix, undone in a scratch copy and reviewed. Returns the row: commit, subject, secs, found {worker: bool},
    failed {worker: reason}, findings {worker: [...]}."""
    group = group or sessions.new_group("bench")
    tmp = Path(tempfile.mkdtemp(prefix="farmout-bench-"))
    wt = tmp / "wt"
    try:
        prepare(top, c, wt, fresh, settings)
        changed = changed_lines(str(wt))
        t0 = time.time()
        with lanes(group, title) if lanes else contextlib.nullcontext():
            r = review.review(str(wt), workers=workers, group=group, models=models)
        mine = {w: [{k: f.get(k) for k in ("rel", "path", "line", "severity", "defect")}
                    for cl in r["clusters"] for f in cl["findings"] if f["worker"] == w] for w in r["workers"]}
        return {"commit": c["sha"], "subject": c["subject"], "secs": round(time.time() - t0),
                "found": {w: scored(r, changed, w) for w in r["workers"]}, "failed": r["failed"], "findings": mine,
                "worker_secs": r["secs"], "replies": r["replies"]}
    finally:
        if not fresh:
            subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        for name in sessions.names():  # their worktree is gone, so in `farmout ls` they'd only be clutter
            if name.startswith(f"{group}-"):
                with contextlib.suppress(RuntimeError, OSError):
                    sessions.remove(name)

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
        rows.append(replay(str(top), c, workers, f"{group}-{i}", lanes=lanes, title=title))
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
    """A worker's name for people; a leaderboard lane `worker:model` shows its model too."""
    worker, _, model = worker.partition(":")
    name = NAMES.get(worker) or (os.environ.get("FARMOUT_API_MODEL") if worker == "api" and not model else None) or worker
    return f"{name} · {model}" if model else name


def card_text(board, commits):
    """A post that fits on X: one bar per worker."""
    width = max(1, min(commits, 10))
    pad = max([len(display(w)) for w, *_ in board] or [0])
    lines = []
    for w, found, of, failed in board:
        filled = round(width * found / of) if of else 0
        score = f"{found}/{of}" if of else "failed"
        lines.append(f"{display(w):<{pad}}  {'█' * filled}{'░' * (width - filled)} {score}")
    fixes = f"{commits} real bug fix{'es' * (commits != 1)}"
    return (f"Which AI reviews my code best? I replayed {fixes} from my own git history:\n\n"
            + "\n".join(lines) + f"\n\nRanked with farmout: {REPO}")


def card_svg(board, commits, day, title="Which AI reviews my code best?", subtitle=None, label="farmout bench"):
    """A 1200x630 image card in the site's colours."""
    rows, top = [], 250
    wide = max([len(display(w)) for w, *_ in board[:5]] or [0]) > 10  # leaderboard lanes carry a model name
    size, bar = (24, 520) if wide else (32, 330)
    for i, (w, found, of, failed) in enumerate(board[:5]):
        y = top + i * 68
        frac = found / of if of else 0
        colour = COLOURS.get(w.partition(":")[0], "#ece8df")
        score = f"{found}/{of}" if of else "failed"
        rows.append(
            f'<text x="80" y="{y + 30}" font-size="{size}" font-weight="700" fill="{colour}">{escape(display(w))}</text>'
            f'<rect x="{bar}" y="{y + 6}" width="{990 - bar}" height="34" rx="8" fill="#171a23"/>'
            f'<rect x="{bar}" y="{y + 6}" width="{max(0.0, (990 - bar) * frac):.0f}" height="34" rx="8" fill="{colour}"/>'
            f'<text x="1120" y="{y + 32}" font-size="30" font-weight="700" fill="#ece8df" text-anchor="end">{score}</text>')
    fixes = subtitle or (f"{commits} real bug fix{'es' * (commits != 1)} from my git history, replayed, and each AI "
                         f"asked to find {'them' if commits != 1 else 'it'}")
    lanes = "".join(f'<path d="M104 256H196C{250 + (i in (0, 3)) * 6} 256 262 {y} 326 {y}H384" stroke="{c}" stroke-width="26" '
                    'stroke-linecap="round" fill="none"/>'
                    for i, (y, c) in enumerate(((140, "#f2a65a"), (218, "#5ec4b6"), (294, "#a78bfa"), (372, "#f47c7c"))))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#0b0c11"/>
<g font-family="Hubot Sans, Mona Sans, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">
<g transform="translate(80 60) scale(0.11)"><rect width="512" height="512" rx="112" fill="#14161f"/>{lanes}
<path d="M104 256H204" stroke="#ece8df" stroke-width="26" stroke-linecap="round"/><circle cx="104" cy="256" r="40" fill="#ece8df"/></g>
<text x="152" y="102" font-size="30" font-weight="700" fill="#ece8df">{escape(label)}</text>
<text x="1120" y="102" font-size="24" fill="#9a9eaf" text-anchor="end">{escape(day)}</text>
<text x="80" y="180" font-size="56" font-weight="800" fill="#ece8df">{escape(title)}</text>
<text x="80" y="222" font-size="24" fill="#9a9eaf">{escape(fixes)}</text>
{"".join(rows)}
<text x="80" y="{min(590, top + len(board[:5]) * 68 + 50)}" font-size="26" fill="#9a9eaf">Ranked with farmout · {REPO}</text>
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
    url = "https://x.com/intent/tweet?text=" + urllib.parse.quote(text)
    return {"text": text, "svg": str(svg), "png": str(image) if image else None, "url": url}
