"""The Bug-Finding Leaderboard: recent real bug fixes from well-known open-source repositories, undone one at a
time, and every lane (a worker, optionally on a named model) asked to find each bug with farmout's review brief.

  python3 bench/leaderboard.py pick [--per-repo 5] [--since 2026-08-01]   # choose fixes into suite.json, then READ them
                                                     # (a rejected fix goes in the repo's "skip" list; pick --refresh --only)
  python3 bench/leaderboard.py check                                      # every fix undoes cleanly (no quota)
  python3 bench/leaderboard.py run [-l codex,claude:opus,copilot:<model>] [--only owner/repo] [--max N]
  python3 bench/leaderboard.py rescore                                    # after a parser fix: score saved replies again
  python3 bench/leaderboard.py render                                     # README.md, leaderboard.json, card.svg/.png

Each (fix, lane) costs one review on that lane's subscription. Results land in leaderboard/results/<lane>/, one file
per fix, so a run resumes where it stopped and a new model is one more lane: `run -l copilot:<model>`, then `render`.
Only the fix's source files are undone, in a fresh repository whose only commit is "snapshot": no regression test,
changelog line or commit subject names the bug (see farmout/bench.py). Clones live in ~/.cache/farmout/leaderboard
(bare and blobless: history up front, file contents as they're needed).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from farmout import VERSION, bench, review, sessions, workers  # noqa: E402
from farmout.isolate import git  # noqa: E402

BOARD = Path(os.environ.get("FARMOUT_LEADERBOARD") or HERE / "leaderboard")  # another folder: tests, private runs
SUITE = BOARD / "suite.json"
RESULTS = BOARD / "results"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "farmout" / "leaderboard"
CLI = {"antigravity": "agy", "codex": "codex", "claude": "claude", "copilot": "copilot", "deepseek": "dsh"}
# fix-like subjects that aren't bugs a reviewer could catch in the code: chores, docs, tooling, flaky tests
CHORE = re.compile(r"\b(typos?|lint(ing|er)?|flak(e|y)|docs?|readme|ci|format(ting)?|style|bump|chore|changelog|"
                   r"release|warnings?|deprecat\w*|types?|typing|mypy|tests?|benchmarks?|comments?|document\w*)\b", re.I)
TOOLING = re.compile(r"^(scripts?|docs?|\.github|tools?|benchmarks?|examples?|\.?ci)/")  # not the project's own code


def slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def lane_name(worker, model):
    return f"{worker}:{model}" if model else worker


def parse_lanes(text):
    out = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        worker, _, model = part.partition(":")
        out.append((worker, model or None))
    return out


def clone(repo):
    """A bare, blobless clone of github.com/<repo> (a local repository's path is used as it is)."""
    if os.path.isdir(repo):
        return Path(repo)
    d = CACHE / slug(repo)
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:none", f"https://github.com/{repo}.git", str(d)],
                       check=True)
    return d


def ready(repo, sha):
    """The clone, fetched again if it doesn't have this commit yet (a suite edited after the clone was made)."""
    top = clone(repo)
    if not os.path.isdir(repo) and subprocess.run(["git", "-C", str(top), "cat-file", "-e", f"{sha}^{{commit}}"],
                                                  capture_output=True).returncode:
        subprocess.run(["git", "-C", str(top), "fetch", "-q", "origin", "+refs/heads/*:refs/heads/*"], check=True)
    return str(top)


def load():
    return json.loads(SUITE.read_text())


