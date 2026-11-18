"""The farmout command line. `farmout --help` lists the commands and `farmout <command> --help` explains one."""
import argparse
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import VERSION, bench, engine, flows, graph, live, pipeline, proc, review, sessions, skills, store, usage
from . import hooks as farmout_hooks
from .config import OUT_DIR, STATE, digest, find, project_root, read, repo_root, settings, trust_file
from .graph import ago, short
from .workers import API_BASE, API_MODEL, ENABLED, SETUP, WORKERS, lane, use_main_claude

HOOK_MARK = "# farmout review gate"


def err(text):
    print(text, file=sys.stderr, flush=True)


def emit(a, data, text=None):
    if getattr(a, "json", False):
        print(json.dumps(data, indent=1, default=str))
    elif text is not None:
        print(text)


def split(value):
    return [w.strip() for w in value.split(",") if w.strip()] if value else []


def table(rows):
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def piped_stdin():
    """What was piped or redirected into farmout. Only a pipe or a file counts: a terminal waits for typing, and a
    socket or device left open by whatever started farmout (a background job in an agent harness, an IDE, a CI
    runner) would block the read forever."""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (AttributeError, OSError, ValueError):  # no stdin at all, or a closed one
        return ""
    return sys.stdin.read() if stat.S_ISFIFO(mode) or stat.S_ISREG(mode) else ""


def read_prompt(words, required=True, cwd=None):
    """(prompt, extra files). Piped stdin is the prompt when no words are given; otherwise it goes along as a file.
    With `redact = true` in the settings, credentials in piped text are replaced before anything is saved."""
    text = " ".join(words).strip()
    piped = piped_stdin()
    if piped.strip() and redacting(cwd):
        piped, n = farmout_hooks.redact(piped)
        if n:
            err(f"farmout: redacted {n} secret{'s' * (n != 1)} from stdin")
    if piped.strip() and not text:
        return piped, []
    if piped.strip():
        return text, [sessions.stash(piped)]
    if required and not text:
        raise ValueError("give a prompt, or pipe one in")
    return text, []


def redacting(cwd):
    try:
        return bool(settings(cwd or os.getcwd()).get("redact"))
    except Exception:
        return False


def check_workers(names):
    bad = [w for w in names if w not in ENABLED]
    if bad:
        raise ValueError(f"unavailable worker(s): {', '.join(bad)}; available: {', '.join(ENABLED) or 'none'} (`farmout doctor`)")
    return names


def everyone(asked):
    """The workers a fan-out command uses: the ones asked for, else every ready one. Never none: with no worker
    installed, a review would print "No findings" and look like a clean change."""
    workers = check_workers(split(asked)) or flows.default_workers()
    if not workers:
        raise ValueError("no workers are ready here: `farmout doctor` shows what to install or log in to")
    return workers


def picked(asked, cwd=None):
    """-w for ask, review, race and fix-pr: lanes, each `worker` or `worker:model`. One subscription can fill several
    (`claude:opus,claude:sonnet`, or `codex,codex` for two runs of one model). With no -w, every ready worker that
    isn't known to be at a usage limit or logged out."""
    lanes = split(asked)
    if lanes:
        check_workers(list(dict.fromkeys(lane(x)[0] for x in lanes)))
        return lanes
    workers, held, why, s = everyone(None), usage.limits(), usage.reasons(), settings(cwd or os.getcwd())
    out = {w: engine.lane_key(w, None, s) for w in workers}
    out = {w: (k if k in held else w) for w, k in out.items() if engine.held_by(k, w, held, why)}
    ready = [w for w in workers if w not in out]
    for k in out.values():
        err(f"farmout: left out {usage.describe(k)}")
    if not ready:
        raise ValueError("every worker is at a usage limit or logged out (`farmout status`); -w uses one anyway")
    return ready


def auto_worker(cwd):
    """(worker, model) for `farmout run auto`: the first allowed lane that isn't at a usage limit or logged out,
    your fallback order first (`farmout bench --apply` writes it best-first), then every other worker."""
    s, held, why = settings(cwd), usage.limits(), usage.reasons()
    allowed = [lane(x) for x in (s.get("fallback") or []) + list(ENABLED)]
    allowed = [(w, m) for w, m in allowed if w in ENABLED and w in s.get("workers", ENABLED)]
    pick = next(((w, m) for w, m in allowed if w != "api" and not engine.held_by(engine.lane_key(w, m, s), w, held, why)),
                None)
    pick = pick or next(iter(allowed), None)
    if not pick:
        raise ValueError("no allowed workers available here (`farmout doctor`, .farmout/settings)")
    return pick


def show_turn(a, name, rec):
    text = flows.text(rec)
    if a.json:
        print(json.dumps({"session": name, **rec, "text": text}, indent=1, default=str))
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")
        notes = "; ".join(rec.get("notes") or [])
        err(f"[{name} · turn {rec.get('n')} · {rec.get('worker')} · {rec['status']} · {rec.get('secs', '?')}s]"
            + (f" {notes}" if notes else ""))
    return 0 if rec["status"] == "ok" else 1


def send_and_show(a, name, prompt, files, attach=(), switch=None, note=None):
    if a.bg:
        msg = sessions.deliver(name, prompt, files, attach, cwd=a.cwd, wait=False, switch=switch, note=note)
        emit(a, {"session": name, "message": msg["id"], "status": "queued"},
             f"{name}: queued; `farmout attach {name}` to watch, `farmout log {name}` to read")
        return 0
    flows.ACTIVE.add(name)
    worker = (switch or {}).get("worker") or sessions.load(name)["worker"]
    after = {name: max((r["n"] for r in sessions.turns(name)), default=0)}
    with live.Lanes(None, None, "working", lanes=[(worker, worker, name)], after=after, keep=False,
                    on=live.enabled(a)):  # a waiting row, so a long answer doesn't look like a hang
        rec = sessions.deliver(name, prompt, files, attach, cwd=a.cwd, switch=switch, note=note)
    flows.ACTIVE.discard(name)
    return show_turn(a, name, rec)


def cmd_run(a):
    prompt, extra = read_prompt(a.prompt, cwd=a.cwd)
    worker, model = auto_worker(a.cwd) if a.worker == "auto" else lane(a.worker)
    check_workers([worker])
    meta = sessions.create(worker, a.cwd, name=a.name, write=a.write, isolate=a.isolate, model=a.model or model,
                           effort=a.effort, fallback=None if a.fallback is None else split(a.fallback))
    if not a.json:
        err(f"[{meta['name']} · {worker}{' · isolated' if a.isolate else ' · writes' if a.write else ''}]")
    return send_and_show(a, meta["name"], prompt, a.file + extra)


def follow(a, name):
    """A session whose folder was renamed or moved follows it (or, with --here, moves to the current folder)."""
    moved = sessions.rehome(name, a.cwd, force=getattr(a, "here", False))
    if moved and not a.json:
        err(f"[{name} · its folder is now {short(moved)}]")


def cmd_send(a):
    prompt, extra = read_prompt(a.message, cwd=a.cwd)
    follow(a, a.session)
    meta = sessions.load(a.session)
    worker, model = lane(a.worker) if a.worker else (None, None)
    if worker:
        check_workers([worker])
    model = a.model or model
    switch = {"worker": worker, "model": model} if worker or model else None
    if switch and not a.json:
        err(f"[{a.session} · {meta['worker']}{':' + meta['model'] if meta.get('model') else ''} → "
            f"{worker or meta['worker']}{':' + model if model else ''}]")
    return send_and_show(a, a.session, prompt, a.file + extra, a.attach, switch)


