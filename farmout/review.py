"""`farmout review`: every worker reviews the change in parallel; findings are merged across models with
agreement counts, and any finding citing a file or line that doesn't exist is dropped before you see it."""
import os
import re
import subprocess
from pathlib import Path

from . import flows, sessions
from .config import repo_root
from .isolate import git

# The same brief as skills/review/SKILL.md (a test keeps them identical).
BRIEF = """Review a code change for defects. The unified diff is in the first listed file; any other listed files are new, untracked files. Read any repository file you need for context; change nothing. Report only problems worth blocking a merge: bugs, crashes, security holes, data loss, races, broken edge cases, wrong behaviour. No style or naming nits, no praise, no summary. One finding per line, exactly, starting with the file's real path from the repository root:
path/to/file.py:42 | high/medium/low | defect | input or state that triggers it | fix
The line is the line number in the file as it is now (open the file to check it), not a position in the diff.
At most 20 findings, most severe first. If there are none, reply exactly: NO FINDINGS"""
MODELS = {"antigravity": "gemini-3.8-flash-low"}  # 22 of 23 planted bugs in ~20s, vs 3+ minutes on the default
SEVERITY = {"low": 1, "medium": 2, "high": 3}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ROW = re.compile(r"^(?P<path>[^\s|:]+):(?P<line>\d+)(?:[-–]\d+)?\s*\|\s*(?P<sev>high|medium|low)\b\s*\|?\s*(?P<rest>.*)$", re.I)
NEAR = 3  # findings further apart than this, in one file, are never the same defect