def cmd_pick(a):
    suite = load()
    for r in suite["repos"]:
        if (r.get("fixes") and not a.refresh) or (a.only and r["repo"] not in a.only):
            continue
        top = str(clone(r["repo"]))
        if not os.path.isdir(r["repo"]):
            subprocess.run(["git", "-C", top, "fetch", "-q", "origin", "+refs/heads/*:refs/heads/*"], check=True)
        skip = set(r.get("skip", []))  # read by hand and rejected: not a bug, or not in the project's own code
        picks = [c for c in bench.candidates(top, a.per_repo * 4, scan=3000, since=a.since)
                 if not CHORE.search(c["subject"]) and not all(TOOLING.match(f) for f in c["files"])
                 and not any(c["sha"].startswith(x) for x in skip)]
        r["fixes"] = [{"sha": c["sha"], "subject": c["subject"], "files": c["files"],
                       "date": git(top, "show", "-s", "--format=%cs", c["sha"]).strip()} for c in picks[:a.per_repo]]
        print(f"{r['repo']}: {len(r['fixes'])} fixes")
        for f in r["fixes"]:
            print(f"  {f['sha'][:9]} {f['date']} {f['subject'][:90]}")
        SUITE.write_text(json.dumps(suite, indent=1) + "\n")
    print(f"\nRead every pick in {SUITE.relative_to(HERE.parent)} and drop any that isn't a real bug fix.")


def cmd_check(a):
    """Build every fix's scratch copy without reviewing it: proves each one undoes cleanly, spends no quota."""
    import tempfile
    bad = 0
    for r in load()["repos"]:
        for f in r.get("fixes", []):
            top = ready(r["repo"], f["sha"])
            with tempfile.TemporaryDirectory(prefix="farmout-board-") as tmp:
                try:
                    bench.prepare(top, f, Path(tmp) / "wt", fresh=True)
                    lines = sum(len(v) for v in bench.changed_lines(str(Path(tmp) / "wt")).values())
                    status = f"ok  {lines:3} lines" if lines else "EMPTY"
                except Exception as e:  # noqa: BLE001 (report and go on)
                    status = f"FAIL {type(e).__name__}: {str(e)[:100]}"
            bad += not status.startswith("ok")
            print(f"{status}  {r['repo']} {f['sha'][:9]} {f['subject'][:60]}", flush=True)
    print(f"\n{bad} problem{'s' * (bad != 1)}")
    return 1 if bad else 0


def result_path(lane, repo, sha):
    return RESULTS / slug(lane) / f"{slug(repo)}-{sha[:12]}.json"


def version(worker):
    exe = shutil.which(CLI.get(worker, worker))
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30) if exe else None
    except (OSError, subprocess.TimeoutExpired):
        out = None
    return (out.stdout or out.stderr).strip().splitlines()[0] if out and (out.stdout or out.stderr).strip() else None