def cmd_fork(a):
    prompt, extra = read_prompt(a.message, cwd=a.cwd)
    worker, model = lane(a.worker) if a.worker else (None, None)
    if worker:
        check_workers([worker])
    follow(a, a.session)
    meta, files, note = sessions.fork(a.session, a.name, worker, a.at, a.model or model)
    if not a.json:
        err(f"[{meta['name']} · branched from {meta['parent']} after turn {meta['base']} · {meta['worker']}"
            f"{':' + meta['model'] if meta.get('model') else ''}]")
    return send_and_show(a, meta["name"], prompt, files + a.file + extra, note=note)


def cmd_tree(a):
    from . import ui
    if a.session:
        sessions.load(a.session)  # an unknown name stops here
    roots = graph.forest(ui.snapshot(a.cwd, None)["sessions"], everything=a.all or bool(a.session))
    roots = [r for r in roots if a.session in [x["name"] for x in graph.nodes(r)]] if a.session else roots[:a.limit]
    roots.reverse()  # the most recently active last, next to your prompt, as in `farmout ls`
    if a.json:
        return emit(a, roots)
    if not roots:
        return print("No conversations yet. Start one: farmout run <worker> \"<prompt>\"")
    colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    print(graph.text(roots, shutil.get_terminal_size((100, 24)).columns, a.session, colour, a.cwd), flush=True)
    err("\nContinue from any turn: farmout fork <session> --at <turn> \"...\", or browse them all with farmout ui")


def cmd_ui(a):
    if a.web or a.port or a.no_open:
        from . import ui
        return ui.serve(a.cwd, a.port, not a.no_open)
    from . import tui
    return tui.run(a.cwd, a.session)


def answers(rounds):
    return [[{"session": n, "worker": r.get("worker"), "lane": flows.label(n), "status": r["status"],
              "secs": r.get("secs"), "text": flows.text(r)} for n, r in results] for results in rounds]


def show_answers(a, group, rounds):
    if a.json:
        emit(a, {"group": group, "answers": answers(rounds)[-1], "rounds": answers(rounds)})
    else:
        for i, results in enumerate(rounds, 1):
            if len(rounds) > 1:
                print(f"═══ round {i}{' (final)' if i == len(rounds) else ''} " + "═" * 30)
            for name, rec in results:
                via = rec.get("worker")
                who = flows.label(name) + (f" → {via}" if via and via != lane(flows.label(name))[0] else "")
                print(f"── {who} · {rec['status']} · {rec.get('secs', '?')}s · {name} " + "─" * 20)
                print(flows.text(rec).rstrip() + "\n")
        err(f"Follow up with one of them: farmout send <session> \"...\" ({', '.join(n for n, _ in rounds[-1])})")
    return 0 if any(r["status"] == "ok" for _, r in rounds[-1]) else 1


def cmd_ask(a):
    prompt, extra = read_prompt(a.prompt, cwd=a.cwd)
    workers = picked(a.workers, a.cwd)
    group = sessions.new_group("debate" if a.debate else "ask")
    title = f"farmout ask{' --debate' if a.debate else ''} · {len(workers)} workers · read-only"
    with live.Lanes(group, workers, "answering", title=title, on=live.enabled(a)):
        if a.debate:
            group, rounds = flows.debate(a.cwd, prompt, workers, a.file + extra, rounds=max(2, a.rounds), group=group)
        else:
            group, results = flows.ask(a.cwd, prompt, workers, a.file + extra, group=group)
            rounds = [results]
    return show_answers(a, group, rounds)


def show_review(a, r, min_agree=1, fail_on="never"):
    bad = review.failing(r, min_agree, fail_on)
    if a.json:
        emit(a, {**r, "failing": len(bad)})
    else:
        print(review.render(r, min_agree, shutil.get_terminal_size((120, 20)).columns))
        if bad:
            err(f"farmout review: {len(bad)} finding(s) at {fail_on} or worse, flagged by {min_agree}+ models")
    if not r["empty"] and not r["workers"]:
        return 1
    return 1 if bad else 0


def cmd_review(a):
    workers = picked(a.workers, a.cwd)
    group = sessions.new_group("review")
    with live.Lanes(group, workers, "reviewing", title=f"farmout review · {len(workers)} workers · read-only",
                    findings=True, on=live.enabled(a)):
        r = review.review(a.cwd, workers=workers, base=a.base, staged=a.staged, focus=" ".join(a.focus) or None,
                          group=group)
    return show_review(a, r, a.min_agree, a.fail_on)


def cmd_race(a):
    task, extra = read_prompt(a.task, cwd=a.cwd)
    workers = picked(a.workers, a.cwd)
    group = sessions.new_group("race")
    with live.Lanes(group, workers, "working", title=f"farmout race · {len(workers)} workers · one worktree each",
                    on=live.enabled(a)) as lanes:
        group, rows, winner = flows.race(a.cwd, task, workers, test=a.test, files=a.file + extra, group=group,
                                         progress=lanes.note)
    return show_race(a, group, rows, winner)


def cmd_fix_pr(a):
    if a.dry_run:
        task = flows.pr_task(a.cwd, a.number)
        return emit(a, {"number": a.number, "task": task}, task)
    workers = picked(a.workers, a.cwd)
    group = sessions.new_group(f"pr{a.number}")
    with live.Lanes(group, workers, "working", title=f"farmout fix-pr {a.number} · {len(workers)} workers",
                    on=live.enabled(a)) as lanes:
        group, rows, winner = flows.fix_pr(a.cwd, a.number, workers, test=a.test, group=group, progress=lanes.note)
    return show_race(a, group, rows, winner)


def show_race(a, group, rows, winner):
    applied = None
    if a.apply and winner:
        try:
            applied = flows.apply(winner["session"])
        except (RuntimeError, ValueError) as e:  # the results still matter: report, don't hide them
            applied = f"Could not apply it: {e}"
    if a.json:
        emit(a, {"group": group, "attempts": rows, "winner": winner and winner["session"], "applied": applied})
        return 0 if winner else 1
    head = [("worker", "result", "tests", "files", "+/-", "time", "session")]
    body = [(r["worker"], "ok" if r["ok"] else "FAILED", "-" if r["tests"] is None else "pass" if r["tests"] else "FAIL",
             r["files"], f"+{r['added']}/-{r['removed']}", f"{r['secs'] or 0:.0f}s", r["session"]) for r in rows]
    print(table(head + body))
    for r in rows:
        if r["error"]:
            print(f"{r['worker']}: {r['error'].splitlines()[0]}")
        if r["tests"] is False:
            print(f"{r['worker']} test output (end):\n{r['test_output'][-600:]}")
    if winner:
        print(f"\nWinner: {winner['session']} ({winner['worker']}). "
              + (applied if applied else f"See it: farmout diff {winner['session']}   Apply it: farmout apply {winner['session']}"))
    else:
        print("\nNo winner" + (": no attempt passed the test command." if a.test else ": no attempt changed anything."))
    return 0 if winner else 1


def ls_rows(a):
    rows = [sessions.summary(sessions.load(n)) for n in sessions.names()]
    return rows if a.all else rows[-a.limit:]


