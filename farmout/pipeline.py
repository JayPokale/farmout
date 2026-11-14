"""Pipelines: stages that run one after another; each starts when the previous one ends and can use its output.

A pipeline is a .toml (Python 3.11+) or .json file in <repo>/.farmout/pipelines/ or ~/.config/farmout/pipelines/,
or a built-in (`fix`, `spec`). A stage either runs a skill (review, ask, race) or sends its prompt to one or more
workers; {stage} in a prompt is replaced by that stage's output file, {stage.patch} by its patch, {args} and
{name} by parameters, {item} by a `foreach` item. `gate` decides whether a finished stage passed, `on` whether a
stage runs after an earlier one failed, and `approve` pauses the run until you approve it. Each run's state lives in
~/.local/state/farmout/runs/<run>/, so a failed, stopped or paused run can be resumed.
"""
import fcntl
import operator
import os
import re
import signal
import time
from pathlib import Path

from . import flows, hooks, proc, review, sessions, skills
from .config import CONFIG, STATE, find, project_root, read
from .workers import ENABLED

RUNS = STATE / "runs"
KEYS = {"name", "prompt", "worker", "workers", "skill", "files", "write", "isolate", "model", "effort", "test", "on",
        "gate", "approve", "foreach", "in", "fallback", "focus", "max_items"}
SKILLS = ("review", "ask", "race")
GATE = re.compile(r'^\s*(?:(ok|tests)|(agree|findings|high)\s*(>=|<=|==|!=|>|<)\s*(\d+)|contains\s+"([^"]+)")\s*$', re.I)
OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, "!=": operator.ne, ">": operator.gt, "<": operator.lt}
PLACE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(\.patch)?\}")
DONE = ("ok", "skipped", "gated", "failed")

BUILTIN = {
    "fix": {
        "description": "Review your uncommitted change; fix what 2+ models agree on, in a worktree; review the fix.",
        "params": {"worker": "codex", "test": ""},
        "stage": [
            {"name": "review", "skill": "review", "gate": "agree >= 2"},
            {"name": "fix", "worker": "{worker}", "isolate": True, "test": "{test}", "gate": "tests",
             "prompt": "Fix every finding in the review at {review} that 2 or more models flagged (the agree column "
                       "shows how many). Change as little as possible and nothing unrelated. If the project has "
                       "tests, run them. Reply with what you changed, per finding."},
            {"name": "check", "skill": "review", "in": "fix", "gate": "high == 0"},
        ],
    },
    "spec": {
        "description": "Spec first: requirements, design and independent tasks, then every task built in its own "
                       "worktree in parallel, then a check of the patches together. Usage: farmout pipe run spec \"<goal>\"",
        "params": {"args": None},
        "stage": [
            {"name": "requirements", "worker": "claude",
             "prompt": "Goal: {args}\n\nFor the codebase in the current directory, write the requirements for this "
                       "goal: a numbered list of testable requirements, then any open questions. Be concise."},
            {"name": "design", "worker": "codex",
             "prompt": "Write a short technical design for the requirements in {requirements}, for the codebase in "
                       "the current directory: the components and files to change, the data flow, the risks. Be concise."},
            {"name": "tasks", "worker": "claude",
             "prompt": "Split the design in {design} into independent implementation tasks that can be built in "
                       "parallel without editing the same files. Reply with only the tasks, one per line, each "
                       "starting with '- ', at most 6."},
            {"name": "build", "foreach": "tasks", "workers": ["codex", "claude", "copilot"], "isolate": True,
             "prompt": "Implement this task: {item}\n\nThe requirements are in {requirements} and the design in "
                       "{design}. Change only what this task needs. Run the project's tests if it has any. Reply "
                       "with a short summary of the change."},
            {"name": "integrate", "worker": "codex",
             "prompt": "The patches listed in {build} were built in parallel for one goal: {args}. Read them and "
                       "report, one per line, any conflicts between patches and any bugs, as "
                       "`path:line | high/medium/low | problem`. If there are none, reply exactly: NO FINDINGS"},
        ],
    },
}


def locate(name, cwd):
    p = Path(os.path.expanduser(name))
    if p.suffix in (".toml", ".json"):
        p = p if p.is_absolute() else Path(cwd, p)
        return p if p.is_file() else None
    for d in (project_root(cwd) / ".farmout" / "pipelines", CONFIG / "pipelines"):
        f = find(d, name)
        if f:
            return f
    return None


