"""Seeded-bug review benchmark: how many planted bugs each worker finds alone, and all of them together.

Each case in bench/cases/<name>/ holds new files (files/) and the bugs planted in them (bugs.json).
For every case the harness builds a throwaway git repo where those files are an uncommitted change,
then sends every enabled worker the exact /farmout:review brief in one parallel delegate call.
A planted bug counts as found when a finding names its file and a line within 3 of it, or within 10
when the finding's text names the defect (the bug's keywords).
Spends real quota: one review per worker per case.

Run:  python3 bench/review_recall.py [worker ...] [case ...]   (writes bench/results/<date>/)
      python3 bench/review_recall.py --score <dir>   (re-score saved outputs, no model calls)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_server import Server  # noqa: E402

BRIEF = re.search(r"```text\n(.*?)```", (ROOT / "skills" / "review" / "SKILL.md").read_text(), re.S).group(1).strip()
HEADER = re.compile(r"^## \[\d+\] (\S+) (ok|FAILED) (\d+)s -> (.+)$", re.M)
CITE = re.compile(r"([\w./-]+\.\w+):(\d+)")
NEAR, FAR = 3, 10  # a finding counts within NEAR lines, or within FAR if its text names the defect


def load_bugs(case):
    bugs = json.loads((case / "bugs.json").read_text())
    for b in bugs:
        lines = (case / "files" / b["file"]).read_text().splitlines()
        b["lines"] = [i for i, text in enumerate(lines, 1) if any(m in text for m in b["match"])]
        assert len(b["lines"]) == len(b["match"]), (case.name, b["id"], b["lines"])
    for a in bugs:  # one finding must never be able to match two planted bugs
        for b in bugs:
            if a is not b and a["file"] == b["file"]:
                assert min(abs(x - y) for x in a["lines"] for y in b["lines"]) > 2 * NEAR, (a["id"], b["id"])
    return bugs


def make_repo(case):
    """A throwaway repo whose uncommitted change adds the case's files; returns (repo, diff file)."""
    repo = Path(tempfile.mkdtemp(prefix=f"farmout-bench-{case.name}-"))

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q")
    (repo / "README.md").write_text(f"# {case.name}\n")
    git("add", ".")
    git("-c", "user.name=bench", "-c", "user.email=bench@localhost", "commit", "-qm", "base")
    shutil.copytree(case / "files", repo, dirs_exist_ok=True)
    git("add", "-N", ".")  # intent-to-add, so `git diff HEAD` shows the new files
    diff = Path(git("rev-parse", "--absolute-git-dir").strip()) / "review.diff"
    diff.write_text(git("diff", "HEAD"))
    return repo, diff


def credit(text, bugs):
    """Ids of the planted bugs a review found. Each finding credits at most one bug: the nearest one it names
    (keywords, within FAR lines), else the nearest within NEAR lines."""
    hits = set()
    for line in text.splitlines():
        for path, n in CITE.findall(line)[:1]:  # a finding's location is the first path:line on its line
            low, dist = line.lower(), [(min(abs(int(n) - x) for x in b["lines"]), b) for b in bugs
                                       if Path(path).name == Path(b["file"]).name]
            named = [(d, b["id"]) for d, b in dist if d <= FAR and any(k in low for k in b["keywords"])]
            near = [(d, b["id"]) for d, b in dist if d <= NEAR]
            if named or near:
                hits.add(min(named or near)[1])
    return hits


def findings(text):
    return sum(1 for line in text.splitlines() if "|" in line and CITE.search(line))


def row(cells):
    return "| " + " | ".join(map(str, cells)) + " |"


