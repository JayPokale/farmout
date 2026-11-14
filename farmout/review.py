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
BRIEF = """Review a code change for defects. The unified diff is in the first listed file; any other listed files are new, untracked files. Read any repository file you need for context; change nothing. Report only problems worth blocking a merge: bugs, crashes, security holes, data loss, races, broken edge cases, wrong behaviour. No style or naming nits, no praise, no summary. One finding per line, exactly:
path:line | high/medium/low | defect | input or state that triggers it | fix
The line is the line number in the file as it is now (open the file to check it), not a position in the diff.
At most 20 findings, most severe first. If there are none, reply exactly: NO FINDINGS"""
MODELS = {"antigravity": "gemini-3.8-flash-low"}  # 22 of 23 planted bugs in ~20s, vs 3+ minutes on the default
SEVERITY = {"low": 1, "medium": 2, "high": 3}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ROW = re.compile(r"^(?P<path>[^\s|:]+):(?P<line>\d+)(?:[-–]\d+)?\s*\|\s*(?P<sev>high|medium|low)\b\s*\|?\s*(?P<rest>.*)$", re.I)
NEAR = 3  # findings within this many lines of each other, in one file, count as the same defect


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


def resolve(root, p, changed=(), known=()):
    """The repo-relative path a finding cites, if it names a file inside root; else None. Models also cite paths
    relative to a subdirectory (`cli.py` for `pkg/cli.py`): a unique suffix match among the changed files, then
    among all of the repository's files, counts."""
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
        "would", "should", "does", "doesn", "isn", "missing", "line", "code", "value", "values", "input", "error"}


def words(text):
    return {w for w in re.findall(r"[a-z][a-z0-9_]{3,}", text.lower()) if w not in STOP}


def same_defect(f, c, near):
    """Two models describe the same defect when they cite the same line, or nearby lines (within `near`) with the
    same severity or a shared distinctive word; that keeps a style note from inflating a real bug's agreement."""
    gap = abs(c["line"] - f["line"])
    return gap == 0 or (gap <= near and (f["severity"] == c["severity"] or bool(words(f["defect"]) & c["words"])))


def merge(found, near=NEAR):
    """Group findings that point at the same defect in one file; each model counts once per group."""
    clusters = []
    for f in sorted(found, key=lambda f: (f["rel"] or f["path"], -SEVERITY[f["severity"]], f["line"])):
        where = f["rel"] or f["path"]
        near_ones = [c for c in clusters if c["path"] == where and f["worker"] not in c["workers"]
                     and same_defect(f, c, near)]
        c = min(near_ones, key=lambda c: abs(c["line"] - f["line"]), default=None)
        if c is None:
            c = {"path": where, "line": f["line"], "severity": f["severity"], "defect": f["defect"], "workers": [],
                 "findings": [], "valid": f["rel"] is not None, "words": set()}
            clusters.append(c)
        c["workers"].append(f["worker"])
        c["findings"].append(f)
        c["words"] |= words(f["defect"])
        if SEVERITY[f["severity"]] > SEVERITY[c["severity"]]:
            c["severity"], c["defect"] = f["severity"], f["defect"]
    return clusters


def check_lines(clusters, root):
    """Drop findings that cite a line past the end of the file."""
    for c in clusters:
        if c["valid"]:
            with open(Path(root, c["path"]), errors="replace") as fh:
                c["valid"] = 1 <= c["line"] <= sum(1 for _ in fh)


def ranked(clusters):
    return sorted(clusters, key=lambda c: (-len(c["workers"]), -SEVERITY[c["severity"]], c["path"], c["line"]))


def review(cwd, workers=None, base=None, staged=False, focus=None, since=None, root=None, group=None):
    """Run the review. Returns a dict: group, label, diff, workers (answered), failed {worker: reason}, clusters."""
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
    for name, rec in flows.fan(group, workers, top, prompt, files, models=MODELS):
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
        cap = max(30, (width or 120) - 60)
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