def cmd_run(a):
    suite = load()
    lanes = parse_lanes(a.lanes) if a.lanes else [tuple(x) for x in suite["lanes"]]
    if any(w == "claude" for w, _ in lanes) and "claude" not in workers.ENABLED:
        workers.use_main_claude()
    missing = sorted({w for w, _ in lanes} - set(workers.ENABLED))
    if missing:
        sys.exit(f"not ready here: {', '.join(missing)} (farmout doctor says why)")
    versions = {w: version(w) for w in {w for w, _ in lanes}}
    todo = []
    for r in suite["repos"]:
        for fix in r.get("fixes", []) if not a.only or r["repo"] in a.only else []:
            pending = []
            for w, m in lanes:
                p = result_path(lane_name(w, m), r["repo"], fix["sha"])
                old = json.loads(p.read_text()) if p.exists() else None
                if not old or (a.retry_failed and old["failed"]):
                    pending.append((w, m))
            if pending:
                todo.append((r["repo"], fix, pending))
    todo = todo[:a.max] if a.max else todo  # --max counts fixes that still need a review
    day, done = time.strftime("%Y-%m-%d"), 0
    for i, (repo, fix, pending) in enumerate(todo, 1):
        top = ready(repo, fix["sha"])
        while pending:  # one review per round: a worker on two models takes two, and so does a lane that can't go offline
            rnd = []
            for w, m in pending:
                if w not in {x for x, _ in rnd} and all(workers.WORKERS[w].web_off == workers.WORKERS[x].web_off for x, _ in rnd):
                    rnd.append((w, m))
            pending = [x for x in pending if x not in rnd]
            offline = workers.WORKERS[rnd[0][0]].web_off  # no web search: the fixes are public, the answers a search away
            print(f"[{i}/{len(todo)}] {repo} {fix['sha'][:9]} {fix['subject'][:60]}  ({', '.join(lane_name(*x) for x in rnd)})",
                  flush=True)
            try:
                row = bench.replay(top, fix, [w for w, _ in rnd], sessions.new_group("board"),
                                   models={w: m for w, m in rnd if m}, fresh=True,
                                   settings={"web": False} if offline else None)
            except Exception as e:  # noqa: BLE001 (one bad fix mustn't end a long run)
                row = {"found": {}, "failed": {w: f"replay failed: {type(e).__name__}: {e}"[:300] for w, _ in rnd},
                       "findings": {}, "worker_secs": {}, "replies": {}}
            for w, m in rnd:
                p = result_path(lane_name(w, m), repo, fix["sha"])
                p.parent.mkdir(parents=True, exist_ok=True)
                failed = row["failed"].get(w)
                if not failed and w not in row["found"]:
                    failed = "no review ran (an empty change?)"
                p.write_text(json.dumps({
                    "repo": repo, "sha": fix["sha"], "subject": fix["subject"], "lane": lane_name(w, m), "worker": w,
                    "model": m, "cli": versions.get(w), "farmout": VERSION, "date": day, "web": not offline,
                    "found": None if failed else row["found"].get(w, False), "failed": failed,
                    "secs": row["worker_secs"].get(w), "findings": row["findings"].get(w, []),
                    "reply": row["replies"].get(w)}, indent=1) + "\n")
                mark = "x" if failed else "✓" if row["found"].get(w) else "·"
                print(f"    {mark} {lane_name(w, m)}" + (f"  failed: {failed[:120]}" if failed else ""), flush=True)
                done += 1
    print(f"\n{done} new result{'s' * (done != 1)}. Next: python3 bench/leaderboard.py render")


def results():
    return [json.loads(p.read_text()) for p in sorted(RESULTS.glob("*/*.json"))]


def plural(n, word, suffix="s"):
    return f"{n} {word}{suffix * (n != 1)}"


def cmd_rescore(a):
    """Score saved replies again with today's parser and path matching: a scoring fix costs no quota."""
    import tempfile
    fixes = {(r["repo"], f["sha"]): f for r in load()["repos"] for f in r.get("fixes", [])}
    by_fix = {}
    for p in sorted(RESULTS.glob("*/*.json")):
        r = json.loads(p.read_text())
        if r.get("reply") and not r["failed"] and (r["repo"], r["sha"]) in fixes:
            by_fix.setdefault((r["repo"], r["sha"]), []).append((p, r))
    flipped = 0
    for (repo, sha), items in by_fix.items():
        with tempfile.TemporaryDirectory(prefix="farmout-board-") as tmp:
            wt = Path(tmp) / "wt"
            bench.prepare(ready(repo, sha), fixes[(repo, sha)], wt, fresh=True)
            changed = bench.changed_lines(str(wt))
            known = [k for k in git(wt, "ls-files", "-z").split("\0") if k]
            for p, r in items:
                found = review.parse(r["reply"], r["worker"])
                for f in found:
                    f["rel"] = review.resolve(str(wt), f["path"], list(changed), known)
                hit = bench.scored({"clusters": [{"findings": found}]}, changed, r["worker"])
                if hit != r["found"]:
                    flipped += 1
                    print(f"{'✓' if hit else '·'} {r['lane']} {repo} {sha[:9]} (was {'✓' if r['found'] else '·'})")
                r["found"] = hit
                r["findings"] = [{k: f.get(k) for k in ("rel", "path", "line", "severity", "defect")} for f in found]
                p.write_text(json.dumps(r, indent=1) + "\n")
    print(f"{plural(flipped, 'score')} changed. Next: python3 bench/leaderboard.py render")


def lang_of(suite):
    return {r["repo"]: r["lang"] for r in suite["repos"]}


