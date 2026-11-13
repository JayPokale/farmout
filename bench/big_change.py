"""One big change: all planted cases at once, so each reviewer can report only its top 10 findings.

Builds one throwaway repo holding every case's files as a single uncommitted change (23 planted bugs),
sends every enabled worker the exact /farmout:review brief once (Antigravity on Flash low, as the skill
asks), and scores with the same rules as review_recall.py. Spends one review per worker.

Run:  python3 bench/big_change.py [worker ...]      (writes bench/results/<date>-big/)
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_recall import BRIEF, HEADER, ROOT, Server, credit, findings, load_bugs, make_repo, row  # noqa: E402


def main():
    cases = sorted(p for p in (ROOT / "bench" / "cases").iterdir() if p.is_dir())
    combined = Path(tempfile.mkdtemp(prefix="farmout-big-")) / "all"
    (combined / "files").mkdir(parents=True)
    bugs = []
    for case in cases:
        shutil.copytree(case / "files", combined / "files", dirs_exist_ok=True)
        bugs += [{**b, "case": case.name} for b in load_bugs(case)]
    (combined / "bugs.json").write_text(json.dumps(bugs))
    repo, diff = make_repo(combined)
    out = ROOT / "bench" / "results" / (time.strftime("%Y-%m-%d") + "-big")
    out.mkdir(parents=True, exist_ok=True)
    s = Server(dict(os.environ))
    try:
        tools = s.call("tools/list")["result"]["tools"]
        workers = tools[0]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["worker"]["enum"]
        workers = [w for w in workers if w in sys.argv[1:] and w != "api"] or [w for w in workers if w != "api"]
        t0 = time.time()
        res = s.call("tools/call", {"name": "delegate", "arguments": {"tasks": [
            {"worker": w, "cwd": str(repo), "files": [str(diff)], "prompt": BRIEF,
             **({"model": "gemini-3.8-flash-low"} if w == "antigravity" else {})} for w in workers]}}, timeout=3600)
        wall = round(time.time() - t0)
    finally:
        s.close()
    hits, rows = {}, []
    for worker, status, secs, path in HEADER.findall(res["result"]["content"][0]["text"]):
        text = Path(path).read_text()
        (out / f"{worker}.md").write_text(text)
        hits[worker] = credit(text, bugs)
        rows.append(row([worker, len(hits[worker]), findings(text), f"{secs}s"]))
    union = set().union(*hits.values())
    table = [row(["reviewer", f"found (of {len(bugs)})", "findings", "time"]), "|---|---|---|---|", *rows,
             row(["**all, in parallel**", f"**{len(union)}**", "", f"{wall}s"])]
    missed = sorted(b["id"] for b in bugs if b["id"] not in union)
    summary = "\n".join(table) + f"\n\nMissed by everyone: {', '.join(missed) or 'none'}\n"
    (out / "summary.md").write_text(summary)
    (out / "results.json").write_text(json.dumps({w: sorted(h) for w, h in hits.items()}, indent=1))
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(combined.parent, ignore_errors=True)
    print(summary)


if __name__ == "__main__":
    main()