def ls_text(rows):
    if not rows:
        return "No sessions yet. Start one: farmout run <worker> \"<prompt>\""
    return table([("NAME", "WORKER", "STATE", "TURNS", "AGE", "WHERE")] +
                 [(r["name"], r["worker"], r["state"] + (f" (+{r['queued']})" if r["queued"] and r["state"] == "running" else ""),
                   r["turns"], ago(r["created"]), short(r["cwd"]) + (f" [{r['branch']}]" if r["branch"] else "")
                   + ("" if os.path.isdir(r["cwd"]) else " (folder gone)"))
                  for r in rows])


def cmd_ls(a):
    if a.json:
        return emit(a, ls_rows(a))
    if not a.watch:
        return print(ls_text(ls_rows(a)))
    try:  # a dashboard: redraw until Ctrl-C
        while True:
            sys.stdout.write("\033[H\033[2J" + status_line() + f"   ({time.strftime('%H:%M:%S')}, Ctrl-C to quit)\n\n"
                             + ls_text(ls_rows(a)) + "\n")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0


def print_turn(r):
    prompt = r["prompt"].strip().splitlines() or [""]
    print(f"── turn {r['n']} · {r.get('worker')} · {r['status']} · {r.get('secs', '?')}s " + "─" * 20)
    print("> " + "\n> ".join(prompt[:6]) + ("\n> …" if len(prompt) > 6 else ""))
    if r["status"] != "running":
        print("\n" + flows.text(r).rstrip() + "\n")


def cmd_log(a):
    recs = sessions.turns(a.session)
    recs = recs[-1:] if a.last else recs
    if a.json:
        return emit(a, [{**r, "text": flows.text(r) if r["status"] != "running" else None} for r in recs])
    for r in recs:
        print_turn(r)
    if a.follow:
        return attach(a.session, {r["n"] for r in recs if r["status"] != "running"})


def attach(name, seen):
    shown_running = set()
    try:
        while True:
            for r in sessions.turns(name):
                if r["n"] in seen:
                    continue
                if r["status"] == "running":
                    if r["n"] not in shown_running:
                        print_turn(r)
                        err(f"(turn {r['n']} running on {r.get('worker')}; Ctrl-C detaches, the session keeps going)")
                        shown_running.add(r["n"])
                    continue
                if r["n"] in shown_running:
                    print("\n" + flows.text(r).rstrip() + "\n")
                    err(f"[turn {r['n']} · {r['status']} · {r.get('secs', '?')}s]")
                else:
                    print_turn(r)
                seen.add(r["n"])
            if not sessions.busy(name) and not sessions.pending(name):
                return 0
            time.sleep(0.3)
    except KeyboardInterrupt:
        err("detached")
        return 0


def cmd_attach(a):
    sessions.load(a.session)
    done = {r["n"] for r in sessions.turns(a.session) if r["status"] != "running"}
    if not sessions.busy(a.session) and not sessions.pending(a.session):
        err(f"{a.session} is idle; showing its last turn")
        recs = sessions.turns(a.session)
        if recs:
            print_turn(recs[-1])
        return 0
    return attach(a.session, done)


def cmd_wait(a):
    rc, out = 0, []
    names = a.sessions or [n for n in sessions.names() if sessions.busy(n) or sessions.pending(n)]
    if not names:
        return print("Nothing is running.")
    for name in names:
        rec = sessions.wait_idle(name, a.timeout)
        status = rec["status"] if rec else "new"
        rc = rc or int(status not in ("ok", "new"))
        out.append({"session": name, "status": status, "turn": rec and rec["n"]})
    emit(a, out, "\n".join(f"{o['session']}: {o['status']}" for o in out))
    return rc


def cmd_stop(a):
    names = [n for n in sessions.names() if sessions.busy(n) or sessions.pending(n)] if a.all else a.sessions
    if not names:
        return print("Nothing is running.")
    for n in names:
        sessions.load(n)
        print(f"{n}: {'stopped' if sessions.stop(n) else 'was not running (its queue is cleared)'}")


def cmd_rm(a):
    names = [n for n in sessions.names() if not sessions.busy(n) and not sessions.pending(n)] if a.idle else a.sessions
    for n in names:
        sessions.remove(n)
        print(f"removed {n}")


def cmd_diff(a):
    patch, stat, _ = flows.current_patch(a.session)
    if a.json:
        return emit(a, {"session": a.session, "patch": str(patch), "stat": stat})
    sys.stdout.write(patch.read_text(errors="surrogateescape"))
    err(f"{patch}\n{stat}")


def cmd_apply(a):
    if a.pr:
        if len(a.sessions) != 1:
            raise ValueError("--pr opens one pull request: give one session")
        url = flows.pull_request(a.sessions[0])
        return emit(a, {"session": a.sessions[0], "pull_request": url}, url)
    done = []  # several patches land in order, and the first that doesn't apply stops the rest
    for name in a.sessions:
        try:
            msg = flows.apply(name)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"{name}: {e}" + (f"\n(already applied: {', '.join(done)}; `farmout undo` takes them back)"
                                                 if done else "")) from None
        done.append(name)
        if not a.json:
            print(msg)
    if a.json:
        emit(a, {"applied": done})


def cmd_undo(a):
    name = a.session or flows.last_applied(a.cwd)
    msg = flows.undo(name)
    emit(a, {"session": name, "result": msg}, msg)


def export_html(name, meta, recs):
    import html
    style = ("body{font:15px/1.55 system-ui,sans-serif;max-width:62rem;margin:2rem auto;padding:0 1rem;color:#1f2328}"
             "pre{white-space:pre-wrap;background:#f6f8fa;padding:.8rem 1rem;border-radius:6px}h2{margin-top:2.2rem}"
             "@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}pre{background:#161b22}}")
    parts = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>",
             f"<title>farmout {html.escape(name)}</title><style>{style}</style>",
             f"<h1>farmout session {html.escape(name)}</h1><p>{html.escape(meta['worker'])} · {html.escape(meta['cwd'])}</p>"]
    for r in recs:
        head = f"Turn {r['n']} · {r.get('worker')} · {r['status']}" + (f" · {r['secs']}s" if r.get("secs") is not None else "")
        parts.append(f"<section><h2>{html.escape(head)}</h2><h3>Prompt</h3><pre>{html.escape(r['prompt'])}</pre>"
                     f"<h3>Reply</h3><pre>{html.escape(flows.text(r))}</pre></section>")
    return "".join(parts) + "\n"


def cmd_export(a):
    meta = sessions.load(a.session)
    recs = sessions.turns(a.session)
    text = export_html(a.session, meta, recs) if a.html else f"# farmout session {a.session} ({meta['worker']})\n\n" + sessions.transcript(a.session)
    if a.output:
        Path(a.output).write_text(text)
        return print(f"wrote {a.output}")
    sys.stdout.write(text)


def status_line():
    names = sessions.names()
    running = [n for n in names if sessions.busy(n)]
    queued = sum(len(sessions.pending(n)) for n in names)
    bits = [f"{len(running)} running"] if running else []
    bits += [f"{queued} queued"] if queued else []
    bits += [f"{st['id']} {pipeline.status(st)}" for st in pipeline.runs() if pipeline.status(st) in ("running", "waiting")]
    why = usage.reasons()
    bits += [f"{w} {'logged out' if why.get(w) == 'login' else 'limited'} till "
             f"{time.strftime('%H:%M', time.localtime(u))}" for w, u in usage.limits().items()]
    return "farmout: " + (" · ".join(bits) if bits else "idle")