def load(name, cwd):
    f = locate(name, cwd)
    if f:
        return validate(read(f), f.stem, str(f))
    if name in BUILTIN:
        return validate(BUILTIN[name], name, "built-in")
    raise KeyError(f"no pipeline named {name!r} (`farmout pipe ls` lists them)")


def available(cwd):
    """[(name, description, source)] for every pipeline you can run here; your files shadow built-ins."""
    found = {}
    for name, defn in BUILTIN.items():
        found[name] = (defn.get("description", ""), "built-in")
    for d in (CONFIG / "pipelines", project_root(cwd) / ".farmout" / "pipelines"):
        for f in sorted(Path(d).iterdir()) if Path(d).is_dir() else []:
            if f.suffix in (".toml", ".json"):
                try:
                    found[f.stem] = (read(f).get("description", ""), str(f))
                except Exception as e:
                    found[f.stem] = (f"(unreadable: {e})", str(f))
    return [(n, *found[n]) for n in sorted(found)]


def refs(value, full=False):
    """Placeholder names in a value: stage or parameter names, or with full=True keys like fix.patch."""
    if isinstance(value, list):
        return [r for v in value for r in refs(v, full)]
    if not isinstance(value, str):
        return []
    return [m.group(1) + ((m.group(2) or "") if full else "") for m in PLACE.finditer(value)]


def validate(defn, name, source):
    stages = defn.get("stage") or defn.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"{source}: a pipeline needs a list of stages ([[stage]] in TOML, \"stage\": [...] in JSON)")
    params = defn.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"{source}: params must be a table")
    seen, out = [], []
    for i, s in enumerate(stages, 1):
        if not isinstance(s, dict):
            raise ValueError(f"{source}: stage {i} must be a table")
        n = s.get("name") or f"stage{i}"
        where = f"{source}: stage {i} ({n})"
        bad = set(s) - KEYS
        if bad:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(bad))}; known: {', '.join(sorted(KEYS))}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n) or n in seen or n in ("args", "item") or n in params:
            raise ValueError(f"{where}: name must be a unique identifier, not a parameter, 'args' or 'item'")
        if s.get("skill") and not (s["skill"] in SKILLS or skills.NAME.fullmatch(str(s["skill"]))):
            raise ValueError(f"{where}: skill must be review, ask, race or the name of one of your skills")
        if (not s.get("skill") or s["skill"] in ("ask", "race")) and not s.get("prompt"):
            raise ValueError(f"{where}: needs a prompt" + ("" if s.get("skill") else " (or a skill)"))
        if s.get("on", "success") not in ("success", "failure", "always"):
            raise ValueError(f"{where}: on must be success, failure or always")
        if s.get("gate") and not GATE.match(s["gate"]):
            raise ValueError(f"{where}: gate must be ok, tests, agree/findings/high <op> <n>, or contains \"text\"")
        for key in ("foreach", "in"):
            if s.get(key) and s[key] not in seen:
                raise ValueError(f"{where}: {key} must name an earlier stage")
        if s.get("write") and not s.get("isolate") and (len(s.get("workers") or [1]) > 1 or s.get("foreach")):
            raise ValueError(f"{where}: several writers in one tree collide; use isolate = true")
        for key in ("prompt", "test", "worker", "workers", "focus", "model", "files"):
            for r in refs(s.get(key)):
                if r not in seen and r not in params and r != "args" and not (r == "item" and s.get("foreach")):
                    raise ValueError(f"{where}: {{{r}}} is not an earlier stage or a parameter")
        seen.append(n)
        out.append({**s, "name": n})
    return {"name": name, "description": defn.get("description", ""), "params": params, "stages": out, "source": source}


def run_dir(run_id):
    if not sessions.NAME.fullmatch(run_id or ""):
        raise ValueError(f"bad run id {run_id!r}")
    return RUNS / run_id


def load_run(run_id):
    state = sessions.read_json(run_dir(run_id) / "state.json")
    if state is None:
        raise KeyError(f"no pipeline run {run_id!r} (`farmout pipe ls --runs` lists them)")
    return state


def save(state):
    sessions.write_json(run_dir(state["id"]) / "state.json", state)


