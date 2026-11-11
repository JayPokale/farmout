"""Real-bug review benchmark: BugsInPy fixes, reversed into bug-introducing changes.

For each listed bug, the project is fetched at its fixed commit and the fix is reverse-applied as an
uncommitted change, so reviewers see a real regression in its real codebase. Every enabled worker gets the
exact /modelmux:review brief (Antigravity on Flash low, as the skill asks). A bug counts as found when a
finding names its file and a line within 3 of the changed lines. Spends one review per worker per bug.

Run:  python3 bench/real_bugs.py [worker ...]      (writes bench/results/<date>-real/)
Data: https://github.com/soarsmu/BugsInPy (bug.info, bug_patch.txt, project.info), fetched at run time.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_recall import BRIEF, CITE, HEADER, ROOT, Server, findings, row  # noqa: E402

BUGS = ["cookiecutter/1", "httpie/3", "httpie/4", "PySnooper/3", "tqdm/1", "tqdm/4", "tqdm/6", "tqdm/7", "tqdm/8"]
RAW = "https://raw.githubusercontent.com/soarsmu/BugsInPy/master/projects/"
NEAR = 3


def fetch(path):
    with urllib.request.urlopen(RAW + path, timeout=60) as r:
        return r.read().decode()


def info(text):
    return dict(re.findall(r'^(\w+)="?([^"\n]*)"?', text, re.M))


def make_repo(bug):
    """The project at the fix commit, with the fix reverse-applied as an uncommitted change."""
    project, n = bug.split("/")
    url = info(fetch(f"{project}/project.info"))["github_url"]
    fixed = info(fetch(f"{project}/bugs/{n}/bug.info"))["fixed_commit_id"]
    patch = fetch(f"{project}/bugs/{n}/bug_patch.txt")
    repo = Path(tempfile.mkdtemp(prefix=f"modelmux-real-{project}-{n}-"))

    def git(*args, stdin=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=stdin, check=True, capture_output=True,
                              text=True).stdout

    git("init", "-q")
    git("fetch", "-q", "--depth", "1", url, fixed)
    git("checkout", "-q", "FETCH_HEAD")
    git("apply", "-R", stdin=patch)
    diff = Path(git("rev-parse", "--absolute-git-dir").strip()) / "review.diff"
    diff.write_text(git("diff", "HEAD"))
    changed = {}  # file -> lines of the change in the current (buggy) file
    current = None
    for line in git("diff", "HEAD", "-U0").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("@@") and current:
            start, _, count = re.search(r"\+(\d+)(,\d+)?", line).group(1, 0, 2)
            start, count = int(start), int((count or ",1")[1:])
            changed.setdefault(current, []).extend(range(start, start + max(count, 1)))
    return repo, diff, changed


def found(text, changed):
    return any(Path(path).name in {Path(f).name for f in changed} and
               min(abs(int(n) - x) for f, xs in changed.items() if Path(f).name == Path(path).name for x in xs) <= NEAR
               for path, n in CITE.findall(text))


def main():
    out = ROOT / "bench" / "results" / (time.strftime("%Y-%m-%d") + "-real")
    s = Server(dict(os.environ))
    runs, calls = [], []
    try:
        tools = s.call("tools/list")["result"]["tools"]
        workers = tools[0]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["worker"]["enum"] if tools else []
        workers = [w for w in workers if w in sys.argv[1:] and w != "api"] or [w for w in workers if w != "api"]
        for bug in BUGS:
            repo, diff, changed = make_repo(bug)
            print(f"{bug}: {sum(map(len, changed.values()))} changed lines in {', '.join(changed)}", flush=True)
            t0 = time.time()
            res = s.call("tools/call", {"name": "delegate", "arguments": {"tasks": [
                {"worker": w, "cwd": str(repo), "files": [str(diff)], "prompt": BRIEF,
                 **({"model": "gemini-3.8-flash-low"} if w == "antigravity" else {})} for w in workers]}}, timeout=3600)
            calls.append({"bug": bug, "wall": round(time.time() - t0)})
            (out / bug.replace("/", "-")).mkdir(parents=True, exist_ok=True)
            for worker, status, secs, path in HEADER.findall(res["result"]["content"][0]["text"]):
                text = Path(path).read_text()
                (out / bug.replace("/", "-") / f"{worker}.md").write_text(text)
                runs.append({"bug": bug, "worker": worker, "ok": status == "ok", "secs": int(secs),
                             "findings": findings(text), "hit": found(text, changed), "changed": changed})
            shutil.rmtree(repo, ignore_errors=True)
    finally:
        s.close()
    hit = {(r["worker"], r["bug"]) for r in runs if r["hit"]}
    table = [row(["reviewer", f"found (of {len(BUGS)})", "findings", "review time"]), "|---|---|---|---|"]
    for w in workers:
        mine = [r for r in runs if r["worker"] == w]
        table.append(row([w, sum((w, b) in hit for b in BUGS), sum(r["findings"] for r in mine),
                          f"{sum(r['secs'] for r in mine)}s"]))
    table.append(row(["**all, in parallel**", f"**{sum(any((w, b) in hit for w in workers) for b in BUGS)}**",
                      sum(r["findings"] for r in runs), f"{sum(c['wall'] for c in calls)}s"]))
    detail = [row(["bug", *workers]), "|" + "---|" * (len(workers) + 1)]
    detail += [row([b, *("✓" if (w, b) in hit else "·" for w in workers)]) for b in BUGS]
    summary = "\n".join(table) + "\n\nPer bug (✓ = found):\n\n" + "\n".join(detail) + "\n"
    (out / "summary.md").write_text(summary)
    (out / "results.json").write_text(json.dumps({"runs": runs, "calls": calls}, indent=1))
    print(summary)


if __name__ == "__main__":
    main()