def cmd_status(a):
    if a.json:
        names = sessions.names()
        return emit(a, {"running": [n for n in names if sessions.busy(n)],
                        "queued": sum(len(sessions.pending(n)) for n in names), "limited": usage.limits(),
                        "why": usage.reasons(),
                        "pipelines": [{"run": st["id"], "status": pipeline.status(st)} for st in pipeline.runs()
                                      if pipeline.status(st) in ("running", "waiting")]})
    print(status_line())
    if not a.line:
        for n in sessions.names():
            if sessions.busy(n):
                print(f"  {n} ({sessions.load(n)['worker']}) running")
        for w in usage.limits():
            print(f"  {usage.describe(w)}")


def cmd_trust(a):
    data = sessions.read_json(trust_file(), {}) or {}
    if a.list:
        return print("\n".join(data) or "You haven't trusted any project's settings.")
    root = project_root(a.cwd)
    if a.revoke:
        data.pop(str(root), None)
        trust_file().parent.mkdir(parents=True, exist_ok=True)
        trust_file().write_text(json.dumps(data, indent=1))
        return print(f"{root}'s hooks won't run any more.")
    f = find(root / ".farmout", "settings")
    if not f:
        raise ValueError(f"{root} has no .farmout/settings to trust")
    hooks = read(f).get("hooks") or []
    print(f"Trusting {f}." + ("" if hooks else " It defines no hooks."))
    for h in hooks:
        print(f"  {h.get('event')}: {h.get('command')}" + (f"   (matching {h['matcher']})" if h.get("matcher") else ""))
    data[str(root)] = digest(f)
    trust_file().parent.mkdir(parents=True, exist_ok=True)
    trust_file().write_text(json.dumps(data, indent=1))
    print("These run on farmout's events in this project. If the file changes, you'll be asked to trust it again.")


def cmd_hooks(a):
    s = settings(a.cwd)
    hs = farmout_hooks.configured(a.cwd, s)
    if a.json:
        return emit(a, {"hooks": hs, "untrusted": s.get("untrusted")})
    print(table([("EVENT", "COMMAND", "MATCHER", "FROM")] +
                [(h["event"], h["command"], h.get("matcher") or "", short(h["origin"])) for h in hs]) if hs
          else "No hooks. Add [[hooks]] to ~/.config/farmout/settings.toml, or `farmout store ls` for ready-made ones.")
    if s.get("untrusted"):
        print(f"{s['untrusted']} defines hooks you haven't trusted; they don't run until you `farmout trust`")


def cmd_skill(a):
    if a.scmd == "ls":
        found = skills.available(a.cwd)
        if a.json:
            return emit(a, [{k: v for k, v in sk.items() if k != "body"} for sk in found.values()])
        return print(table([("SKILL", "MODE", "FROM", "DESCRIPTION")] +
                           [(n, sk["meta"].get("mode") or "ask", sk["origin"], sk["description"][:70]) for n, sk in sorted(found.items())])
                     if found else "No skills yet. `farmout store ls`, or `farmout skill add <git-url|path|url>`.")
    if a.scmd == "show":
        sk = skills.get(a.skill, a.cwd)
        return print(f"{sk['path']}\n\n" + Path(sk["path"]).read_text(errors="replace"))
    if a.scmd == "add":
        name, dest, notes = skills.add(a.source, a.name, a.path, a.ref, a.force)
        print(f"Installed skill {name} in {dest}. Run it: farmout {name} [args]" + "".join(f"\nNote: {n}" for n in notes))
        return 0
    if a.scmd == "rm":
        skills.remove(a.skill)
        return print(f"removed {a.skill}")
    if a.scmd == "run":
        text, extra = read_prompt(a.args, required=False, cwd=a.cwd)
        sk = skills.get(a.skill, a.cwd)
        mode, result = skills.run(sk, a.cwd, text, workers=check_workers(split(a.workers)) or None, files=a.file + extra,
                                  write=a.write or None, isolate=a.isolate or None, test=a.test, model=a.model)
        if mode == "review":
            return show_review(a, result, a.min_agree, a.fail_on)
        if mode == "race":
            return show_race(a, *result)
        group, results = result
        if len(results) == 1 and not a.json:
            return show_turn(a, results[0][0], results[0][1])
        return show_answers(a, group, [results])
    raise ValueError("skill needs an action: ls, show, run, add or rm")


def cmd_store(a):
    where = store.url(a.cwd)
    if a.tcmd == "ls":
        where, entries = store.index(where)
        have = store.installed()
        if a.json:
            return emit(a, {"store": where, "entries": entries})
        print(f"{where}\n" + table([("NAME", "KIND", "VERSION", "INSTALLED", "DESCRIPTION")] +
                                   [(e["name"], e["kind"], e.get("version", ""), (have.get(e["name"]) or (0, {}))[1].get("version", ""),
                                     e.get("description", "")[:70]) for e in entries]))
        return 0
    if a.tcmd == "show":
        where, e = store.entry(a.name, where)
        print(json.dumps({k: v for k, v in e.items() if k != "files"}, indent=1))
        for rel, data in store.download(where, e):
            print(f"\n── {rel} (sha256 verified) " + "─" * 20 + "\n" + data.decode(errors="replace").rstrip())
        return 0
    if a.tcmd == "add":
        e, dest = store.add(a.name, where, force=a.force)
        how = {"skill": f"farmout {e['name']} [args]", "hook": "it runs on " + ", ".join(e.get("hook", {}).get("events", [])),
               "tool": f"farmout tool run {e['name']}"}[e["kind"]]
        return print(f"Installed {e['kind']} {e['name']} {e.get('version', '')} in {dest} (every file's SHA-256 checked); {how}.")
    if a.tcmd == "rm":
        return print(f"removed {store.remove(a.name)} {a.name}")
    if a.tcmd == "update":
        updated = []
        for name, (kind, meta, _) in store.installed().items():
            try:
                _, e = store.entry(name, where)
            except KeyError:
                continue
            if e.get("version") != meta.get("version"):
                store.add(name, where)
                updated.append(f"{name} {meta.get('version')} -> {e.get('version')}")
        return print("\n".join(updated) or "Everything installed from the store is up to date.")
    raise ValueError("store needs an action: ls, show, add, rm or update")


def cmd_tool(a):
    if a.tcmd == "ls":
        found = store.tools()
        return print("\n".join(f"{n:<16} {cmd}" for n, cmd in found.items()) or "No tools. `farmout store ls` lists some.")
    if a.tcmd == "run":
        return store.run_tool(a.tool, a.args, a.cwd)
    raise ValueError("tool needs an action: ls or run")