def board(rows):
    """[(lane, found, attempted, failed, findings per fix, median secs)] best first; failed runs don't count against."""
    out = []
    for lane in sorted({r["lane"] for r in rows}):
        mine = [r for r in rows if r["lane"] == lane]
        ok = [r for r in mine if not r["failed"]]
        secs = sorted(r["secs"] for r in ok if r.get("secs") is not None)
        out.append((lane, sum(bool(r["found"]) for r in ok), len(ok), len(mine) - len(ok),
                    round(sum(len(r["findings"]) for r in ok) / len(ok), 1) if ok else 0,
                    secs[len(secs) // 2] if secs else None))
    return sorted(out, key=lambda b: (-(b[1] / b[2] if b[2] else 0), -b[1], b[0]))


def cmd_render(a):
    suite, rows = load(), results()
    if not rows:
        sys.exit("no results yet: python3 bench/leaderboard.py run")
    langs, b = lang_of(suite), board(rows)
    fixes = [(r["repo"], f) for r in suite["repos"] for f in r.get("fixes", [])]
    lanes = [x[0] for x in b]
    seen = {(r["lane"], r["repo"], r["sha"]): r for r in rows}
    lines = ["# Bug-Finding Leaderboard", "",
             f"Recent real bug fixes from {len({r for r, _ in fixes})} open-source repositories, undone one at a time; "
             "each lane (a farmout worker, on its default model or a named one) reviews the result with farmout's review "
             "brief and scores when a finding points within 3 lines of the undone change. Generated by "
             "`bench/leaderboard.py render` from the files in `results/`; method and caveats below.", "",
             "| lane | found | rate | failed | findings per fix | median time |", "|---|---|---|---|---|---|"]
    web = {r["lane"] for r in rows if r.get("web")}  # lanes whose CLI couldn't turn web search off
    for lane, found, of, failed, per, secs in b:
        name = f"`{lane}`" + (" (web)" if lane in web else "")
        lines.append(f"| {name} | {found}/{of} | {found / of:.0%} | {failed} | {per} | "
                     f"{f'{secs:.0f}s' if secs is not None else '-'} |" if of else f"| {name} | 0/0 | - | {failed} | - | - |")
    by_lang = sorted({langs.get(r["repo"], "?") for r in rows})
    lines += ["", "## By language", "", "| lane | " + " | ".join(by_lang) + " |", "|---|" + "---|" * len(by_lang)]
    for lane in lanes:
        cells = []
        for lang in by_lang:
            ok = [r for r in rows if r["lane"] == lane and langs.get(r["repo"]) == lang and not r["failed"]]
            cells.append(f"{sum(bool(r['found']) for r in ok)}/{len(ok)}" if ok else "-")
        lines.append(f"| `{lane}` | " + " | ".join(cells) + " |")
    lines += ["", "## Every fix", "", "✓ found, · missed, x failed, blank: not run", "",
              "| repository | fix | " + " | ".join(f"`{x}`" for x in lanes) + " |", "|---|---|" + "---|" * len(lanes)]
    for repo, f in fixes:
        cells = []
        for lane in lanes:
            r = seen.get((lane, repo, f["sha"]))
            cells.append("" if not r else "x" if r["failed"] else "✓" if r["found"] else "·")
        if any(cells):
            subject = f["subject"].replace("|", "\\|")[:70]
            lines.append(f"| {repo} | [{f['sha'][:9]}](https://github.com/{repo}/commit/{f['sha']}) {subject} | "
                         + " | ".join(cells) + " |")
    clis = sorted({(r["worker"], r["cli"]) for r in rows if r.get("cli")})
    lines += ["", "## Method", "",
              "- **Fixes:** picked by `leaderboard.py pick` (a fix-like subject; 1-80 changed lines in at most 4 source "
              "files; besides those only tests and docs may change, nothing added, deleted or renamed; chores skipped), "
              "then screened by hand: picks that weren't bugs in the project's own code are in each repository's `skip` "
              "list. Recent fixes, so fewer of them are in any model's training data; each fix's date is in `suite.json`.",
              "- **What a lane sees:** a fresh repository with the fixed tree as its only commit (\"snapshot\"), and the "
              "fix's source changes undone as an uncommitted change. Tests, docs and changelog stay as fixed, so the diff "
              "never deletes a regression test that names the bug, and `git log` can't show the fix.",
              "- **Scoring:** a lane finds the bug when one of its findings cites a changed line, within 3 lines. That can "
              "credit a right line with the wrong reason, or miss a right reason on the wrong line; every finding is in "
              "`results/` to check. *Findings per fix* is how much a lane reports: more findings, more chances to hit.",
              "- **No web search** where the CLI can turn it off (the fixes are public, so a search could find the answer). "
              "Antigravity can't, so its lanes had web search available, marked (web) above; each result records `web`.",
              "- **Failed** runs (a usage limit, a crash) are re-run with `run --retry-failed` and don't count as misses.",
              "- **CLIs:** " + ", ".join(f"{w} {v}" for w, v in clis) + f"; farmout {VERSION}.",
              "", f"Rerun: `python3 bench/leaderboard.py run -l <lanes>` then `render`. Last rendered "
              f"{time.strftime('%Y-%m-%d')}.", ""]
    (BOARD / "README.md").write_text("\n".join(lines))
    summary = [{"lane": lane, "found": found, "attempted": of, "failed": failed, "findings_per_fix": per,
                "median_secs": secs} for lane, found, of, failed, per, secs in b]
    (BOARD / "leaderboard.json").write_text(json.dumps({"rendered": time.strftime("%Y-%m-%d"), "fixes": len(fixes),
                                                        "board": summary}, indent=1) + "\n")
    cards = [(lane, found, of, failed) for lane, found, of, failed, *_ in b]
    svg = BOARD / "card.svg"
    svg.write_text(bench.card_svg(cards, sum(1 for _ in {(r["repo"], r["sha"]) for r in rows}), time.strftime("%Y-%m-%d"),
                                  title="Which AI finds real bugs?",
                                  subtitle=f"{plural(len({(r['repo'], r['sha']) for r in rows}), 'recent bug fix', 'es')} from "
                                           f"{plural(len({r['repo'] for r in rows}), 'open-source project')}, undone, then reviewed",
                                  label="farmout leaderboard"))
    png = bench.png(svg)
    print(f"wrote {BOARD / 'README.md'}, leaderboard.json, {svg.name}" + (f", {png.name}" if png else ""))
    for lane, found, of, failed, per, secs in b:
        print(f"  {lane:<32} {found}/{of}" + (f"  ({failed} failed)" if failed else ""))


def main(argv=None):
    p = argparse.ArgumentParser(prog="bench/leaderboard.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pick", help="choose recent fixes for every repository without any (or --refresh)")
    pk.add_argument("--per-repo", type=int, default=5)
    pk.add_argument("--since", default="2026-08-01", help="only fixes committed since this date")
    pk.add_argument("--refresh", action="store_true", help="re-pick repositories that already have fixes")
    pk.add_argument("--only", action="append", metavar="OWNER/REPO", help="only this repository (repeatable)")
    rn = sub.add_parser("run", help="review every fix that a lane hasn't done yet")
    rn.add_argument("-l", "--lanes", help="worker[:model],... (default: suite.json's lanes)")
    rn.add_argument("--only", action="append", metavar="OWNER/REPO", help="only this repository (repeatable)")
    rn.add_argument("--max", type=int, help="at most this many fixes")
    rn.add_argument("--retry-failed", action="store_true", help="run failed results again")
    sub.add_parser("check", help="build every fix's scratch copy without a review (no quota)")
    sub.add_parser("rescore", help="score saved replies again with today's parser (no quota)")
    sub.add_parser("render", help="README.md, leaderboard.json and the card, from results/")
    a = p.parse_args(argv)
    return {"pick": cmd_pick, "check": cmd_check, "run": cmd_run, "rescore": cmd_rescore, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