def summarize(cases, workers, runs, calls, matrix):
    hits = {(m["worker"], m["case"], m["id"]) for m in matrix if m["hit"]}
    bugs = {c.name: [b["id"] for b in load_bugs(c)] for c in cases}
    head = ["reviewer", *(f"{c} ({len(ids)})" for c, ids in bugs.items()), f"total ({sum(map(len, bugs.values()))})",
            "findings", "time"]
    table = [row(head), "|" + "---|" * len(head)]
    for w in workers:
        per = [sum((w, c, i) in hits for i in ids) for c, ids in bugs.items()]
        mine = [r for r in runs if r["worker"] == w]
        table.append(row([w, *per, sum(per), sum(r["findings"] for r in mine), f"{sum(r['secs'] for r in mine)}s"]))
    per = [sum(any((w, c, i) in hits for w in workers) for i in ids) for c, ids in bugs.items()]
    table.append(row(["**all, in parallel**", *per, f"**{sum(per)}**", sum(r["findings"] for r in runs),
                      f"{sum(c['wall'] for c in calls)}s"]))
    detail = [row(["bug", *workers]), "|" + "---|" * (len(workers) + 1)]
    for c, ids in bugs.items():
        detail += [row([f"{c}/{i}", *("✓" if (w, c, i) in hits else "·" for w in workers)]) for i in ids]
    return "\n".join(table) + "\n\nPer bug (✓ = found):\n\n" + "\n".join(detail) + "\n"


def score(out):
    """Score the raw outputs saved in `out`; rerun any time with --score <dir>."""
    cases = sorted(p for p in (ROOT / "bench" / "cases").iterdir() if p.is_dir())
    meta = json.loads((out / "results.json").read_text())
    workers = list(dict.fromkeys(r["worker"] for r in meta["runs"]))
    matrix = []
    for case in cases:
        bugs = load_bugs(case)
        for w in workers:
            f = out / case.name / f"{w}.md"
            hit = credit(f.read_text(), bugs) if f.exists() else set()
            matrix += [{"case": case.name, "id": b["id"], "worker": w, "hit": b["id"] in hit} for b in bugs]
    summary = summarize(cases, workers, meta["runs"], meta["calls"], matrix)
    (out / "summary.md").write_text(summary)
    (out / "results.json").write_text(json.dumps({"runs": meta["runs"], "calls": meta["calls"], "matrix": matrix}, indent=1))
    return summary


def main():
    cases = sorted(p for p in (ROOT / "bench" / "cases").iterdir() if p.is_dir())
    out = ROOT / "bench" / "results" / time.strftime("%Y-%m-%d")
    s = Server(dict(os.environ))
    runs, calls = [], []
    try:
        tools = s.call("tools/list")["result"]["tools"]
        workers = tools[0]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["worker"]["enum"] if tools else []
        workers = [w for w in workers if w in sys.argv[1:]] or workers
        cases = [c for c in cases if c.name in sys.argv[1:]] or cases
        if not workers:
            sys.exit("no workers enabled; see `python3 server.py doctor`")
        for case in cases:
            bugs = load_bugs(case)
            repo, diff = make_repo(case)
            print(f"{case.name}: {len(bugs)} planted bugs, {len(workers)} reviewers...", flush=True)
            t0 = time.time()
            res = s.call("tools/call", {"name": "delegate", "arguments": {"tasks": [
                {"worker": w, "cwd": str(repo), "files": [str(diff)], "prompt": BRIEF} for w in workers]}}, timeout=3600)
            calls.append({"case": case.name, "wall": round(time.time() - t0)})
            (out / case.name).mkdir(parents=True, exist_ok=True)
            for worker, status, secs, path in HEADER.findall(res["result"]["content"][0]["text"]):
                text = Path(path).read_text()
                (out / case.name / f"{worker}.md").write_text(text)
                runs.append({"case": case.name, "worker": worker, "ok": status == "ok", "secs": int(secs),
                             "findings": findings(text)})
            shutil.rmtree(repo, ignore_errors=True)
    finally:
        s.close()
    if (out / "results.json").exists():  # keep earlier cases from the same day
        old = json.loads((out / "results.json").read_text())
        done = {c.name for c in cases}
        runs = [r for r in old["runs"] if r["case"] not in done] + runs
        calls = [c for c in old["calls"] if c["case"] not in done] + calls
    (out / "results.json").write_text(json.dumps({"runs": runs, "calls": calls}, indent=1))
    summary = score(out)
    failed = [r for r in runs if not r["ok"]]
    print(summary + (f"\nFAILED runs: {failed}" if failed else ""))


if __name__ == "__main__":
    print(score(Path(sys.argv[2]).resolve())) if sys.argv[1:2] == ["--score"] else main()