def start(defn, cwd, args="", params=None):
    """Create a run (not yet started). Parameters: the pipeline's defaults, then --set values; args is free text."""
    values = {"args": "", **defn["params"], **(params or {})}
    if args:
        values["args"] = args
    missing = [k for k, v in values.items() if v is None]
    if missing:
        raise ValueError(f"pipeline {defn['name']} needs: {', '.join(missing)} (give args after the name, or --set k=v)")
    run_id = sessions.reserve(RUNS, defn["name"])
    state = {"id": run_id, "pipeline": defn["name"], "source": defn["source"], "cwd": os.path.realpath(cwd),
             "params": {k: str(v) for k, v in values.items()}, "defn": defn, "status": "pending",
             "created": time.time(), "stages": [{"name": s["name"], "status": "pending"} for s in defn["stages"]]}
    save(state)
    return state


def substitute(value, vals, item=None):
    if isinstance(value, list):
        return [substitute(v, vals, item) for v in value]
    if not isinstance(value, str):
        return value

    def rep(m):
        if m.group(1) == "item" and item is not None:
            return item
        key = m.group(1) + (m.group(2) or "")
        if key not in vals:
            raise ValueError(f"{m.group(0)} has no value (that stage made no {'patch' if m.group(2) else 'output'})")
        return vals[key]
    return PLACE.sub(rep, value)


def context(state):
    vals = dict(state["params"])
    for st in state["stages"]:
        if st.get("output"):
            vals[st["name"]] = st["output"]
        if st.get("patch"):
            vals[st["name"] + ".patch"] = st["patch"]
    return vals


def pick(names, distinct=True):
    """The stage's workers that are available here; an unavailable one is swapped for another, so built-in
    pipelines work with whatever subscriptions you have. distinct: no worker twice (one session per worker)."""
    out, notes = [], []
    for w in names:
        if w in ENABLED and not (distinct and w in out):
            out.append(w)
            continue
        if w in ENABLED:
            continue
        alt = next((a for a in ENABLED if a != "api" and a not in out), None)
        if not alt and not distinct:
            alt = next(iter(ENABLED), None)
        if not alt:
            if not out and not ENABLED:
                raise ValueError("no workers available (`farmout doctor`)")
            notes.append(f"{w} is not available here, and no other worker is free")
            continue
        notes.append(f"{w} is not available here; used {alt}")
        out.append(alt)
    if not out:
        raise ValueError("no workers available (`farmout doctor`)")
    return out, notes