def apply_bench(a, rows):
    """`farmout bench --apply`: the measured best model per worker and a best-first fallback order go into this
    repository's .farmout/settings.json (JSON: TOML can't be written without a dependency). Returns what changed."""
    want = bench.advice(rows, [w for w in ENABLED if w != "api"])
    if not want:
        raise ValueError("nothing to apply: no lane finished a review in that run")
    root = project_root(a.cwd)
    toml, path = root / ".farmout" / "settings.toml", root / ".farmout" / "settings.json"
    if toml.exists():
        lines = [f"fallback = {json.dumps(want['fallback'])}"] + (
            ["[models]"] + [f"{w} = {json.dumps(m)}" for w, m in want["models"].items()] if want["models"] else [])
        raise ValueError(f"{toml} is TOML, which farmout doesn't rewrite; add these lines yourself:\n" + "\n".join(lines))
    data = read(path) if path.exists() else {}
    hooks = bool(data.get("hooks"))
    data["fallback"] = want["fallback"]
    if want["models"]:
        data["models"] = {**(data.get("models") or {}), **want["models"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")
    return {"settings": str(path), **want, "retrust": hooks}


def show_applied(applied):
    models = ", ".join(f"{w} → {m}" for w, m in applied["models"].items())
    print(f"Wrote {applied['settings']}:")
    if models:
        print(f"  default models: {models}")
    print(f"  fallback and `farmout run auto` order: {', '.join(applied['fallback'])}")
    if applied["retrust"]:
        print("  That file defines hooks, and it changed: `farmout trust` runs them again.")


def cmd_bench(a):
    if a.last:
        if not a.apply:
            raise ValueError("--last goes with --apply: it applies your newest saved bench of this repository")
        top = repo_root(a.cwd)
        found = bench.last_report(top) if top else None
        if not found:
            raise ValueError("no saved bench of this repository yet: run `farmout bench --apply`")
        rows, report = found
        applied = apply_bench(a, rows)
        if a.json:
            return emit(a, {"report": str(report), "applied": applied})
        print(f"From {report}:")
        return show_applied(applied)
    asked = list(dict.fromkeys(split(a.workers)))  # lanes: codex, or claude:opus to name a model
    if (not asked or "claude" in [lane(x)[0] for x in asked]) and use_main_claude() and not a.json:
        err("farmout bench: your main Claude login takes part, on its own quota (no second account in FARMOUT_CLAUDE_DIR)")
    if asked:
        check_workers(list(dict.fromkeys(lane(x)[0] for x in asked)))
    workers = asked or everyone(None)
    n = len(workers)
    if not a.json:
        err(f"farmout bench: up to {a.commits} commit{'s' * (a.commits != 1)} x {n} lane{'s' * (n != 1)}"
            f" = up to {a.commits * n} review{'s' * (a.commits * n != 1)} on your subscriptions")
    lanes = (lambda g, t, ws: live.Lanes(g, ws, "reviewing", title=t, findings=True)) if live.enabled(a) else None
    rows, report = bench.run(a.cwd, a.commits, workers, a.scan, progress=(lambda m: None) if a.json else err,
                             lanes=lanes)
    board = bench.scoreboard(rows)
    card = bench.share(board, rows, report) if a.share else None
    applied, failed_apply = None, None
    if a.apply:
        try:
            applied = apply_bench(a, rows)
        except ValueError as e:  # the reviews are paid for: show them, then say what couldn't be applied
            failed_apply = str(e)
    if a.json:
        return emit(a, {"commits": rows, "scoreboard": [{"worker": w, "found": f, "of": o, "failed": x} for w, f, o, x in board],
                        "report": str(report), **({"share": card} if card else {}),
                        **({"applied": applied} if applied else {}),
                        **({"apply_error": failed_apply} if failed_apply else {})}) or (1 if failed_apply else 0)
    print(table([("WORKER", "FOUND", "FAILED")] + [(w, f"{f}/{o}", x) for w, f, o, x in board]))
    print()
    ws = [w for w, *_ in board]
    print(table([("COMMIT", "SUBJECT", *ws)] + [(r["commit"][:9], r["subject"][:48],
                                                 *("✓" if r["found"].get(w) else "x" if w in r["failed"] else "·" for w in ws))
                                                for r in rows]))
    print(f"\n✓ found the bug   · missed it   x failed.  Report: {report}")
    if card:
        print("\n" + card["text"] + "\n")
        print(f"Image: {card['png'] or card['svg']}" + ("" if card["png"] else "  (install librsvg or ImageMagick for a PNG)"))
        print(f"Post it: {card['url']}")
        print("Scores only: no repository name, file names or code.")
    elif rows:
        err("Share it: farmout bench --share")
    if applied:
        print()
        show_applied(applied)
    elif failed_apply:
        err(f"farmout bench --apply: {failed_apply}")
        return 1
    elif rows:
        err("Use the winners here (best model per worker, fallback order): farmout bench --apply --last")


def progress(st):
    if st["status"] == "running":
        err(f"… {st['name']}")
    else:
        bits = [x for x in (st.get("gate"), st.get("note")) if x]
        err(f"{'✓' if st['status'] == 'ok' else '-' if st['status'] == 'skipped' else '✗'} {st['name']}: {st['status']}"
            + (f" ({'; '.join(bits)})" if bits else ""))


def finish_run(a, state):
    if a.json:
        emit(a, state)
    else:
        print(pipeline.describe(state))
    return {"ok": 0, "waiting": 0, "stopped": 130}.get(state["status"], 1)


def go(a, run_id):
    if a.bg:
        pipeline.background(run_id)
        return emit(a, {"run": run_id, "status": "started"},
                    f"{run_id} runs in the background: farmout pipe status {run_id}") or 0
    return finish_run(a, pipeline.execute(run_id, progress=None if a.json else progress))


def cmd_pipe(a):
    if a.pcmd == "ls":
        if a.runs:
            states = pipeline.runs()
            if a.json:
                return emit(a, [{k: s.get(k) for k in ("id", "pipeline", "status", "created", "cwd")} for s in states])
            return print(table([("RUN", "PIPELINE", "STATUS", "AGE", "WHERE")] +
                               [(s["id"], s["pipeline"], s["status"], ago(s.get("created")), short(s["cwd"])) for s in states])
                         if states else "No runs yet.")
        found = pipeline.available(a.cwd)
        if a.json:
            return emit(a, [{"name": n, "description": d, "source": src} for n, d, src in found])
        for n, d, src in found:
            print(f"{n:<10} {d}\n{'':<10} ({short(src)})")
        return 0
    if a.pcmd == "run":
        params = {}
        for kv in a.set:
            k, sep, v = kv.partition("=")
            if not sep or not k:
                raise ValueError(f"--set wants KEY=VALUE, got {kv!r}")
            params[k] = v
        defn = pipeline.load(a.pipeline, a.cwd)
        state = pipeline.start(defn, a.cwd, " ".join(a.args), params)
        if not a.json and not a.bg:
            err(f"{state['id']}: " + " → ".join(s["name"] for s in defn["stages"]))
        return go(a, state["id"])
    if a.pcmd == "status":
        return finish_run(a, pipeline.load_run(a.run))
    if a.pcmd == "resume":
        pipeline.reset(pipeline.load_run(a.run))
        return go(a, a.run)
    if a.pcmd == "approve":
        pipeline.approve(a.run, reject=a.reject)
        return go(a, a.run)
    if a.pcmd == "stop":
        return print(f"{a.run}: {'stopped' if pipeline.stop(a.run) else 'was not running'}")
    raise ValueError("pipe needs an action: run, ls, status, resume, approve or stop")


def cmd_stats(a):
    rows, limits = usage.stats(a.days), usage.limits()
    if a.json:
        return emit(a, {"days": a.days, "workers": rows, "limited_until": limits})
    if not rows:
        return print(f"No worker runs in the last {a.days} days.")

    def dur(s):
        return f"{s / 3600:.1f}h" if s >= 3600 else f"{s / 60:.0f}m" if s >= 60 else f"{s:.0f}s"
    body = [(w, r["runs"], r["ok"], r["failed"], r["limited"], dur(r["secs"]), dur(r["secs"] / max(r["runs"], 1)),
             f"{r['tokens']:,}" if r["tokens"] else "-") for w, r in sorted(rows.items(), key=lambda x: -x[1]["runs"])]
    print(f"Last {a.days} days\n" + table([("WORKER", "RUNS", "OK", "FAILED", "LIMITED", "TIME", "AVG", "TOKENS")] + body))
    for w in limits:
        print(usage.describe(w))
    total = sum(r["secs"] for r in rows.values())
    print(f"Your other subscriptions worked {dur(total)} for you.")


def hook_script(kind):
    py, pkg = shlex.quote(sys.executable), shlex.quote(sessions.PKG)
    run = f'PYTHONPATH={pkg}${{PYTHONPATH:+:$PYTHONPATH}} exec {py} -m farmout review'
    lines = ["#!/bin/sh", f"{HOOK_MARK} ({kind}), installed by `farmout hook install`.",
             "# Skip it once with FARMOUT_SKIP=1, or git's --no-verify.", '[ "${FARMOUT_SKIP:-0}" = 1 ] && exit 0']
    if kind == "pre-push":
        lines += ["base=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || "
                  "{ echo 'farmout: no upstream branch, so nothing to compare; skipping the review' >&2; exit 0; }",
                  f'{run} --base "$base" --min-agree 2 --fail-on high']
    else:
        lines.append(f"{run} --staged --min-agree 2 --fail-on high")
    return "\n".join(lines) + "\n"


def cmd_hook(a):
    top = repo_root(a.cwd)
    if not top:
        raise ValueError("not in a git repository")
    hooks = Path(subprocess.run(["git", "-C", str(top), "rev-parse", "--git-path", "hooks"], capture_output=True,
                                text=True, check=True).stdout.strip())
    hooks = hooks if hooks.is_absolute() else top / hooks
    kind = "pre-push" if a.pre_push else "pre-commit"
    f, backup = hooks / kind, hooks / f"{kind}.farmout-backup"
    ours = f.exists() and HOOK_MARK in f.read_text(errors="replace")
    if a.hcmd == "uninstall":
        if not ours:
            return print(f"No farmout {kind} hook installed.")
        f.unlink()
        if backup.exists():
            backup.rename(f)
        return print(f"Removed the farmout {kind} hook" + (" and restored your old one." if f.exists() else "."))
    if f.exists() and not ours:
        if not a.force:
            raise ValueError(f"{f} exists; --force moves it to {backup.name} first")
        if backup.exists():
            raise ValueError(f"{backup} already holds an earlier hook; move it away first")
        f.rename(backup)
    hooks.mkdir(parents=True, exist_ok=True)
    f.write_text(hook_script(kind))
    f.chmod(0o755)
    print(f"Installed {f}: every {'push' if a.pre_push else 'commit'} is reviewed by your workers, and blocked when "
          "2+ models flag a high-severity problem. Skip once: FARMOUT_SKIP=1.")


def cmd_doctor(a):
    from .mcp import run_all
    print(f"farmout {VERSION} on Python {sys.version.split()[0]}; sessions and runs in {STATE}, results in {OUT_DIR}")
    for name, w in WORKERS.items():
        exe, how = SETUP[name]
        detail = f"{API_MODEL} at {API_BASE}" if w.ready and not exe else how
        if w.ready and exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
                detail = f"{shutil.which(exe)} {((out.stdout or out.stderr).strip().splitlines() or ['?'])[0]}"
            except Exception as e:
                detail = f"{shutil.which(exe)} (--version failed: {e})"
        print(f"  {'ready  ' if w.ready else 'missing'}  {name:<12} {detail}")
        if name == "antigravity" and w.ready:
            print(f"  note     {'':<12} Google's terms (section 6) may forbid using Antigravity with other tools;"
                  " see Terms of service in the README")
    for w in usage.limits():
        print(f"  skipped  {usage.describe(w)}")
    if len([w for w in ENABLED if w != "api"]) == 1:
        w = next(w for w in ENABLED if w != "api")
        print(f"One subscription is enough: compare its models (`farmout bench -w {w}:<model>,{w}:<model>`), review "
              f"with two of them (`farmout review -w {w}:<model>,{w}:<model>`), and set a fallback model for its limits "
              "(`fallback = [\"" + w + ":<model>\"]` in your settings).")
    if not a.live:
        print("Logins are not checked. `farmout doctor --live` sends each ready worker one tiny prompt.")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        tasks = [{"worker": w, "prompt": "Reply with exactly: ok", "cwd": tmp, "fallback": []} for w in ENABLED]
        for w, (head, body, ok) in zip(ENABLED, run_all(tasks, "doctor", Path(tmp)) if tasks else []):
            print(f"  {'works  ' if ok else 'BROKEN '}  {w:<12} {head.split()[-3]}  {'' if ok else body[:300]}")
    return 0


def parser():
    p = argparse.ArgumentParser(prog="farmout", description=(
        "Hand work to the AI subscriptions you already pay for (Codex, Gemini via Antigravity, GitHub Copilot, a "
        "second Claude account, local models): one at a time, all at once, or in pipelines."))
    p.add_argument("--version", action="version", version=f"farmout {VERSION}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-C", dest="cwd", metavar="DIR", help="work in DIR instead of the current directory")
    files = argparse.ArgumentParser(add_help=False)
    files.add_argument("-f", "--file", action="append", default=[], metavar="PATH", help="a file for the worker to read (repeatable)")

    def add(name, help, fn, parents=(common,), **kw):
        sp = sub.add_parser(name, parents=list(parents), help=help, description=help, **kw)
        sp.set_defaults(fn=fn)
        return sp

    r = add("run", "start a session: send a prompt to one worker, which remembers the conversation", cmd_run, (common, files))
    r.add_argument("worker", help="antigravity, codex, claude, copilot, api, or auto (the first one not at its limit); "
                   "worker:model names the model (claude:opus)")
    r.add_argument("prompt", nargs="*", help="the prompt; or pipe it in")
    r.add_argument("-n", "--name", help="name the session (default: <worker>-<n>)")
    r.add_argument("--write", action="store_true", help="let the worker edit files in the current directory")
    r.add_argument("--isolate", action="store_true", help="edit in a private git worktree instead; makes a patch")
    r.add_argument("--model", help="the worker's model")
    r.add_argument("--effort", help="reasoning effort, e.g. low, medium, high")
    r.add_argument("--fallback", metavar="W1,W2", help="workers (or worker:model) to try if this one hits a usage "
                   "limit or is logged out; default: your settings', else every other ready worker; \"\" for none")
    r.add_argument("--bg", action="store_true", help="return at once; `farmout attach` to watch")

    s = add("send", "send another message to a session", cmd_send, (common, files))
    s.add_argument("session")
    s.add_argument("message", nargs="*", help="the message; or pipe it in")
    s.add_argument("--attach", action="append", default=[], metavar="SESSION", help="also hand over that session's last reply")
    s.add_argument("-w", "--worker", metavar="WORKER[:MODEL]",
                   help="continue the conversation on another worker from this message on (it gets the earlier turns)")
    s.add_argument("--model", help="continue on another model of the same worker (or of --worker)")
    s.add_argument("--here", action="store_true", help="the session's folder moved: continue in the current folder "
                   "(-C DIR for another); inside the same git repository farmout finds it without this")
    s.add_argument("--bg", action="store_true", help="queue it and return at once")

    f = add("fork", "branch a conversation: a new session that keeps its turns up to one you pick and goes on from "
            "there", cmd_fork, (common, files))
    f.add_argument("session")
    f.add_argument("message", nargs="*")
    f.add_argument("--at", type=int, metavar="TURN", help="branch after this turn (`farmout tree` numbers them; "
                   "default: the newest; 0 keeps none)")
    f.add_argument("-n", "--name")
    f.add_argument("-w", "--worker", metavar="WORKER[:MODEL]", help="continue on another worker (it gets the "
                   "conversation so far as a file)")
    f.add_argument("--model", help="continue on another model")
    f.add_argument("--here", action="store_true", help="the session's folder moved: use the current folder")
    f.add_argument("--bg", action="store_true")

    tr = add("tree", "every conversation as a tree: its turns, and the branches off each one", cmd_tree)
    tr.add_argument("session", nargs="?", help="only the tree this session belongs to")
    tr.add_argument("-n", dest="limit", type=int, default=10, help="how many conversations (default 10)")
    tr.add_argument("--all", action="store_true", help="also review, ask, race and pipeline runs")
    ui = add("ui", "every conversation as a graph in the terminal: read any turn, continue a conversation or branch "
             "off any turn", cmd_ui)
    ui.add_argument("session", nargs="?", help="start at this session's newest turn")
    ui.add_argument("--web", action="store_true", help="the same in your browser instead")
    ui.add_argument("--port", type=int, default=0, help="with --web: port on 127.0.0.1 (default: any free one)")
    ui.add_argument("--no-open", action="store_true", help="with --web: print the address instead of opening a browser")

    k = add("ask", "ask every worker the same question in parallel", cmd_ask, (common, files))
    k.add_argument("prompt", nargs="*", help="the question; or pipe it in")
    k.add_argument("-w", "--workers", metavar="W1,W2", help="only these workers; worker:model picks its model (claude:opus)")
    k.add_argument("--debate", action="store_true", help="then each reads the others' answers and revises or defends its own")
    k.add_argument("--rounds", type=int, default=2, help="debate rounds, counting the first answer (default 2)")

    v = add("review", "every worker reviews your change; findings merged with agreement counts", cmd_review)
    v.add_argument("focus", nargs="*", help="optional focus, e.g. security")
    g = v.add_mutually_exclusive_group()
    g.add_argument("--base", metavar="REF", help="review the branch since REF instead of uncommitted changes")
    g.add_argument("--staged", action="store_true", help="review only the staged changes")
    v.add_argument("-w", "--workers", metavar="W1,W2", help="only these workers; worker:model picks its model (claude:opus)")
    v.add_argument("--min-agree", type=int, default=1, metavar="N", help="show findings flagged by N+ models (default 1)")
    v.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="never",
                   help="exit 1 if a finding at this severity or worse is flagged by --min-agree models")

    c = add("race", "every worker attempts the same task in its own worktree; your tests pick the winner", cmd_race, (common, files))
    c.add_argument("task", nargs="*", help="the task; or pipe it in")
    c.add_argument("-w", "--workers", metavar="W1,W2", help="only these workers; worker:model picks its model (claude:opus)")
    c.add_argument("--test", metavar="CMD", help="command that must pass, run in each worktree, e.g. 'pytest -x'")
    c.add_argument("--apply", action="store_true", help="apply the winner's patch to your tree")

    fp = add("fix-pr", "a pull request's review comments and failing checks become a race (check it out first)", cmd_fix_pr)
    fp.add_argument("number", type=int)
    fp.add_argument("-w", "--workers", metavar="W1,W2", help="only these workers; worker:model picks its model (claude:opus)")
    fp.add_argument("--test", metavar="CMD", help="command that must pass in each worktree")
    fp.add_argument("--apply", action="store_true", help="apply the winner's patch to your tree")
    fp.add_argument("--dry-run", action="store_true", help="print the brief the workers would get, and stop")

    ls = add("ls", "list sessions", cmd_ls)
    ls.add_argument("--all", action="store_true", help="every session, not just the newest")
    ls.add_argument("-n", dest="limit", type=int, default=30, help="how many to show (default 30)")
    ls.add_argument("--watch", action="store_true", help="keep redrawing, as a dashboard (Ctrl-C to quit)")
    ls.add_argument("--interval", type=float, default=2, help="seconds between redraws (default 2)")
    lg = add("log", "show a session's conversation", cmd_log)
    lg.add_argument("session")
    lg.add_argument("--last", action="store_true", help="only the last turn")
    lg.add_argument("--follow", action="store_true", help="keep showing new turns (Ctrl-C to stop watching)")
    add("attach", "watch a running session until it's idle (Ctrl-C detaches; it keeps running)", cmd_attach).add_argument("session")
    w = add("wait", "wait until sessions are idle; exit 1 if a last turn failed", cmd_wait)
    w.add_argument("sessions", nargs="*", help="default: every session that's running or queued")
    w.add_argument("--timeout", type=float, metavar="SECONDS")
    st = add("stop", "stop sessions: the running turn and anything queued", cmd_stop)
    st.add_argument("sessions", nargs="*")
    st.add_argument("--all", action="store_true", help="every running session")
    rm = add("rm", "delete sessions, with their worktrees and branches", cmd_rm)
    rm.add_argument("sessions", nargs="*")
    rm.add_argument("--idle", action="store_true", help="every session that isn't running")
    add("diff", "show an isolated session's patch", cmd_diff).add_argument("session")
    ap = add("apply", "apply isolated sessions' patches to your tree in order, or open a pull request", cmd_apply)
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--pr", action="store_true", help="instead: commit it on a new branch, push, and open a PR with gh")
    add("undo", "take back a patch `farmout apply` applied", cmd_undo).add_argument(
        "session", nargs="?", help="default: the patch applied last in this repository")
    ex = add("export", "a session's conversation as Markdown (or --html), to share", cmd_export)
    ex.add_argument("session")
    ex.add_argument("--html", action="store_true")
    ex.add_argument("-o", "--output", metavar="FILE")
    stt = add("status", "what's running, queued, waiting or at a usage limit", cmd_status)
    stt.add_argument("--line", action="store_true", help="one line, for a shell prompt or a tmux status bar")

    pp = add("pipe", "pipelines: stages that start when the previous one ends", cmd_pipe, ())
    psub = pp.add_subparsers(dest="pcmd", metavar="<action>")
    pr = psub.add_parser("run", parents=[common], help="run a pipeline (built-in: fix, spec)")
    pr.add_argument("pipeline", help="a name from `farmout pipe ls`, or a .toml/.json file")
    pr.add_argument("args", nargs="*", help="free text for {args}, e.g. the goal for spec")
    pr.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="a parameter (repeatable)")
    pr.add_argument("--bg", action="store_true", help="run in the background")
    pl = psub.add_parser("ls", parents=[common], help="list pipelines (or --runs)")
    pl.add_argument("--runs", action="store_true", help="list runs instead")
    psub.add_parser("status", parents=[common], help="show a run's stages").add_argument("run")
    for name, help in (("resume", "run a failed, stopped or gated run again from where it stopped"),
                       ("approve", "approve the stage a run is waiting on, and continue")):
        x = psub.add_parser(name, parents=[common], help=help)
        x.add_argument("run")
        x.add_argument("--bg", action="store_true")
        if name == "approve":
            x.add_argument("--reject", action="store_true", help="reject it instead")
    psub.add_parser("stop", parents=[common], help="stop a running pipeline").add_argument("run")

    sa = add("stats", "what each subscription did for you", cmd_stats)
    sa.add_argument("--days", type=int, default=7)
    bn = add("bench", "which of your subscriptions reviews best on your own code (replays past bug fixes)", cmd_bench)
    bn.add_argument("-n", "--commits", type=int, default=3, help="how many bug-fix commits to replay (default 3)")
    bn.add_argument("-w", "--workers", metavar="W1,W2", help="only these workers; worker:model names a model, and one "
                    "worker on several models compares them (claude:opus,claude:sonnet)")
    bn.add_argument("--scan", type=int, default=400, help="how far back to look for them (default 400 commits)")
    bn.add_argument("--share", action="store_true", help="also make a post and an image card of the scores (no code)")
    bn.add_argument("--apply", action="store_true", help="then use the results in this repo: each worker's best model "
                    "and a best-first fallback order go into .farmout/settings.json")
    bn.add_argument("--last", action="store_true", help="with --apply: apply your newest saved bench instead of running one")

    sk = add("skill", "skills: SKILL.md folders run on your workers (`farmout <skill>` for short)", cmd_skill, ())
    ssub = sk.add_subparsers(dest="scmd", metavar="<action>")
    ssub.add_parser("ls", parents=[common], help="list skills")
    ssub.add_parser("show", parents=[common], help="print a skill").add_argument("skill")
    sr = ssub.add_parser("run", parents=[common, files], help="run a skill")
    sr.add_argument("skill")
    sr.add_argument("args", nargs="*", help="arguments ($ARGUMENTS); or pipe them in")
    sr.add_argument("-w", "--workers", metavar="W1,W2", help="override the skill's workers")
    sr.add_argument("--write", action="store_true")
    sr.add_argument("--isolate", action="store_true")
    sr.add_argument("--test", metavar="CMD", help="race mode: the command that picks the winner")
    sr.add_argument("--model")
    sr.add_argument("--min-agree", type=int, default=1, help="review mode")
    sr.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="never", help="review mode")
    sr.add_argument("--apply", action="store_true", help="race mode: apply the winner")
    sad = ssub.add_parser("add", parents=[common], help="install a skill from a git repo, a folder or a SKILL.md URL")
    sad.add_argument("source")
    sad.add_argument("--name", help="install under this name")
    sad.add_argument("--path", help="the skill's folder inside the source, if it holds several")
    sad.add_argument("--ref", help="git branch or tag")
    sad.add_argument("--force", action="store_true", help="replace an installed skill of that name")
    ssub.add_parser("rm", parents=[common], help="remove an installed skill").add_argument("skill")

    so = add("store", "reviewed skills, hooks and tools, every file pinned by SHA-256", cmd_store, ())
    tsub = so.add_subparsers(dest="tcmd", metavar="<action>")
    tsub.add_parser("ls", parents=[common], help="list the store")
    for name, help in (("show", "print an entry and its files"), ("add", "install an entry"), ("rm", "remove an installed entry")):
        x = tsub.add_parser(name, parents=[common], help=help)
        x.add_argument("name")
        if name == "add":
            x.add_argument("--force", action="store_true", help="replace a skill, hook or tool of yours with that name")
    tsub.add_parser("update", parents=[common], help="reinstall entries that have a new version")
    to = add("tool", "tools from the store: `farmout tool run <name> [args]`", cmd_tool, ())
    tosub = to.add_subparsers(dest="tcmd", metavar="<action>")
    tosub.add_parser("ls", parents=[common], help="list installed tools")
    trn = tosub.add_parser("run", parents=[common], help="run a tool")
    trn.add_argument("tool")
    trn.add_argument("args", nargs=argparse.REMAINDER, help="passed to the tool")

    tr = add("trust", "let this project's .farmout/settings hooks run (pinned to the file as it is now)", cmd_trust)
    tg = tr.add_mutually_exclusive_group()
    tg.add_argument("--revoke", action="store_true")
    tg.add_argument("--list", action="store_true", help="every project you trust")
    add("hooks", "the hooks that run here, and where they come from", cmd_hooks)
    hk = add("hook", "install a git hook that reviews every commit (or push)", cmd_hook, ())
    hsub = hk.add_subparsers(dest="hcmd", metavar="<action>")
    for name in ("install", "uninstall"):
        x = hsub.add_parser(name, parents=[common], help=f"{name} the hook")
        x.add_argument("--pre-push", action="store_true", help="review each push instead of each commit")
        if name == "install":
            x.add_argument("--force", action="store_true", help="move an existing hook aside")
    d = add("doctor", "check which workers are installed (and, with --live, logged in)", cmd_doctor)
    d.add_argument("--live", action="store_true")
    add("mcp", "serve the MCP tools on stdio (what `python3 server.py` does)", None, ())
    for hidden in ("_runner", "_pipe"):
        sub.add_parser(hidden).add_argument("target")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = parser()
    commands = next(x for x in p._actions if isinstance(x, argparse._SubParsersAction)).choices
    if argv and argv[0] not in commands and not argv[0].startswith("-"):  # `farmout <skill> [args]`
        try:
            if argv[0] in skills.available(os.getcwd()):
                argv = ["skill", "run", *argv]
        except Exception:
            pass
    a, extra = p.parse_known_args(argv)
    if extra:  # older argparse won't take free text after an option (`send s --attach x "msg"`): give it back
        field = next((f for f in ("prompt", "message", "task", "focus", "args") if hasattr(a, f)), None)
        if not field or any(x.startswith("-") for x in extra):
            p.error(f"unrecognized arguments: {' '.join(extra)}")
        setattr(a, field, list(getattr(a, field) or []) + extra)
    if a.cmd is None:
        p.print_help()
        return 0
    if a.cmd == "mcp":
        from . import mcp
        return mcp.main()
    if a.cmd == "_runner":
        return sessions.runner_main(a.target)
    if a.cmd == "_pipe":
        return pipeline.runner_main(a.target)
    action = {"pipe": "pcmd", "hook": "hcmd", "skill": "scmd", "store": "tcmd", "tool": "tcmd"}.get(a.cmd)
    if action and not getattr(a, action, None):
        p.parse_args([a.cmd, "--help"])
    a.cwd = os.path.realpath(os.path.expanduser(a.cwd)) if getattr(a, "cwd", None) else os.getcwd()
    a.json = getattr(a, "json", False)
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # `farmout stop` and friends: stop like Ctrl-C
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        for n in list(flows.ACTIVE):
            sessions.stop(n)
        proc.kill_all()
        err("farmout: stopped")
        return 130
    except (ValueError, KeyError, FileNotFoundError, PermissionError, RuntimeError, TimeoutError, OSError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        if a.json:
            print(json.dumps({"error": msg}))
        err(f"farmout: {msg}")
        return 1
