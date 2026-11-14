"""Git worktree isolation. A writer gets a private worktree under <repo>/.farmout/worktrees/: HEAD plus your
uncommitted and untracked files (committed as its base) plus any ignored files .farmoutinclude names. Its result is
a patch of only its own changes, so parallel writers can't collide and your tree stays untouched."""
import fcntl
import fnmatch
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

CACHES = ("__pycache__", ".pyc", ".pytest_cache", "node_modules")
EXCLUDE = "/.farmout/worktrees/"
QUIET = {**os.environ, "FARMOUT_SKIP": "1"}  # farmout's own git hook must not review farmout's internal commits


def git(cwd, *args, stdin=None, binary=False, env=None):
    """git's stdout. binary=True keeps bytes as they are: diffs that must apply again keep their CRLFs."""
    p = subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, env=env,
                       **({} if binary else {"text": True, "errors": "surrogateescape"}))
    if p.returncode:
        err = p.stderr.decode(errors="replace") if binary else p.stderr
        raise RuntimeError(f"git {' '.join(args[:2])}: {err.strip()[-500:]}")
    return p.stdout


@contextmanager
def repo_lock(top):
    """One farmout process at a time adds or removes worktrees in a repository (git's own lock files and
    .git/info/exclude would race otherwise); sessions run in separate processes, so a thread lock isn't enough."""
    common = Path(top, git(top, "rev-parse", "--git-common-dir").strip())
    with open(common / "farmout.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield common
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def included(top):
    """Ignored paths to copy into every worktree: .farmoutinclude holds glob patterns, one per line, matched
    against the repo-relative path (or the file name, for a pattern without a slash), e.g. `.env` or `config/*.local`."""
    f = Path(top, ".farmoutinclude")
    pats = [p.strip().rstrip("/") for p in f.read_text().splitlines()] if f.exists() else []
    pats = [p for p in pats if p and not p.startswith("#")]
    if not pats:
        return []
    found = git(top, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z").split("\0")
    out = []
    for entry in (e.rstrip("/") for e in found if e and not e.startswith(".farmout/")):
        if any(fnmatch.fnmatch(entry, p) or ("/" not in p and fnmatch.fnmatch(os.path.basename(entry), p)) for p in pats):
            out.append(entry)
    return out


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def make(cwd, label, branch):
    """A new worktree at <top>/.farmout/worktrees/<label> on a new branch. Returns its info dict."""
    cwd = os.path.realpath(os.path.expanduser(cwd or os.getcwd()))
    top = git(cwd, "rev-parse", "--show-toplevel").strip()
    wt = Path(top, ".farmout", "worktrees", label)
    with repo_lock(top) as common:
        exclude = common / "info" / "exclude"
        text = exclude.read_text() if exclude.exists() else ""
        if EXCLUDE not in text:  # keep git status clean; .farmout/settings and pipelines stay committable
            exclude.parent.mkdir(exist_ok=True)
            exclude.write_text(text + f"\n{EXCLUDE}\n")
        git(top, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
        base = git(top, "diff", "HEAD", "--binary", binary=True)
        untracked = [f for f in git(top, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
        extra = included(top)
    try:
        if base:
            git(wt, "apply", "--whitespace=nowarn", stdin=base, binary=True)
        for f in untracked:
            copy(Path(top) / f, wt / f)
        if base or untracked:
            git(wt, "add", "-A")
            git(wt, "-c", "user.name=farmout", "-c", "user.email=farmout@localhost", "commit", "-q", "--no-verify",
                "-m", "your uncommitted changes", env=QUIET)
        for f in extra:  # ignored, so they stay out of the base commit and of the patch
            copy(Path(top) / f, wt / f)
    except Exception:  # don't leave a half-made worktree and branch behind
        remove({"top": top, "worktree": str(wt), "branch": branch})
        raise
    return {"top": top, "worktree": str(wt), "branch": branch, "base": git(wt, "rev-parse", "HEAD").strip(),
            "patch": str(wt.parent / f"{wt.name}.patch"), "rel": os.path.relpath(cwd, top)}


def rewrite(text, top, wt):
    """Point mentions of the repo's path at the worktree: whole path components only (not /repo2), and not paths
    that are already inside a worktree."""
    pat = re.escape(top) + r"(?=$|[/\s'\"`)\]>,;:]|\.(?:\s|$))(?!/\.farmout/worktrees/)"
    return re.sub(pat, lambda m: wt, text)


def enter(t, info):
    """The task, moved into the worktree: cwd, in-repo `files` (ignored ones copied in) and any mention of the
    repo's path in the brief, which would otherwise send writers to the user's tree."""
    top, wt = info["top"], Path(info["worktree"])
    cwd = os.path.realpath(os.path.expanduser(t.get("cwd") or os.getcwd()))
    files = []
    for f in t.get("files") or []:
        src = os.path.realpath(os.path.join(cwd, os.path.expanduser(f)))
        rel = os.path.relpath(src, top)
        inside = (os.path.exists(src) and rel.split(os.sep)[0] not in ("..", ".git")
                  and not src.startswith(str(wt.parent) + os.sep))
        if inside and not (wt / rel).exists():  # an ignored file the task needs: copy it in too
            copy(src, wt / rel)
        files.append(str(wt / rel) if inside else src)
    return {**t, "cwd": str(wt / info.get("rel", ".")), "files": files, "write": True,
            "prompt": rewrite(t["prompt"], top, str(wt))}


def patch(info):
    """Write the worktree's changes since its base to info["patch"] (bytes as they are); returns a diffstat."""
    wt, base = info["worktree"], info["base"]
    git(wt, "add", "-A", "--", ".", *(f":!*{c}*" for c in CACHES))
    Path(info["patch"]).write_bytes(git(wt, "diff", "--cached", "--binary", base, binary=True))
    return git(wt, "diff", "--cached", "--stat", base).strip() or "no changes"


def shortstat(info):
    """(files changed, lines added, lines removed) since the base."""
    out = git(info["worktree"], "diff", "--cached", "--numstat", info["base"])
    rows = [line.split("\t") for line in out.splitlines() if line]
    return len(rows), sum(int(a) for a, *_ in rows if a.isdigit()), sum(int(r[1]) for r in rows if r[1].isdigit())


def remove(info):
    with repo_lock(info["top"]):
        for args in (["worktree", "remove", "--force", info["worktree"]], ["branch", "-D", info["branch"]]):
            subprocess.run(["git", "-C", info["top"], *args], capture_output=True)
    if info.get("patch"):
        Path(info["patch"]).unlink(missing_ok=True)