def items(text, limit):
    return [m.group(1) for m in (re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$", line) for line in text.splitlines())
            if m][:limit]


def evaluate(gate, result):
    """(passed, note) for a stage that finished."""
    if not gate:
        return True, None
    word, var, op, num, needle = GATE.match(gate).groups()
    if word and word.lower() == "ok":
        return True, None
    if word:  # tests
        t = result.get("metrics", {}).get("tests")
        return (True, "no test command") if t is None else (bool(t), "tests passed" if t else "tests failed")
    if var:
        val = result.get("metrics", {}).get(var.lower())
        if val is None:
            return False, f"this stage doesn't measure {var}"
        ok = OPS[op](val, int(num))
        return ok, f"{var} = {val} ({gate}: {'met' if ok else 'not met'})"
    ok = needle.lower() in Path(result["output"]).read_text().lower()
    return ok, f'output {"contains" if ok else "lacks"} "{needle}"'


def worktree_of(state, stage_name):
    st = next(x for x in state["stages"] if x["name"] == stage_name)
    for name in st.get("sessions") or []:
        iso = sessions.load(name).get("iso")
        if iso:
            return iso
    raise ValueError(f"stage {stage_name!r} has no worktree to work in (it needs isolate = true)")


def run_stage(state, s, attempt=1):
    """Run one stage; returns {ok, output, patch, sessions, metrics, note}. A resumed stage's sessions get new names."""
    vals, d = context(state), run_dir(state["id"])
    group = f"{state['id']}-{s['name']}" + (f"-{attempt}" if attempt > 1 else "")
    out, written = d / f"{s['name']}.md", s  # written: the stage as the pipeline file has it
    sk = None
    if s.get("skill") and s["skill"] not in SKILLS:  # one of your skills: its instructions, with the prompt as args
        sk = skills.get(s["skill"], state["cwd"])
        mode = str(sk["meta"].get("mode") or "ask")
        text = "" if s.get("foreach") else skills.render(sk, substitute(s.get("prompt") or "", vals))
        s = {**s, "skill": {"review": "review", "race": "race"}.get(mode), "prompt": text, "focus": text, "raw": True,
             "files": (s.get("files") or []) + [sk["dir"]]}
        if not s.get("worker") and not s.get("workers") and sk["meta"].get("workers"):
            s["workers"] = sk["meta"]["workers"] if isinstance(sk["meta"]["workers"], list) else [sk["meta"]["workers"]]
        for k in ("test", "model", "effort", "isolate", "write"):
            if s.get(k) is None and skills.option(sk, k) is not None:  # a project's or a fetched skill can't add test/write
                s[k] = skills.option(sk, k)
    sub = (lambda v: v) if s.get("raw") else (lambda v: substitute(v, vals))  # a skill's own text may hold braces
    iso = worktree_of(state, s["in"]) if s.get("in") else None
    cwd = os.path.join(iso["worktree"], iso.get("rel", ".")) if iso else state["cwd"]
    stage_keys = {st["name"] for st in state["stages"]} | {st["name"] + ".patch" for st in state["stages"]}
    used = [vals[r] for r in refs([written.get("prompt"), written.get("test")], full=True)  # stage outputs, not params
            if r in stage_keys and r in vals and os.path.isfile(vals[r])]
    files = [substitute(f, vals) for f in s.get("files") or []] + sorted(set(used))
    names = (s.get("workers") or [s.get("worker")]) if (s.get("workers") or s.get("worker")) else flows.default_workers()
    workers, notes = pick([substitute(w, vals) for w in names], distinct=not s.get("foreach"))
    model, effort, test = sub(s.get("model")), s.get("effort"), sub(s.get("test") or "")
    skill = s.get("skill")

    if skill == "review":
        r = review.review(cwd, workers=workers, focus=sub(s.get("focus")) or None,
                          since=iso["base"] if iso else None, root=iso["worktree"] if iso else None, group=group)
        out.write_text(review.render(r))
        sessions.write_json(d / f"{s['name']}.json", r)
        return {"ok": r["empty"] or bool(r["workers"]), "output": str(out), "sessions": r["sessions"],
                "metrics": review.counts(r), "note": "; ".join(notes + [f"{w} failed" for w in r["failed"]]) or None}

    if skill == "race":
        prompt = sub(s["prompt"])
        _, rows, winner = flows.race(cwd, prompt, workers, test=test or None, group=group, files=files)
        lines = [f"{r['session']}: {'ok' if r['ok'] else 'failed'}, {r['files']} files +{r['added']}/-{r['removed']}"
                 + ("" if r["tests"] is None else f", tests {'passed' if r['tests'] else 'failed'}")
                 + (f", patch {r['patch']}" if r["patch"] else "") for r in rows]
        lines.append(f"winner: {winner['session']}" if winner else "no winner")
        out.write_text("\n".join(lines) + "\n")
        return {"ok": winner is not None, "output": str(out), "patch": winner and winner["patch"],
                "sessions": [r["session"] for r in rows], "metrics": {"tests": bool(winner) if test else None},
                "note": "; ".join(notes) or None}

    if s.get("foreach"):
        src = Path(vals[s["foreach"]]).read_text()
        todo = items(src, int(s.get("max_items", 8)))
        if not todo:
            return {"ok": False, "output": None, "note": f"no '- item' lines in the output of {s['foreach']}"}
        workers = [workers[i % len(workers)] for i in range(len(todo))]
        prompts = [skills.render(sk, substitute(written.get("prompt") or "", vals, item)) if sk
                   else substitute(s["prompt"], vals, item) for item in todo]
        names = {i: f"{group}-{i + 1}" for i in range(len(todo))}
    else:
        prompts, names = sub(s["prompt"]), None
    results = flows.fan(group, workers, cwd, prompts, files, isolate=bool(s.get("isolate")),
                        write=bool(s.get("write")) and not s.get("isolate"), models={w: model for w in workers} if model else None,
                        effort=effort, fallback=s.get("fallback"), names=names)
    parts, patches, tests = [], [], []
    for name, rec in results:
        meta = sessions.load(name)
        head = f"## {name} ({rec.get('worker')}, {rec['status']}, {rec.get('secs', '?')}s)"
        body = flows.text(rec).strip()
        if rec["status"] == "ok" and meta.get("iso"):
            patches.append(meta["iso"]["patch"])
            if test:
                passed, log = proc.shell(test, os.path.join(meta["iso"]["worktree"], meta["iso"].get("rel", ".")))
                tests.append(passed)
                body += f"\n\nTest `{test}`: {'passed' if passed else 'FAILED'}\n{log[-800:]}"
        elif rec["status"] == "ok" and test and (s.get("write") or s.get("in")):
            passed, log = proc.shell(test, cwd)
            tests.append(passed)
            body += f"\n\nTest `{test}`: {'passed' if passed else 'FAILED'}\n{log[-800:]}"
        parts.append(head + "\n\n" + body + (f"\n\npatch: {meta['iso']['patch']}" if meta.get("iso") and rec["status"] == "ok" else ""))
    text = parts[0].split("\n\n", 1)[1] if len(parts) == 1 else "\n\n".join(parts)
    if len(patches) > 1:
        text += "\n\nPatches:\n" + "\n".join(f"- {p}" for p in patches)
    out.write_text(text + "\n")
    ok_all = all(rec["status"] == "ok" for _, rec in results)
    failed = [n for n, rec in results if rec["status"] != "ok"]
    return {"ok": ok_all if skill != "ask" else any(rec["status"] == "ok" for _, rec in results),
            "output": str(out), "patch": patches[0] if len(patches) == 1 else None, "sessions": [n for n, _ in results],
            "metrics": {"tests": all(tests) if tests else None},
            "note": "; ".join(notes + ([f"failed: {', '.join(failed)}"] if failed else [])) or None}


def running(run_id):
    """Whether some process is executing this run (it holds the run's lock)."""
    f = run_dir(run_id) / "run.lock"
    if not f.exists():
        return False
    with open(f, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def execute(run_id, progress=None):
    """Run (or continue) a pipeline run until it ends or waits for approval. Returns the final state.
    progress, if given, is called with each stage's state as it starts and ends. One executor per run."""
    d = run_dir(run_id)
    load_run(run_id)
    lock = open(d / "run.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError(f"{run_id} is already running (`farmout pipe stop {run_id}` stops it)") from None
    (d / "run.pid").write_text(str(os.getpid()))
    try:
        return _execute(run_id, progress or (lambda st: None))
    finally:
        (d / "run.pid").unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _execute(run_id, progress):
    state = load_run(run_id)
    state.update(status="running", pid=os.getpid())
    save(state)
    current = None
    try:
        for i, s in enumerate(state["defn"]["stages"]):
            st = state["stages"][i]
            if st["status"] in DONE:
                continue
            bad = any(x["status"] in ("failed", "gated") for x in state["stages"][:i])
            on = s.get("on", "success")
            if (on == "success" and bad) or (on == "failure" and not bad):
                st.update(status="skipped", note="an earlier stage failed or was gated" if bad else "nothing failed")
                save(state)
                progress(st)
                continue
            if s.get("approve") and not st.get("approved"):
                st["status"] = state["status"] = "waiting"
                state.pop("pid", None)
                save(state)
                return state
            current = st
            st.update(status="running", started=time.time(), attempt=st.get("attempt", 0) + 1)
            for k in ("output", "patch", "sessions", "metrics", "note", "gate"):
                st.pop(k, None)
            save(state)
            progress(st)
            try:
                result = run_stage(state, s, st["attempt"])
                passed, gate_note = evaluate(s.get("gate"), result) if result.get("ok") else (False, None)
            except Exception as e:  # a stage's error ends that stage, not the run's bookkeeping
                result, passed, gate_note = {"ok": False, "note": f"{type(e).__name__}: {e}"}, False, None
            st.update({k: v for k, v in result.items() if k != "ok" and v is not None})
            st.update(status="ok" if passed else "gated" if result.get("ok") else "failed", ended=time.time())
            if gate_note:
                st["gate"] = gate_note
            current = None
            notify(state, "StageEnd", {"stage": st["name"], "status": st["status"], "output": st.get("output"),
                                       "patch": st.get("patch"), "gate": st.get("gate")}, st)
            save(state)
            progress(st)
    except KeyboardInterrupt:
        for n in list(flows.ACTIVE):
            sessions.stop(n)
        if current is not None:
            current.update(status="stopped", ended=time.time())
        state["status"] = "stopped"
        state.pop("pid", None)
        save(state)
        raise
    final = [x["status"] for x in state["stages"]]
    state["status"] = "failed" if "failed" in final else "gated" if "gated" in final else "ok"
    state.pop("pid", None)
    notify(state, "PipelineEnd", {"status": state["status"],
                                  "stages": [{k: x.get(k) for k in ("name", "status", "output", "patch")} for x in state["stages"]]})
    save(state)
    return state


def notify(state, kind, payload, st=None):
    """Fire a pipeline event's hooks; failures are noted on the stage (or the run)."""
    try:
        _, notes = hooks.fire(kind, {"run": state["id"], "pipeline": state["pipeline"], **payload}, state["cwd"],
                              subject=state["pipeline"])
    except Exception as e:
        notes = [f"{kind} hooks: {type(e).__name__}: {e}"]
    if notes:
        target = st if st is not None else state
        target["note"] = "; ".join(filter(None, [target.get("note")] + notes))


def reset(state):
    """Make a run resumable: everything from the first stage that didn't finish well runs again."""
    if running(state["id"]):
        raise RuntimeError(f"{state['id']} is still running")
    i = next((i for i, st in enumerate(state["stages"]) if st["status"] not in ("ok", "skipped")), len(state["stages"]))
    for j in range(i, len(state["stages"])):
        old = state["stages"][j]
        state["stages"][j] = {"name": old["name"], "status": "pending", "attempt": old.get("attempt", 0),
                              **({"approved": True} if old.get("approved") else {})}
    state["status"] = "pending"
    save(state)
    return state


def approve(run_id, reject=False):
    if running(run_id):
        raise RuntimeError(f"{run_id} is still running")
    state = load_run(run_id)
    st = next((x for x in state["stages"] if x["status"] == "waiting"), None)
    if not st:
        raise ValueError(f"{run_id} isn't waiting for approval (status: {state['status']})")
    if reject:
        st.update(status="failed", note="rejected by you", ended=time.time())
        st.pop("approved", None)
    else:
        st.update(status="pending", approved=True)
    state["status"] = "pending"
    save(state)
    return state


def runner_main(run_id):
    """`farmout _pipe <run>`: a background pipeline run."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        execute(run_id)
    except KeyboardInterrupt:
        proc.kill_all()
        return 130
    return 0


def background(run_id):
    p = sessions.spawn(["_pipe", run_id], run_dir(run_id) / "run.log")
    (run_dir(run_id) / "run.pid").write_text(str(p.pid))  # so `pipe stop` finds it even before it starts
    return p


def stop(run_id):
    """Stop the process executing this run (foreground or background). True if one was running."""
    load_run(run_id)
    try:
        pid = int((run_dir(run_id) / "run.pid").read_text())
    except (OSError, ValueError):
        return False
    if sessions.alive(pid) and (running(run_id) or sessions.is_farmout(pid, "_pipe", run_id)):
        os.kill(pid, signal.SIGTERM)
        return True
    return False


def status(state):
    """The run's status, with a run whose executor died shown as interrupted."""
    return "interrupted" if state["status"] == "running" and not running(state["id"]) else state["status"]


def runs():
    if not RUNS.exists():
        return []
    states = [s for s in (sessions.read_json(p / "state.json") for p in RUNS.iterdir()) if s]
    return sorted(states, key=lambda s: s.get("created", 0))


def describe(state):
    """A run's stages as text lines."""
    mark = {"ok": "✓", "failed": "✗", "gated": "■", "skipped": "-", "waiting": "?", "running": "…", "stopped": "✗",
            "pending": " "}
    lines = [f"{state['id']} ({state['pipeline']}, {state['source']}): {status(state)}  in {state['cwd']}"]
    for st in state["stages"]:
        bits = [x for x in (st.get("gate"), st.get("note")) if x]
        where = st.get("patch") or st.get("output") or ""
        lines.append(f"  {mark.get(st['status'], ' ')} {st['name']:<14} {st['status']:<8} {' · '.join(bits)}"
                     + (f"\n      {where}" if where else ""))
    if state["status"] == "waiting":
        lines.append(f"Waiting for you: farmout pipe approve {state['id']}   (or --reject)")
    elif status(state) in ("failed", "stopped", "gated", "interrupted"):
        lines.append(f"To run again from the stage that stopped: farmout pipe resume {state['id']}")
    return "\n".join(lines)