def capture(top, group, base=None, staged=False, since=None):
    """Save the change under review to .git/farmout/<group>.diff. Returns (diff path, untracked files, label)."""
    if base and base.startswith("-"):
        raise ValueError("--base takes a git ref, not an option")
    if since:  # a worktree's changes since its base commit (pipelines)
        diff, label = git(top, "diff", "--no-color", since), "the worktree's changes"
    elif staged:
        diff, label = git(top, "diff", "--cached", "--no-color"), "staged changes"
    elif base:
        diff, label = git(top, "diff", "--no-color", f"{base}...HEAD"), f"{base}...HEAD"
    else:
        head = subprocess.run(["git", "-C", top, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0
        diff, label = git(top, "diff", "--no-color", "HEAD" if head else EMPTY_TREE), "uncommitted changes"
    untracked = [] if (staged or base) else [
        f for f in git(top, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
    out = Path(git(top, "rev-parse", "--absolute-git-dir").strip()) / "farmout" / f"{group}.diff"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff, errors="surrogateescape")
    return out, [str(Path(top, f)) for f in untracked], label


def parse(text, worker):
    """Findings in `path:line | severity | defect | trigger | fix` lines, tolerating bullets, backticks and tables."""
    out = []
    for raw in text.splitlines():
        s = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", raw.strip().strip("|").strip()).replace("`", "").replace("**", "")
        m = ROW.match(s)
        if m:
            defect, trigger, fix = ([p.strip() for p in m["rest"].split("|")] + ["", "", ""])[:3]
            out.append({"worker": worker, "path": m["path"], "line": int(m["line"]), "severity": m["sev"].lower(),
                        "defect": defect, "trigger": trigger, "fix": fix})
    return out


PLACEHOLDER = {"path", "file", "filename", "path/to/file", "path/to/file.py", "<path>", "<file>"}


def resolve(root, p, changed=(), known=()):
    """The repo-relative path a finding cites, if it names a file inside root; else None. Models also cite paths
    relative to a subdirectory (`cli.py` for `pkg/cli.py`): a unique suffix match among the changed files, then
    among all of the repository's files, counts. A model that copies the brief's placeholder (`path:163`) means the
    changed file, when there's only one."""
    if p.lower() in PLACEHOLDER:
        return changed[0] if len(set(changed)) == 1 else None
    root = Path(root).resolve()
    for cand in [p] + ([p[2:]] if p.startswith(("a/", "b/")) else []):
        q = Path(cand) if os.path.isabs(cand) else root / cand
        try:
            rel = q.resolve().relative_to(root)
        except ValueError:
            continue
        if q.is_file():
            return str(rel)
    tail = re.sub(r"^(?:\./)+", "", p)
    if os.path.isabs(tail) or tail.startswith(".."):
        return None
    for pool in (changed, known):
        hits = [k for k in pool if k == tail or k.endswith("/" + tail)]
        if len(hits) == 1:
            return hits[0]
    return None


STOP = {"this", "that", "with", "when", "from", "which", "will", "into", "than", "then", "there", "their", "could",
        "would", "should", "does", "doesn", "isn", "missing", "line", "code", "value", "values", "input", "error",
        "about", "after", "allows", "also", "because", "before", "causes", "causing", "instead", "other", "using",
        "where", "while", "without", "raise", "raises", "raised"}
SAME_LINE = MIN_LINK = 0.1  # see link() and group(); equal, so a shared line alone can link two findings
# Models name one bug class in different words; these fold to one token before matching.
SYNONYMS = {**dict.fromkeys(("race", "races", "racy", "racing", "concurrent", "concurrency", "concurrently", "toctou"),
                            "race"),
            **dict.fromkeys(("none", "null", "nil", "nonetype", "nullptr"), "none")}
SUFFIXES = ("ations", "ation", "ions", "ion", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s", "e")


def stem(w):
    """A crude stem, enough for `crashes`/`crash`, `deletions`/`deleted` and `committed`/`commit` to match."""
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            if suf in ("ed", "ing", "ings") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
                w = w[:-1]  # committ -> commit
            return w
    return w


def words(text):
    """The distinctive words of a finding: lowercased, identifiers split at underscores, stopwords dropped,
    synonyms folded and the rest stemmed."""
    return {SYNONYMS.get(w) or stem(w) for w in re.findall(r"[a-z][a-z0-9]{3,}", text.lower()) if w not in STOP}


def weights(fs, bags):
    """How much sharing a word says about two findings being one defect. Each model reports a defect once, so a
    word one model uses in several of its findings (file, owner, quota) describes the code, not a defect. The
    weight is the share of the word's uses that come from different models, squared, so such words count for
    little: 1 when every model that uses it does so once, 0.36 for three models across five findings."""
    uses = {}
    for f, bag in zip(fs, bags):
        for w in bag:
            per = uses.setdefault(w, {})
            per[f["worker"]] = per.get(f["worker"], 0) + 1
    return {w: (len(per) / sum(per.values())) ** 2 for w, per in uses.items()}


def link(a, b, bag_a, bag_b, weight, near=NEAR):
    """How alike two findings from different models are, or None when they can't be the same defect: more than
    `near` lines apart, on different lines without sharing half a word's worth of weight (words that name this
    defect rather than the code around it), or on one line with nothing in common, not even the severity. The
    score is the weighted overlap of their words (Dice: shared weight against both findings' weight, so a short
    finding sharing one common word doesn't look identical), plus a little for citing the same line. On one
    line, models describe one bug in very different words, so the line alone can link two findings of the same
    severity. The constants come from four real four-model runs, each grouped by hand (see the tests)."""
    gap = abs(a["line"] - b["line"])
    if a["worker"] == b["worker"] or gap > near:
        return None
    shared = sum(weight[w] for w in bag_a & bag_b)
    if gap and shared < 0.5:
        return None
    if not gap and not shared and a["severity"] != b["severity"]:
        return None  # one line, nothing in common, not even the severity: two defects that happen to share a line
    size = sum(weight[w] for w in bag_a) + sum(weight[w] for w in bag_b)
    return (2 * shared / size if size else 0) + SAME_LINE * (gap == 0)


def group(fs, near=NEAR):
    """Cluster one file's findings: lists of indices into fs. Average linkage, best pair first, so on a line with
    several defects each finding goes with its closest match rather than with whichever came first. Two groups
    join only if no model would count twice, they stay within `near` lines, and their findings are alike on
    average (pairs that can't be one defect count as 0) by at least MIN_LINK."""
    bags = [words(f["defect"]) for f in fs]
    weight = weights(fs, bags)
    scores = {}
    for i in range(len(fs)):
        for j in range(i + 1, len(fs)):
            s = link(fs[i], fs[j], bags[i], bags[j], weight, near)
            if s is not None:
                scores[i, j] = s
    of, members = list(range(len(fs))), {i: [i] for i in range(len(fs))}
    while True:
        sums = {}
        for (i, j), s in scores.items():
            pair = (min(of[i], of[j]), max(of[i], of[j]))
            if pair[0] != pair[1]:
                sums[pair] = sums.get(pair, 0) + s
        best = None
        for (a, b), s in sorted(sums.items()):
            ma, mb = members[a], members[b]
            lines = [fs[k]["line"] for k in ma + mb]
            avg = s / (len(ma) * len(mb))
            if ({fs[k]["worker"] for k in ma} & {fs[k]["worker"] for k in mb} or max(lines) - min(lines) > near
                    or avg < MIN_LINK):
                continue
            if best is None or avg > best[0]:
                best = (avg, a, b)
        if best is None:
            return list(members.values())
        _, a, b = best
        for k in members[b]:
            of[k] = a
        members[a] += members.pop(b)


def merge(found, near=NEAR):
    """Group findings that point at the same defect in one file; each model counts once per group. A group shows
    its most severe finding, at the line most of its most severe findings cite."""
    order, files, clusters = {}, {}, []
    for f in found:
        order.setdefault(f["worker"], len(order))
        files.setdefault((f["rel"] or f["path"], f["rel"] is not None), []).append(f)
    for (where, valid), fs in files.items():
        for idx in group(fs, near):
            ms = sorted((fs[k] for k in idx), key=lambda f: (order[f["worker"]], f["line"]))
            top = max(SEVERITY[f["severity"]] for f in ms)
            lines = [f["line"] for f in ms if SEVERITY[f["severity"]] == top]
            line = min(set(lines), key=lambda x: (-lines.count(x), x))
            lead = next(f for f in ms if SEVERITY[f["severity"]] == top and f["line"] == line)
            clusters.append({"path": where, "line": line, "severity": lead["severity"], "defect": lead["defect"],
                             "workers": [f["worker"] for f in ms], "findings": ms, "valid": valid,
                             "words": set().union(*(words(f["defect"]) for f in ms))})
    return clusters


def check_lines(clusters, root):
    """Drop findings that cite a line past the end of the file."""
    for c in clusters:
        if c["valid"]:
            with open(Path(root, c["path"]), errors="replace") as fh:
                c["valid"] = 1 <= c["line"] <= sum(1 for _ in fh)


def ranked(clusters):
    return sorted(clusters, key=lambda c: (-len(c["workers"]), -SEVERITY[c["severity"]], c["path"], c["line"]))


def review(cwd, workers=None, base=None, staged=False, focus=None, since=None, root=None, group=None, models=None):
    """Run the review. Returns a dict: group, label, diff, workers (answered), failed {worker: reason}, clusters.
    models {worker: model} overrides the defaults (MODELS, else each CLI's own)."""
    top = str(root or repo_root(cwd) or "")
    if not top:
        raise ValueError("farmout review works on a git repository; run it inside one")
    group = group or sessions.new_group("review")
    diff, untracked, label = capture(top, group, base, staged, since)
    result = {"group": group, "label": label, "diff": str(diff), "workers": [], "failed": {}, "clusters": [],
              "sessions": [], "empty": not diff.stat().st_size and not untracked}
    if result["empty"]:
        return result
    workers = workers or flows.default_workers()
    prompt = BRIEF + (f"\nFocus: {focus}" if focus else "")
    files = [str(diff), *untracked]
    found = []
    for name, rec in flows.fan(group, workers, top, prompt, files, models={**MODELS, **(models or {})}):
        meta = sessions.load(name)
        if rec["status"] != "ok" and meta["model"] and meta["model"] == MODELS.get(meta["worker"]):
            meta["model"] = None  # an older CLI may not know the fast model: once more on its default
            sessions.save(meta)
            flows.ACTIVE.add(name)
            rec = sessions.deliver(name, prompt, files, cwd=top)
            flows.ACTIVE.discard(name)
        result["sessions"].append(name)
        w = rec.get("worker") or meta["worker"]
        if rec["status"] == "ok":
            result["workers"].append(w)
            found += parse(flows.text(rec), w)
        else:
            lines = flows.text(rec).strip().splitlines() or [rec["status"]]
            result["failed"][w] = lines[0][:300]
    changed = re.findall(r"^\+\+\+ b/(.+)$", diff.read_text(errors="replace"), re.M)
    changed += [os.path.relpath(f, top) for f in untracked]
    known = [k for k in git(top, "ls-files", "-z").split("\0") if k] + changed
    for f in found:
        f["rel"] = resolve(top, f["path"], changed, known)
    clusters = merge(found)
    check_lines(clusters, top)
    for c in clusters:
        c.pop("words", None)
    result["clusters"] = ranked(clusters)
    return result


def counts(result, min_agree=1):
    good = [c for c in result["clusters"] if c["valid"] and len(c["workers"]) >= min_agree]
    return {"findings": len(good), "agree": max((len(c["workers"]) for c in good), default=0),
            "high": sum(c["severity"] == "high" for c in good)}


def failing(result, min_agree=1, fail_on="never"):
    """Findings that should fail a gate: valid, flagged by min_agree+ models, at fail_on severity or worse."""
    if fail_on == "never":
        return []
    return [c for c in result["clusters"] if c["valid"] and len(c["workers"]) >= min_agree
            and SEVERITY[c["severity"]] >= SEVERITY[fail_on]]


def render(result, min_agree=1, width=None):
    if result["empty"]:
        return f"Nothing to review: no {result['label']}."
    n = len(result["workers"])
    good = [c for c in result["clusters"] if c["valid"] and len(c["workers"]) >= min_agree]
    lines = [f"{result['group']}: {n} reviewer{'s' * (n != 1)} ({', '.join(result['workers']) or 'none'}) "
             f"on {result['label']}"]
    if good:
        rows = [("agree", "severity", "where", "finding", "flagged by")]
        rows += [(f"{len(c['workers'])}/{n}", c["severity"], f"{c['path']}:{c['line']}", c["defect"], ", ".join(c["workers"]))
                 for c in good]
        # the finding column takes what the others leave of the terminal, so no row wraps
        fixed = sum(max(len(r[i]) for r in rows) for i in (0, 1, 2, 4)) + 8
        cap = max(24, (width or 120) - fixed - 1)
        rows = [(a, b, c, d if len(d) <= cap else d[:cap - 1] + "…", e) for a, b, c, d, e in rows]
        widths = [max(len(r[i]) for r in rows) for i in range(4)]
        lines += ["  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4] for r in rows]
    else:
        lines.append("No findings" + (f" flagged by {min_agree}+ models." if min_agree > 1 else "."))
    bad = [c for c in result["clusters"] if not c["valid"]]
    if bad:
        lines.append(f"Dropped {len(bad)} citing a file or line that doesn't exist: " +
                     "; ".join(f"{c['path']}:{c['line']} ({', '.join(c['workers'])})" for c in bad))
    for w, why in result["failed"].items():
        lines.append(f"{w} failed: {why}")
    lines.append(f"Follow up with a reviewer: farmout send {result['group']}-<worker> \"...\"")
    return "\n".join(lines)
