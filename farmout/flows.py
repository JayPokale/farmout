"""Multi-worker flows on top of sessions: fan-out, ask, race, and the patch commands (diff, apply, pr)."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import isolate, proc, sessions
from .workers import ENABLED

ACTIVE = set()  # sessions this process is waiting on; the CLI stops them on Ctrl-C
RACE = ("\n\nMake the smallest change that does this, in the current directory. No unrelated edits and no new "
        "dependencies. Run the project's tests if it has any. Reply with a short summary of what you changed.")


def default_workers(skip=("api",)):
    return [w for w in ENABLED if w not in skip]


def fan(group, workers, cwd, prompt, files=(), isolate=False, models=None, timeout=None, names=None, write=False,
        effort=None, fallback=None):
    """One session per worker in a group, all started at once; waits for every turn. prompt may be one brief for
    all, or a list with one per worker. Returns [(session name, turn record)] in order."""
    prompts = prompt if isinstance(prompt, list) else [prompt] * len(workers)
    metas = []
    try:
        for i, w in enumerate(workers):
            metas.append(sessions.create(w, cwd, name=(names or {}).get(i) or f"{group}-{w}", isolate=isolate,
                                         write=write, model=(models or {}).get(w), effort=effort, group=group,
                                         fallback=fallback))
            ACTIVE.add(metas[-1]["name"])
        msgs = [sessions.deliver(m["name"], p, files, cwd=cwd, wait=False) for m, p in zip(metas, prompts)]
        return [(m["name"], sessions.wait_msg(m["name"], msg["id"], timeout)) for m, msg in zip(metas, msgs)]
    except BaseException:  # Ctrl-C, a timeout, a bad file: don't leave the others working (and writing) on
        for m in metas:
            sessions.stop(m["name"])
        raise
    finally:
        ACTIVE.difference_update(m["name"] for m in metas)


def text(rec):
    """A turn's output, or its error."""
    if rec.get("output") and os.path.exists(rec["output"]):
        return Path(rec["output"]).read_text()
    return rec.get("error") or rec["status"]


def ask(cwd, prompt, workers, files=(), group=None):
    group = group or sessions.new_group("ask")
    return group, fan(group, workers, cwd, prompt, files)


def race(cwd, task, workers, test=None, timeout=None, group=None, files=()):
    """Every worker attempts the task in its own worktree; your test command picks the winner (then the smallest
    diff, then the fastest). Returns (group, rows, winner row or None)."""
    group = group or sessions.new_group("race")
    brief = task + (f"\n\nSuccess means this command passes: {test}" if test else "") + RACE
    rows = []
    for name, rec in fan(group, workers, cwd, brief, files, isolate=True, timeout=timeout):
        iso = sessions.load(name).get("iso")
        row = {"session": name, "worker": rec.get("worker"), "ok": rec["status"] == "ok", "secs": rec.get("secs"),
               "files": 0, "added": 0, "removed": 0, "tests": None, "test_output": "", "patch": rec.get("patch"),
               "error": None if rec["status"] == "ok" else text(rec)[:300]}
        if row["ok"] and iso:
            row["files"], row["added"], row["removed"] = isolate.shortstat(iso)
            if test and row["files"]:
                row["tests"], row["test_output"] = proc.shell(test, os.path.join(iso["worktree"], iso.get("rel", ".")))
        rows.append(row)
    contenders = [r for r in rows if r["ok"] and r["files"] and (r["tests"] if test else True)]
    winner = min(contenders, key=lambda r: (r["added"] + r["removed"], r["secs"] or 0), default=None)
    return group, rows, winner


def current_patch(name):
    meta = sessions.load(name)
    iso = meta.get("iso")
    if not iso:
        raise ValueError(f"{name} has no worktree; only isolated sessions (--isolate, race) make patches")
    if not os.path.isdir(iso["worktree"]):
        raise ValueError(f"{name}'s worktree is gone: {iso['worktree']}")
    stat = isolate.patch(iso)
    return Path(iso["patch"]), stat, meta


def apply(name):
    """Apply an isolated session's patch to the tree it came from."""
    patch, stat, meta = current_patch(name)
    if not patch.read_text(errors="surrogateescape").strip():
        return "no changes to apply"
    top = meta["iso"]["top"]
    plain = subprocess.run(["git", "-C", top, "apply", "--whitespace=nowarn", str(patch)], capture_output=True, text=True)
    if plain.returncode == 0:
        return f"applied {name}'s patch to {top}:\n{stat}"
    merged = subprocess.run(["git", "-C", top, "apply", "--3way", "--whitespace=nowarn", str(patch)], capture_output=True, text=True)
    if merged.returncode:
        raise RuntimeError(f"the patch doesn't apply to {top}: {(plain.stderr or merged.stderr).strip()[-800:]}")
    return f"applied {name}'s patch to {top} with a 3-way merge; check for conflict markers:\n{stat}"


def pull_request(name):
    """Commit the patch on a new branch from HEAD, push it and open a pull request with `gh`; returns its URL."""
    patch, _, meta = current_patch(name)
    top, branch = meta["iso"]["top"], f"farmout/pr-{name}"
    if not patch.read_text(errors="surrogateescape").strip():
        raise ValueError(f"{name} has no changes to propose")
    if subprocess.run(["git", "-C", top, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"], capture_output=True).returncode == 0:
        raise ValueError(f"branch {branch} already exists (an earlier pull request?); `git branch -D {branch}` first")
    tmp = Path(tempfile.mkdtemp(prefix="farmout-pr-")) / "wt"
    isolate.git(top, "worktree", "add", "-q", "-b", branch, str(tmp), "HEAD")
    done = False
    try:
        try:
            isolate.git(tmp, "apply", "--whitespace=nowarn", str(patch))
        except RuntimeError:
            raise RuntimeError("the patch builds on uncommitted changes that a branch from HEAD lacks; commit them first") from None
        first = (sessions.turns(name) or [{"prompt": name}])[0]["prompt"].strip().splitlines()[0][:72]
        isolate.git(tmp, "add", "-A")
        isolate.git(tmp, "commit", "-qm", first, env=isolate.QUIET)  # your hooks run, farmout's review gate doesn't
        isolate.git(tmp, "push", "-u", "origin", branch)
        p = subprocess.run(["gh", "pr", "create", "--fill", "--head", branch], cwd=tmp, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(f"gh pr create: {(p.stderr or p.stdout).strip()[-800:]}")
        done = True
        return p.stdout.strip()
    finally:
        subprocess.run(["git", "-C", top, "worktree", "remove", "--force", str(tmp)], capture_output=True)
        shutil.rmtree(tmp.parent, ignore_errors=True)
        if not done:
            subprocess.run(["git", "-C", top, "branch", "-D", branch], capture_output=True)
