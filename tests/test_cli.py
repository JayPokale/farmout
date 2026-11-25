"""Offline tests for the farmout CLI and the MCP session/pipeline tools, against the fake worker CLIs.

Run:  python3 -m unittest discover -s tests -v
"""
import http.client
import http.server
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from test_server import CLIS, SERVER, Base, FakeAPI, flag, wait_until

sys.path.insert(0, str(SERVER.parent))
from farmout import graph  # noqa: E402
from farmout import review as review_mod  # noqa: E402


def in_farmout(env, body):
    """stdout of Python run with this test's state and the farmout package importable, for what no command reaches."""
    code = f"import json, sys, time\nsys.path.insert(0, {str(SERVER.parent)!r})\nfrom farmout import relay, sessions\n{body}"
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout


class CliBase(Base):
    def setUp(self):
        super().setUp()
        self.bindir = self.make_bin(CLIS + ("pdftotext",))
        self.env = {"HOME": str(self.home), "PATH": f"{self.bindir}:/usr/bin:/bin", "FAKE_LOG": str(self.log),
                    "FARMOUT_CLAUDE_DIR": str(self.tmp / "claude-config"), "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@localhost", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost"}
        self.state = self.home / ".local" / "state" / "farmout"
        self.addCleanup(self.stop_everything)

    def stop_everything(self):
        subprocess.run([sys.executable, str(SERVER), "stop", "--all"], env=self.env, capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=30)

    def fo(self, *args, input=None, cwd=None, rc=0, timeout=60, **env):
        """Run `farmout <args>`; asserts the exit code unless rc is None. Returns the CompletedProcess."""
        p = subprocess.run([sys.executable, str(SERVER), *args], cwd=cwd or self.work, env={**self.env, **env},
                           input=input, stdin=None if input is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        if rc is not None:
            self.assertEqual(p.returncode, rc, f"farmout {' '.join(args)}\nstdout: {p.stdout}\nstderr: {p.stderr}")
        return p

    def json(self, *args, **kw):
        return json.loads(self.fo(*args, "--json", **kw).stdout)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.work), *args], check=True, capture_output=True,
                              text=True, env={**os.environ, **self.env}).stdout

    def repo(self):
        self.git("init", "-q")
        (self.work / "a.py").write_text("x = 1\ny = 2\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def runs(self):
        """Every fake CLI run so far, oldest first."""
        recs = [(p.stat().st_mtime_ns, json.loads(p.read_text())) for p in self.log.glob("[0-9]*.json")]
        return [r for _, r in sorted(recs, key=lambda x: x[0])]

    def last(self, cli):
        return [r for r in self.runs() if r["name"] == cli][-1]

    def meta(self, name):
        return json.loads((self.state / "sessions" / name / "meta.json").read_text())

    def reviewers_say(self, **findings):
        for cli, text in findings.items():
            (self.log / f"review-{cli}.txt").write_text(text)


class SessionTest(CliBase):
    def test_review8_a_message_taken_back_after_the_runner_read_it_never_runs(self):
        self.fo("run", "codex", "say a", "-n", "s")
        out = in_farmout(self.env, "f = sessions.path('s') / 'inbox' / '9999-x.json'  # gone: a cancel took it\n"
                                   "print(sessions.turn('s', {'id': 'x', 'prompt': 'say b'}, ('session', 's'), f))\n"
                                   "print(len(sessions.turns('s')))")
        self.assertEqual(out.split(), ["None", "1"])

    def test_review9_a_stop_while_a_turn_is_set_up_or_wrapped_up(self):
        self.fo("run", "codex", "say a", "-n", "s")
        head = ("import os, signal\n"
                "signal.signal(signal.SIGTERM, getattr(sessions, 'stopping', signal.default_int_handler))\n"
                "f = sessions.path('s') / 'inbox' / '0099-{id}.json'\nf.write_text('{{}}')\n")
        run = ("try:\n    sessions.turn('s', {{'id': '{id}', 'prompt': 'say b'}}, ('session', 's'), f)\n"
               "except KeyboardInterrupt:\n    print('interrupted')\n"
               "print(sessions.turns('s')[-1]['status'], sessions.load('s')['native_id'])")
        # while it's set up (the earlier turns are being readied): stopped as the worker would start
        setup = in_farmout(self.env, head.format(id="b") + "real = sessions.handoff\n"
                           "def handoff(*a):\n    os.kill(os.getpid(), signal.SIGTERM)\n    return real(*a)\n"
                           "sessions.handoff = handoff\n" + run.format(id="b"))
        self.assertEqual(setup.split()[:2], ["interrupted", "stopped"])
        # while it's wrapped up (the worker has answered): too late, the answer and its conversation id are kept
        late = in_farmout(self.env, head.format(id="c") + "done = []\n"
                          "def execute(*a, **k):\n    done.append(1)\n"
                          "    return {'ok': True, 'worker': 'codex', 'model': None, 'text': 'answered', 'notes': [], "
                          "'iso': None, 'secs': 0.1, 'native_id': 'nid-9'}\n"
                          "sessions.engine.execute = execute\nreal = sessions.load\n"
                          "def load(n):\n    if done and len(done) == 1:\n        done.append(2)\n"
                          "        os.kill(os.getpid(), signal.SIGTERM)\n    return real(n)\n"
                          "sessions.load = load\n" + run.format(id="c"))
        self.assertEqual(late.split(), ["ok", "nid-9"])

    def test_review10_a_second_stop_doesnt_break_off_taking_the_worker_down(self):
        out = in_farmout(self.env, "import os, signal\nsignal.signal(signal.SIGTERM, sessions.stopping)\n"
                                   "sessions.STOP['working'] = True  # the worker runs\n"
                                   "for _ in range(2):\n    try:\n        os.kill(os.getpid(), signal.SIGTERM)\n"
                                   "        time.sleep(0.1)\n        print('let go')\n"
                                   "    except KeyboardInterrupt:\n        print('stopped')")
        self.assertEqual(out.splitlines(), ["stopped", "let go"])

    def test_review11_a_worker_a_stop_broke_off_taking_down_is_still_killed(self):
        mark = "37.2511"  # this test's own sleep
        out = in_farmout(self.env, "from farmout import proc\nreal = proc.stop\n"
                                   "def stop(p, grace=None):\n    raise KeyboardInterrupt  # a SIGTERM came meanwhile\n"
                                   "proc.stop = stop\ntry:\n"
                                   f"    proc.run(['sleep', '{mark}'], 'k', {str(self.work)!r}, timeout=0.5)\n"
                                   "except KeyboardInterrupt:\n    pass\nproc.stop = real\nproc.kill_all()\n"
                                   "time.sleep(0.5)\nprint('done')")
        self.assertEqual(out.strip(), "done")
        left = [d.name for d in Path("/proc").iterdir() if d.name.isdigit()
                and (lambda c: c == f"sleep\0{mark}\0")(self.cmdline(d))]
        for pid in left:  # tidy up before failing
            os.kill(int(pid), 9)
        self.assertEqual(left, [], "kill_all() found the worker")

    def test_review12_a_child_left_when_a_stop_broke_off_is_still_killed(self):
        mark = "37.2512"  # the CLI dies on TERM; its child ignores TERM
        out = in_farmout(self.env, "from farmout import proc\nreal = proc.stop\n"
                                   "def stop(p, grace=None):\n    proc.signal_group(p)\n    p.wait(5)\n"
                                   "    raise KeyboardInterrupt  # a second Ctrl-C before the KILL\n"
                                   "proc.stop = stop\ntry:\n"
                                   f"    proc.run(['sh', '-c', \"(trap '' TERM; exec sleep {mark}) & sleep 30\"], 'k', "
                                   f"{str(self.work)!r}, timeout=1)\n"
                                   "except KeyboardInterrupt:\n    pass\nproc.stop = real\nproc.kill_all(grace=1)\n"
                                   "time.sleep(0.5)\nprint('done')")
        self.assertEqual(out.strip(), "done")
        left = [d.name for d in Path("/proc").iterdir() if d.name.isdigit() and self.cmdline(d) == f"sleep\0{mark}\0"]
        for pid in left:  # tidy up before failing
            os.kill(int(pid), 9)
        self.assertEqual(left, [], "kill_all() killed what was left of its process group")

    @staticmethod
    def cmdline(d):
        try:
            return (d / "cmdline").read_bytes().decode(errors="replace")
        except OSError:
            return ""

    def test_review8_cancel_never_stops_another_messages_turn(self):
        self.fo("run", "codex", "say a", "-n", "s")
        in_farmout(self.env, "sessions.write_json(sessions.path('s') / 'turns' / '0002.json', {'n': 2, 'id': 'old', "
                             "'prompt': 'x', 'status': 'running', 'started': time.time()})  # its runner died")
        self.fo("send", "s", "nap 4", "--bg")
        wait_until(lambda: in_farmout(self.env, "print(sessions.busy('s'))").strip() == "True", 20)
        self.assertEqual(in_farmout(self.env, "print(sessions.cancel('s', 'old'))").strip(), "False")
        wait_until(lambda: in_farmout(self.env, "print(sessions.busy('s'))").strip() == "False", 30)
        self.assertIn("napped", self.fo("log", "s").stdout, "the running turn went on")

    def test_every_worker_resumes_its_own_conversation(self):
        for worker, cli in (("codex", "codex"), ("claude", "claude"), ("copilot", "copilot"), ("antigravity", "agy")):
            with self.subTest(worker):
                p = self.fo("run", worker, f"say first from {worker}", "-n", f"s-{worker}")
                self.assertEqual(p.stdout, f"first from {worker}\n")
                self.assertIn(f"[s-{worker} · turn 1 · {worker} · ok", p.stderr)
                first = self.last(cli)
                self.assertEqual(self.fo("send", f"s-{worker}", "recall").stdout, f"say first from {worker}\n",
                                 "the second turn continues the first conversation")
                second, native = self.last(cli), self.meta(f"s-{worker}")["native_id"]
                self.assertEqual((first["session"], second["session"]), (native, native))
                argv = second["argv"]
                if cli == "claude":
                    self.assertEqual(flag(first["argv"], "--session-id"), native)
                    self.assertEqual(flag(argv, "--resume"), native)
                    self.assertNotIn("--no-session-persistence", argv)
                elif cli == "copilot":
                    self.assertEqual(flag(argv, "--session-id"), native)
                elif cli == "codex":
                    self.assertEqual(argv[:2], ["exec", "resume"])
                    self.assertEqual(flag(argv, "-c"), 'sandbox_mode="read-only"')
                    self.assertNotIn("--ephemeral", first["argv"])
                else:
                    self.assertEqual(flag(argv, "--conversation"), native)

    def test_effort_and_model_are_passed_through(self):
        self.fo("run", "claude", "say hi", "--effort", "high", "--model", "opus")
        self.fo("run", "copilot", "say hi", "--effort", "low")
        self.fo("run", "codex", "say hi", "--effort", "medium")
        self.fo("run", "antigravity", "say hi", "--effort", "max")
        self.assertEqual(flag(self.last("claude")["argv"], "--effort"), "high")
        self.assertEqual(flag(self.last("claude")["argv"], "--model"), "opus")
        self.assertEqual(flag(self.last("copilot")["argv"], "--reasoning-effort"), "low")
        self.assertIn('model_reasoning_effort="medium"', self.last("codex")["argv"])
        self.assertEqual(flag(self.last("agy")["argv"], "--effort"), "max")

    def test_claude_reads_with_web_search_pre_approved(self):
        self.fo("run", "claude", "say hi")
        argv = self.last("claude")["argv"]
        self.assertEqual(flag(argv, "--tools"), "Read,Grep,Glob,WebSearch")
        self.assertEqual(flag(argv, "--allowedTools"), "WebSearch", "headless claude denies tools that would ask")

    def test_stdin_is_the_prompt_or_an_attached_file(self):
        self.assertEqual(self.fo("run", "codex", input="say piped\n").stdout, "piped\n")
        self.fo("run", "codex", "say with a file", input="the piped data\n")
        brief = self.last("codex")["brief"]
        stash = re.search(r"^- (\S+-stdin\.txt)$", brief, re.M).group(1)
        self.assertEqual(Path(stash).read_text(), "the piped data\n")
        self.assertIn("give a prompt", self.fo("run", "codex", rc=1).stderr)

    def test_deepseek_harness_reads_only_writes_on_request_and_resumes(self):
        env = {"PATH": f"{self.make_bin(('dsh',))}:{self.env['PATH']}"}
        self.assertEqual(self.fo("run", "deepseek", "say first", "-n", "ds", **env).stdout, "first\n")
        argv = self.last("dsh")["argv"]
        self.assertEqual(argv[:2], ["headless", "--json"])
        self.assertEqual((flag(argv, "--permission-mode"), flag(argv, "--approval-policy")), ("read-only", "never"))
        self.assertEqual(argv[-1], "-", "the brief goes on stdin")
        self.assertEqual(self.fo("send", "ds", "recall", **env).stdout, "say first\n", "the conversation resumes")
        self.assertRegex(flag(self.last("dsh")["argv"], "--session-id"), r"^session-")
        self.fo("run", "deepseek", "say edit", "--write", "-n", "dw", **env)
        self.assertEqual(flag(self.last("dsh")["argv"], "--permission-mode"), "workspace-write")
        self.assertIn("deepseek", self.fo("doctor", **env).stdout)

    def test_an_open_socket_on_stdin_is_not_read(self):
        # Background jobs in some agent harnesses get a socket as stdin that never closes; reading it hung forever.
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        p = subprocess.run([sys.executable, str(SERVER), "run", "codex", "say hi"], cwd=self.work, env=self.env,
                           stdin=theirs, capture_output=True, text=True, timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, "hi\n"), p.stderr)

    def test_json_output_and_failures(self):
        out = self.json("run", "codex", "say hi", "-n", "j")
        self.assertEqual((out["session"], out["status"], out["text"]), ("j", "ok", "hi"))
        p = self.fo("run", "codex", "fail boom", rc=1)
        self.assertEqual(p.stdout, "RuntimeError: codex exited 1: boom\n")
        self.assertIn("unavailable worker(s): nope", self.fo("run", "nope", "x", rc=1).stderr)
        self.assertIn("bad session name", self.fo("run", "codex", "x", "-n", "../evil", rc=1).stderr)
        self.assertIn("already exists", self.fo("run", "codex", "x", "-n", "j", rc=1).stderr)
        self.assertIn("no session named 'ghost'", self.fo("send", "ghost", "x", rc=1).stderr)
        for cmd in (("log", "ghost"), ("log", "ghost", "--json"), ("wait", "ghost")):  # not empty, not finished
            self.assertIn("no session named 'ghost'", self.fo(*cmd, rc=1).stderr)

    def test_busy_session_queues_messages_in_order(self):
        self.fo("run", "codex", "nap 1", "-n", "q", "--bg")
        self.fo("send", "q", "say second", "--bg")
        self.fo("send", "q", "say third", "--bg")
        self.assertEqual(self.fo("wait").stdout, "q: ok\n", "a bare wait waits for whatever is running or queued")
        self.assertEqual(self.fo("wait").stdout, "Nothing is running.\n")
        turns = self.json("log", "q")
        self.assertEqual([(t["n"], t["status"], t["text"]) for t in turns],
                         [(1, "ok", "napped"), (2, "ok", "second"), (3, "ok", "third")])
        self.assertIn("turn 3", self.fo("attach", "q").stdout, "attaching to an idle session shows its last turn")

    def test_stop_kills_the_worker_and_drops_the_queue(self):
        self.fo("run", "claude", "sleep st", "-n", "busy", "--bg")
        pids = self.pids("st")
        self.fo("send", "busy", "say never", "--bg")
        [row] = [r for r in self.json("ls") if r["name"] == "busy"]
        self.assertEqual((row["state"], row["queued"]), ("running", 1))
        self.assertIn("busy: stopped", self.fo("stop", "busy").stdout)
        self.assertAllDead(pids, "the worker outlived farmout stop")
        self.assertTrue(wait_until(lambda: self.json("ls")[0]["state"] == "stopped"))
        self.assertEqual([t["status"] for t in self.json("log", "busy")], ["stopped"])
        self.assertEqual(self.fo("wait", "busy", rc=1).stdout, "busy: stopped\n")

    def test_fork_claude_natively_and_others_with_the_transcript(self):
        self.fo("run", "claude", "say root", "-n", "p")
        self.assertEqual(self.fo("fork", "p", "recall", "-n", "f").stdout, "say root\n", "the fork remembers")
        argv, parent_id = self.last("claude")["argv"], self.meta("p")["native_id"]
        self.assertEqual(flag(argv, "--resume"), parent_id)
        self.assertIn("--fork-session", argv)
        self.assertNotEqual(flag(argv, "--session-id"), parent_id)
        self.assertEqual(self.fo("send", "p", "recall").stdout, "say root\n", "the parent is unchanged")

        self.fo("run", "codex", "say alpha", "-n", "c")
        self.fo("fork", "c", "say side question", "-n", "c2", "--worker", "copilot")
        brief = self.last("copilot")["brief"]
        self.assertTrue(brief.startswith("say side question\n\nContext: this continues an earlier conversation (with codex)"))
        ctx = Path(re.search(r"^- (\S+context\.md)$", brief, re.M).group(1)).read_text()
        self.assertIn("say alpha", ctx)
        self.assertIn("### Reply\n\nalpha", ctx)
        self.assertEqual(self.meta("c2")["parent"], "c")

    def test_send_can_hand_over_another_sessions_reply(self):
        self.fo("run", "codex", "say alpha", "-n", "a")
        self.fo("run", "claude", "say beta", "-n", "b")
        self.fo("send", "b", "--attach", "a", "say got it")  # options first works on every Python's argparse
        attached = re.search(r"^- (\S+/a/turns/0001\.md)$", self.last("claude")["brief"], re.M).group(1)
        self.assertEqual(Path(attached).read_text(), "alpha")

    def test_api_sessions_keep_their_history(self):
        api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        api.seen, api.release = [], threading.Event()
        threading.Thread(target=api.serve_forever, daemon=True).start()
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        env = {"FARMOUT_API_BASE": f"http://127.0.0.1:{api.server_port}/v1", "FARMOUT_API_MODEL": "m"}
        self.fo("run", "api", "first", "-n", "h", **env)
        self.fo("send", "h", "second", **env)
        self.assertEqual([m["role"] for m in api.seen[1]["body"]["messages"]], ["user", "assistant", "user"])
        self.assertEqual(api.seen[1]["body"]["messages"][0]["content"], "first")

    def test_rm_keeps_a_worktree_its_branches_work_in(self):
        self.repo()
        self.fo("run", "codex", "write f.txt hi", "--isolate", "-n", "iso")
        self.fo("fork", "iso", "say branch")
        p = self.fo("rm", "iso", rc=1)
        self.assertIn("iso-1 works in iso's worktree; remove it first (`farmout rm iso-1 iso`)", p.stdout + p.stderr)
        self.assertTrue(Path(self.meta("iso")["iso"]["worktree"]).is_dir(), "nothing was removed")
        self.fo("rm", "iso", "iso-1")  # branches go first, whatever order they're given in
        self.assertEqual(self.json("ls"), [])

    def test_rm_idle_goes_on_past_one_it_cant_remove(self):
        self.repo()
        self.fo("run", "codex", "say idle", "-n", "plain")  # oldest: removed after the one that can't be
        self.fo("run", "codex", "write f.txt hi", "--isolate", "-n", "iso")
        self.fo("fork", "iso", "nap 20", "--bg")
        self.assertTrue(wait_until(lambda: any(r["state"] == "running" for r in self.json("ls")), 20))
        p = self.fo("rm", "--idle", rc=1)
        self.assertIn("iso-1 works in iso's worktree", p.stderr)
        self.assertIn("removed plain", p.stdout, "the rest are removed anyway")
        self.fo("stop", "iso-1")

    def test_rm_keeps_a_session_with_messages_waiting_and_goes_on_past_errors(self):
        self.fo("run", "codex", "say one", "-n", "old")
        self.fo("run", "codex", "say two", "-n", "waits")
        self.fo("run", "codex", "say three", "-n", "stuck")
        inbox = self.state / "sessions" / "waits" / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "0001.json").write_text(json.dumps({"id": "m", "prompt": "say late"}))
        (self.state / "sessions" / "stuck" / "turns").chmod(0o500)  # its files can't be deleted

        def writable():  # so the test's own cleanup can delete what the trash kept
            for d in (self.state / "trash").rglob("*"):
                if d.is_dir():
                    d.chmod(0o700)
        self.addCleanup(writable)
        p = self.fo("rm", "old", "waits", "stuck", rc=1)
        self.assertIn("waits has messages waiting", p.stderr)
        self.assertIn("removed stuck", p.stdout, "what won't delete stays in the trash, not as half a session")
        self.assertIn("removed old", p.stdout, "the rest go anyway")
        self.assertEqual([r["name"] for r in self.json("ls")], ["waits"])

    def test_rm_removes_the_session_and_its_worktree(self):
        self.repo()
        self.fo("run", "codex", "write f.txt hi", "--isolate", "-n", "gone")
        wt = Path(self.meta("gone")["iso"]["worktree"])
        self.assertTrue(wt.is_dir())
        self.fo("rm", "gone")
        self.assertFalse(wt.exists())
        self.assertNotIn("farmout/gone", self.git("branch"))
        self.assertEqual(self.json("ls"), [])


class ReviewTest(CliBase):
    def test_a_finding_on_a_deleted_file_doesnt_sink_the_review(self):
        self.repo()
        (self.work / "b.py").write_text("z = 1\n")
        self.git("add", "b.py")
        self.git("commit", "-qm", "b")
        (self.work / "b.py").unlink()
        (self.work / "a.py").write_text("x = 1\ny = 3\n")
        self.reviewers_say(claude="b.py:1 | high | z is gone | import b | restore it\na.py:2 | low | y changed | always | y = 2\n")
        r = self.json("review", "-w", "claude")
        self.assertEqual(sorted((c["path"], c["valid"]) for c in r["clusters"]), [("a.py", True), ("b.py", False)])

    def test_parsing_keeps_one_word_defects_and_plus_lines(self):
        self.assertEqual(review_mod.parse("a.py:10 | high | bug | always | fix it", "codex")[0]["defect"], "bug")
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n a\n+++ b  (an added line starting ++)\n"
        self.assertEqual(review_mod.diff_positions(diff), {4: ("x.py", 1), 5: ("x.py", 2)})

    def test_a_line_cited_as_a_diff_position_is_mapped_to_the_file(self):
        self.repo()
        (self.work / "a.py").write_text("x = 1\ny = 3\n")
        # the diff: diff --git, index, ---, +++, @@, " x = 1", "-y = 2", "+y = 3": its 8th line is a.py's 2nd
        self.reviewers_say(claude="a.py:8 | high | defect | y is wrong | always | y = 2\n")
        [c] = self.json("review", "-w", "claude")["clusters"]
        self.assertEqual((c["path"], c["line"], c["valid"], c["defect"]), ("a.py", 2, True, "y is wrong"))

    def test_a_finding_that_copies_the_placeholder_path_means_the_changed_file(self):
        self.repo()
        (self.work / "a.py").write_text("x = 1\ny = 3\n")
        self.reviewers_say(claude="path:2 | high | y is wrong | always | y = 2\n")  # Sonnet did this on a real fix
        [c] = self.json("review", "-w", "claude", rc=0)["clusters"]
        self.assertEqual((c["path"], c["line"], c["valid"]), ("a.py", 2, True))
        (self.work / "b.py").write_text("z = 1\n")
        self.git("add", "b.py")
        self.git("commit", "-qm", "b")
        (self.work / "b.py").write_text("z = 2\n")
        [c] = self.json("review", "-w", "claude", rc=0)["clusters"]
        self.assertFalse(c["valid"], "two changed files: no guessing which one")

    def test_findings_are_merged_checked_and_gated(self):
        self.repo()
        (self.work / "b.py").write_text("def f(x):\n    return x / 0\n")
        self.git("add", "b.py")
        self.reviewers_say(codex="b.py:2 | high | division by zero | f(1) | guard it\n",
                           claude="* `b.py:2` | high | divides by zero | any call | check\nzzz.py:5 | low | no such file | - | -\n",
                           copilot="| b.py:99 | medium | past the end | x | y |\na/b.py:1 | low | style | - | -\n")
        out = self.json("review")
        good = [c for c in out["clusters"] if c["valid"]]
        self.assertEqual([(c["path"], c["line"], sorted(c["workers"])) for c in good],
                         [("b.py", 2, ["claude", "codex"]), ("b.py", 1, ["copilot"])])
        self.assertEqual(sorted((c["path"], c["line"]) for c in out["clusters"] if not c["valid"]),
                         [("b.py", 99), ("zzz.py", 5)])
        self.assertEqual(out["workers"], ["antigravity", "codex", "claude", "copilot"])
        diff = Path(out["diff"]).read_text()
        self.assertIn("+    return x / 0", diff)
        self.assertEqual(self.last("agy")["argv"][self.last("agy")["argv"].index("--model") + 1], "gemini-3.8-flash-low")
        text = self.fo("review", "--min-agree", "2").stdout
        self.assertRegex(text, r"2/4 +high +b\.py:2 +division by zero +codex, claude")
        self.assertNotIn("b.py:1 ", text)
        self.assertIn("Dropped 2 citing a file or line that doesn't exist", text)
        self.fo("review", "--min-agree", "2", "--fail-on", "high", rc=1)
        self.fo("review", "--min-agree", "3", "--fail-on", "high", rc=0)
        self.fo("review", "--staged", "--fail-on", "low", rc=1)

    def test_nothing_to_review_and_bad_base(self):
        self.repo()
        self.assertIn("Nothing to review", self.fo("review").stdout)
        self.assertIn("--base takes a git ref", self.fo("review", "--base=--output=x", rc=1).stderr)
        self.assertIn("git repository", self.fo("review", cwd=self.tmp, rc=1).stderr)

    def test_parse_tolerates_common_formats(self):
        text = ("1. `src/a.py:10` | HIGH | sql injection | name='x' | params\n"
                "- **src/b.py:7-9** | medium | race | two writers | lock\n"
                "| src/c.py:3 | low | leak | open() | with |\n"
                "NO FINDINGS\nnot a finding: x.py:3 high\n")
        got = [(f["path"], f["line"], f["severity"], f["defect"]) for f in review_mod.parse(text, "w")]
        self.assertEqual(got, [("src/a.py", 10, "high", "sql injection"), ("src/b.py", 7, "medium", "race"),
                               ("src/c.py", 3, "low", "leak")])

    def test_a_line_with_several_defects_keeps_them_apart(self):
        # A real four-model review of bench/cases/files_api (2026-11-17). Lines 36 and 48-49 each hold several
        # defects, models word them differently and cite neighbouring lines; the first-come merge this replaced
        # mixed them up (and credited codex with a missing quota row it never mentioned).
        said = {
            "antigravity": [
                (36, "high", "Path traversal vulnerability allows arbitrary filesystem read"),
                (18, "high", "SQL injection vulnerability via unsanitized format string"),
                (43, "high", "Unhandled None on nonexistent file causes TypeError: 'NoneType' object is not subscriptable crash"),
                (48, "high", "Unhandled None on missing quota row causes TypeError: 'NoneType' object is not subscriptable crash"),
                (49, "medium", "Missing transaction management / commit causes database mutations to be uncommitted or lost"),
                (27, "medium", "1-based pagination calculation uses incorrect offset formula causing page 1 to skip first 20 records and page 0/last page bugs"),
                (48, "medium", "Race condition in read-modify-write quota update causes quota corruption under concurrent deletions"),
                (37, "medium", "Unbounded file read into memory risks memory exhaustion / crash on large files")],
            "codex": [
                (18, "high", "SQL injection exposes arbitrary database data"),
                (27, "medium", "page 1 skips first 20 files; every page skips a full page"),
                (36, "high", "path traversal permits arbitrary file reads"),
                (36, "high", "unauthenticated cross-owner file download"),
                (49, "medium", "concurrent deletes can lose quota decrements")],
            "claude": [
                (18, "high", "SQL injection: owner interpolated into query"),
                (36, "high", "Path traversal in download: name unsanitised; absolute name makes os.path.join discard STORAGE; owner also unchecked"),
                (36, "high", "No authorization in download: any caller can read any owner's file"),
                (27, "high", "Off-by-one pagination: pages start at 1 but start = page * PAGE_SIZE"),
                (25, "medium", "page < 1 not handled: negative start slices from the end, page=0 returns the wrong rows"),
                (43, "medium", 'fetchone() returns None for an unknown id → TypeError on row["owner"] (500, not 404)'),
                (48, "medium", "Quota row missing → fetchone() None → TypeError after the permission check, before any delete"),
                (48, "medium", "Read-modify-write on quota is racy: lost update under concurrent deletes"),
                (49, "high", "No commit and no atomicity: the quota UPDATE, file removal and row DELETE are not in a transaction; if os.remove raises, the quota is already decremented, and the file is gone before the DB row is deleted"),
                (53, "medium", 'Path traversal on delete: row["name"] joined unsanitised, so a stored name like ../../x or an absolute path deletes arbitrary files'),
                (29, "low", "URL built from unescaped owner/name: /, ? or # in a name gives a broken or wrong link")],
            "copilot": [
                (19, "high", "SQL injection exposes other users' files or database data"),
                (36, "high", "arbitrary file reads via absolute paths and .. traversal, without ownership validation"),
                (27, "medium", "pagination skips the first page because page 1 starts at offset 20"),
                (44, "medium", "nonexistent file IDs crash with a TypeError instead of returning a controlled not-found result")],
        }
        found = [{"worker": w, "path": "files_api.py", "rel": "files_api.py", "line": n, "severity": s, "defect": d}
                 for w, rows in said.items() for n, s, d in rows]
        got = sorted((c["line"], c["workers"], c["defect"][:22]) for c in review_mod.merge(found))
        everyone = ["antigravity", "codex", "claude", "copilot"]
        self.assertEqual(got, [
            (18, everyone, "SQL injection vulnerab"),
            (25, ["claude"], "page < 1 not handled: "),
            (27, everyone, "Off-by-one pagination:"),
            (29, ["claude"], "URL built from unescap"),
            (36, everyone, "Path traversal vulnera"),
            (36, ["codex", "claude"], "unauthenticated cross-"),
            (37, ["antigravity"], "Unbounded file read in"),  # shares only "file" and "read" with line 36
            (43, ["antigravity", "claude", "copilot"], "Unhandled None on none"),
            (48, ["antigravity", "claude"], "Unhandled None on miss"),
            (48, ["antigravity", "codex", "claude"], "Race condition in read"),
            (49, ["antigravity", "claude"], "No commit and no atomi"),
            (53, ["claude"], "Path traversal on dele"),
        ])

    def test_a_second_real_run_groups_the_same_defects(self):
        # Another real four-model review of the same change (2026-11-17), worded differently: copilot cites line 44
        # for the missing-row crash the others put on 43, and claude's line 44 is a different defect; copilot's
        # "concurrent deletions" is the race the others call a race, not the missing quota row beside it.
        said = {
            "antigravity": [
                (36, "high", "Path traversal vulnerability allows arbitrary file read"),
                (18, "high", "SQL injection vulnerability via unescaped owner parameter"),
                (43, "high", "Unhandled None when file_id does not exist raises TypeError: 'NoneType' object is not subscriptable"),
                (33, "medium", "Off-by-one error in pagination calculation causes page 1 to return page 2's slice and skips first 20 items"),
                (48, "medium", "Unhandled None when quota row does not exist raises TypeError"),
                (48, "medium", "Lost updates / race condition on quota recalculation due to read-modify-write"),
                (49, "medium", "Changes are never committed to the database session, leaving operations unpersisted")],
            "codex": [
                (36, "high", "Path traversal permits arbitrary readable-file download"),
                (18, "high", "SQL injection exposes other owners’ file metadata"),
                (27, "medium", "Pagination skips first page"),
                (43, "medium", "Missing file ID crashes handler")],
            "claude": [
                (18, "high", "SQL injection: owner interpolated into query"),
                (36, "high", "Path traversal on download: name is joined unsanitized, and an absolute name replaces STORAGE entirely. owner is also unchecked."),
                (31, "high", "Missing authorization on download: no caller identity is checked, so anyone can read any owner's files"),
                (27, "high", "Off-by-one pagination: pages start at 1 but start = page * PAGE_SIZE skips the first page"),
                (25, "medium", "page < 1 is not handled: a negative or zero page gives a wrong slice, and a negative start slices from the end"),
                (43, "medium", "fetchone() returns None for an unknown id, so row[\"owner\"] raises TypeError"),
                (48, "medium", "The quota lookup can also return None and crash after the permission check. The quota update uses a read-modify-write, which is a race."),
                (49, "high", "Ordering and atomicity: quota is decremented and the file removed from disk before the DB row delete, and there is no commit or transaction"),
                (47, "high", "Path traversal on delete: row[\"name\"] from the DB is joined unsanitized and is then removed"),
                (44, "low", "user[\"admin\"] raises KeyError if the key is absent"),
                (23, "low", "The URL is built from the raw name and owner with no encoding")],
            "copilot": [
                (19, "high", "SQL injection in the owner filter"),
                (28, "medium", "Every normal first page is empty because slicing starts at offset 20"),
                (36, "high", "Arbitrary file read via path traversal or absolute names"),
                (44, "medium", "Missing file IDs crash with a TypeError instead of returning a controlled not-found result"),
                (48, "medium", "Concurrent deletions can corrupt quota accounting and raise during the second filesystem removal")],
        }
        found = [{"worker": w, "path": "files_api.py", "rel": "files_api.py", "line": n, "severity": s, "defect": d}
                 for w, rows in said.items() for n, s, d in rows]
        got = sorted((c["line"], c["workers"], c["defect"][:22]) for c in review_mod.merge(found))
        self.assertEqual(got, [
            (18, ["antigravity", "codex", "claude", "copilot"], "SQL injection vulnerab"),
            (23, ["claude"], "The URL is built from "),
            (25, ["claude"], "page < 1 is not handle"),
            (27, ["codex", "claude", "copilot"], "Off-by-one pagination:"),
            (31, ["claude"], "Missing authorization "),
            (33, ["antigravity"], "Off-by-one error in pa"),
            (36, ["antigravity", "codex", "claude", "copilot"], "Path traversal vulnera"),
            (43, ["antigravity", "codex", "claude", "copilot"], "Unhandled None when fi"),
            (44, ["claude"], "user[\"admin\"] raises K"),
            (47, ["claude"], "Path traversal on dele"),
            (48, ["antigravity"], "Unhandled None when qu"),
            (48, ["antigravity", "claude", "copilot"], "Lost updates / race co"),
            (49, ["antigravity", "claude"], "Ordering and atomicity"),
        ])

    def test_a_third_real_run_with_misplaced_citations(self):
        # A third real run (2026-11-17), unseen while the scoring was tuned. Copilot cites lines 29, 36 and 40 for
        # bugs on 36, 43 and 48: too far to join, so they stay single. Claude reports two pagination problems and
        # only the off-by-one joins the others; antigravity's missing-row crash on 43 joins codex's on 44.
        said = {
            "antigravity": [
                (18, "high", "SQL injection vulnerability via unsanitized f-string formatting"),
                (36, "high", "Path traversal vulnerability allows arbitrary filesystem read"),
                (43, "high", "Unhandled NoneType dereference crash when file does not exist"),
                (48, "high", "Unhandled NoneType dereference crash when quota record missing"),
                (27, "medium", "Off-by-one pagination logic skips first page entirely"),
                (55, "medium", "Missing database transaction commit leaves deletions and quota updates unpersisted")],
            "codex": [
                (18, "high", "SQL injection exposes other owners’ file metadata"),
                (27, "medium", "first requested page skips first 20 files"),
                (36, "high", "path traversal reads arbitrary server files"),
                (44, "medium", "nonexistent file ID crashes with TypeError instead of returning a handled not-found result")],
            "claude": [
                (18, "high", "SQL injection: owner interpolated into query"),
                (36, "high", "Path traversal on download: name/owner unvalidated in os.path.join"),
                (31, "high", "No authorization on download: caller identity never checked"),
                (27, "high", "Off-by-one pagination: pages documented 1-based but start = page * PAGE_SIZE"),
                (25, "medium", "page < 1 not handled"),
                (43, "medium", "fetchone() returns None for a nonexistent id → TypeError on row[\"owner\"] (500, not 404)"),
                (47, "high", "Path traversal on delete: row[\"name\"] from the DB joined unsanitised, and os.remove follows it"),
                (48, "medium", "Quota row may be missing → fetchone() is None → TypeError after the auth check, before any change; the file is then undeletable"),
                (49, "medium", "Non-atomic read-modify-write on quota: used read, then written"),
                (49, "high", "No commit and ordering unsafe: the quota is updated, then the file removed, then the row deleted, with no db.commit()/transaction handling"),
                (44, "medium", "user[\"admin\"] KeyError if the key is absent; the permission check also leaks existence of file ids (PermissionError vs. TypeError)"),
                (23, "low", "URL built from unescaped owner/name"),
                (22, "low", "f[\"size\"] may be NULL → TypeError in list"),
                (17, "low", "Whole result set fetched and paginated in Python")],
            "copilot": [
                (16, "high", "SQL injection in the owner filter can expose other users' files"),
                (29, "high", "path traversal allows arbitrary file reads outside STORAGE"),
                (25, "medium", "page 1 skips the first 20 files and returns the second page"),
                (40, "medium", "concurrent deletions can lose quota decrements and leave quota usage too high"),
                (36, "medium", "invalid file IDs cause an uncaught TypeError instead of a controlled not-found result")],
        }
        found = [{"worker": w, "path": "files_api.py", "rel": "files_api.py", "line": n, "severity": s, "defect": d}
                 for w, rows in said.items() for n, s, d in rows]
        got = sorted((c["line"], c["workers"], c["defect"][:22]) for c in review_mod.merge(found))
        self.assertEqual(got, [
            (17, ["claude"], "Whole result set fetch"),
            (18, ["antigravity", "codex", "claude", "copilot"], "SQL injection vulnerab"),
            (22, ["claude"], "f[\"size\"] may be NULL "),
            (23, ["claude"], "URL built from unescap"),
            (25, ["claude"], "page < 1 not handled"),
            (27, ["antigravity", "codex", "claude", "copilot"], "Off-by-one pagination:"),
            (29, ["copilot"], "path traversal allows "),
            (31, ["claude"], "No authorization on do"),
            (36, ["antigravity", "codex", "claude"], "Path traversal vulnera"),
            (36, ["copilot"], "invalid file IDs cause"),
            (40, ["copilot"], "concurrent deletions c"),
            (43, ["antigravity", "codex", "claude"], "Unhandled NoneType der"),
            (44, ["claude"], "user[\"admin\"] KeyError"),
            (47, ["claude"], "Path traversal on dele"),
            (48, ["antigravity", "claude"], "Unhandled NoneType der"),
            (49, ["claude"], "No commit and ordering"),
            (49, ["claude"], "Non-atomic read-modify"),
            (55, ["antigravity"], "Missing database trans"),
        ])

    def test_a_race_cited_one_line_off_joins_the_race(self):
        # A fourth real run (2026-11-17): copilot puts the racy quota update on line 48, beside the missing-row
        # crash, while the others put it on 49. What it says (concurrent, quota) must outweigh where it says it.
        said = {
            "antigravity": [
                (36, "high", "Path traversal vulnerability allows arbitrary filesystem read"),
                (17, "high", "SQL injection vulnerability via untrusted string interpolation"),
                (43, "high", "NoneType subscript crash when file does not exist"),
                (48, "high", "NoneType subscript crash if owner missing from quota table"),
                (49, "medium", "Race condition / quota desync via non-atomic read-modify-write without transaction/commit"),
                (52, "medium", "Database changes are never committed"),
                (27, "medium", "Off-by-one error in 1-based pagination skips the first page"),
                (24, "low", "Empty file list with page=1 incorrectly rejected / returns empty files with pages=1")],
            "codex": [
                (18, "high", "SQL injection exposes other owners’ file metadata"),
                (36, "high", "path traversal permits arbitrary file reads"),
                (47, "high", "stored malicious filename can delete arbitrary host files"),
                (27, "medium", "first page skips the first 20 files"),
                (44, "medium", "deleting a nonexistent file crashes with TypeError"),
                (49, "medium", "concurrent deletions can leave quota usage too high, permitting quota bypass")],
            "claude": [
                (18, "high", "SQL injection via f-string owner"),
                (36, "high", "Path traversal in download: no containment check, and absolute name replaces STORAGE in os.path.join"),
                (31, "high", "download has no authentication or authorization; any requester can read any owner's files"),
                (27, "high", "Pagination off-by-one: the docstring says pages start at 1, but start = page * PAGE_SIZE skips the first page"),
                (25, "medium", "No lower-bound check on page"),
                (43, "medium", "fetchone() returns None for an unknown file_id, so row[\"owner\"] raises TypeError (500 instead of 404)"),
                (47, "high", "Path traversal on delete: row[\"name\"] joins unsanitized into the path, and an absolute or .. name removes arbitrary files. Names from upload are likely user-controlled"),
                (48, "medium", "Quota lookup fetchone()[\"used\"] crashes on None if the owner has no quota row; this happens after the permission check but before any deletion, so the file can never be deleted"),
                (49, "medium", "Quota is read, then written as an absolute value (read-modify-write) → lost update under concurrent deletes or uploads; quota drifts"),
                (49, "high", "No atomicity or commit, and the order is wrong: the quota is updated and the file removed from disk before the row is deleted. If os.remove raises (permissions, I/O) or the DELETE fails, the quota is already decremented (never committed or rolled back). The DB and disk go out of sync, and a double delete decrements the quota twice"),
                (52, "medium", "Nothing calls db.commit(), so (sqlite3 default) the UPDATE and DELETE are never persisted if the caller doesn't commit. The file is gone from disk but the row remains, so later downloads or lists show a ghost entry"),
                (50, "low", "TOCTOU between os.path.exists and os.remove → FileNotFoundError under concurrent delete"),
                (23, "low", "f[\"url\"] interpolates raw owner and name without URL-encoding, so names containing /, ?, # or % produce broken or ambiguous links"),
                (22, "low", "f[\"size\"] can be NULL, and None / 1024 raises TypeError, so the whole listing fails")],
            "copilot": [
                (19, "high", "SQL injection in the owner filter exposes other users' files"),
                (36, "high", "downloads allow unauthenticated access and path traversal"),
                (27, "medium", "page 1 skips the first 20 files"),
                (44, "medium", "nonexistent file IDs crash with a TypeError"),
                (48, "medium", "concurrent deletions can lose quota updates")],
        }
        found = [{"worker": w, "path": "files_api.py", "rel": "files_api.py", "line": n, "severity": s, "defect": d}
                 for w, rows in said.items() for n, s, d in rows]
        got = sorted((c["line"], c["workers"], c["defect"][:22]) for c in review_mod.merge(found))
        self.assertEqual(got, [
            (18, ["antigravity", "codex", "claude", "copilot"], "SQL injection exposes "),
            (22, ["claude"], "f[\"size\"] can be NULL,"),
            (23, ["claude"], "f[\"url\"] interpolates "),
            (24, ["antigravity"], "Empty file list with p"),
            (25, ["claude"], "No lower-bound check o"),
            (27, ["antigravity", "codex", "claude", "copilot"], "Pagination off-by-one:"),
            (31, ["claude"], "download has no authen"),
            (36, ["antigravity", "codex", "claude", "copilot"], "Path traversal vulnera"),
            (43, ["antigravity", "codex", "claude", "copilot"], "NoneType subscript cra"),
            (47, ["codex", "claude"], "stored malicious filen"),
            (48, ["antigravity", "claude"], "NoneType subscript cra"),
            (49, ["antigravity", "codex", "claude", "copilot"], "Race condition / quota"),
            (49, ["claude"], "No atomicity or commit"),
            (50, ["claude"], "TOCTOU between os.path"),
            (52, ["antigravity", "claude"], "Database changes are n"),
        ])


class RaceAndPatchTest(CliBase):
    def test_tests_pick_the_winner_and_apply_lands_it(self):
        self.repo()
        out = self.json("race", "write fix.txt done", "-w", "codex,claude", "--test", "test -f fix.txt")
        self.assertEqual([(a["worker"], a["tests"], a["files"]) for a in out["attempts"]],
                         [("codex", True, 1), ("claude", True, 1)])
        self.assertIn(out["winner"], ("race-1-codex", "race-1-claude"))
        self.assertFalse((self.work / "fix.txt").exists(), "nothing lands until you apply")
        self.assertIn("+done", self.fo("diff", out["winner"]).stdout)
        self.assertIn("applied", self.fo("apply", out["winner"]).stdout)
        self.assertEqual((self.work / "fix.txt").read_text(), "done")
        self.assertIn("No winner", self.fo("race", "write g.txt x", "-w", "codex", "--test", "false", rc=1).stdout)

    def test_pull_request_from_an_isolated_session(self):
        self.repo()
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        gh = self.bindir / "gh"
        gh.write_text(f"#!/bin/sh\necho \"$@\" > {self.log}/gh.args\necho https://example.test/pull/1\n")
        gh.chmod(0o755)
        self.fo("run", "codex", "write feature.txt new feature", "--isolate", "-n", "feat")
        self.assertEqual(self.fo("apply", "feat", "--pr").stdout, "https://example.test/pull/1\n")
        self.assertEqual((self.log / "gh.args").read_text().split(), ["pr", "create", "--fill", "--head", "farmout/pr-feat"])
        log = subprocess.run(["git", "-C", str(remote), "log", "--format=%s|%an", "farmout/pr-feat"], capture_output=True,
                             text=True, check=True).stdout
        self.assertEqual(log.splitlines()[0], "write feature.txt new feature|t")
        self.assertFalse((self.work / "feature.txt").exists(), "a PR leaves your tree alone")

    def test_farmoutinclude_copies_ignored_files_into_worktrees(self):
        self.repo()
        (self.work / ".gitignore").write_text(".env\nbuild/\n")
        (self.work / ".env").write_text("TOKEN=1\n")
        (self.work / "build").mkdir()
        (self.work / "build" / "x").write_text("artifact")
        (self.work / ".farmoutinclude").write_text("# secrets the tests need\n.env\n")
        self.fo("run", "codex", "write out.txt ok", "--isolate", "-n", "inc")
        wt = Path(self.meta("inc")["iso"]["worktree"])
        self.assertEqual((wt / ".env").read_text(), "TOKEN=1\n")
        self.assertFalse((wt / "build").exists(), "only what .farmoutinclude names")
        self.assertNotIn(".env", self.fo("diff", "inc").stdout)


class SettingsAndLimitsTest(CliBase):
    def test_repo_and_user_settings_only_narrow(self):
        self.repo()
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text('{"workers": ["claude", "copilot", "antigravity"], "web": false}')
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"workers": ["codex", "claude", "antigravity"]}')
        self.assertIn("don't allow copilot (allowed: claude, antigravity)", self.fo("run", "copilot", "say x", rc=1).stdout)
        self.assertIn("forbid web access, and antigravity can't", self.fo("run", "antigravity", "say x", rc=1).stdout)
        self.fo("run", "claude", "say x")
        argv = self.last("claude")["argv"]
        self.assertEqual(flag(argv, "--tools"), "Read,Grep,Glob")
        self.assertNotIn("--allowedTools", argv)
        (self.work / ".farmout" / "settings.json").write_text('{"write": false}')
        self.assertIn("forbid write access", self.fo("run", "codex", "say x", "--write", rc=1).stdout)
        (self.work / ".farmout" / "settings.json").write_text('{"wrkers": []}')
        self.assertIn("unknown setting 'wrkers'", self.fo("run", "codex", "say x", rc=1).stdout)

    def test_toml_settings(self):
        if importlib.util.find_spec("tomllib") is None:
            self.skipTest("TOML needs Python 3.11+")
        self.repo()
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.toml").write_text('workers = ["codex"]\n')
        self.assertIn("don't allow claude", self.fo("run", "claude", "say x", rc=1).stdout)

    def test_a_fallback_in_a_new_worktree_starts_clean(self):
        self.repo()
        p = self.fo("run", "codex", "halfway codex junk.txt half done", "--isolate", "--fallback", "claude", "-n", "hw")
        self.assertRegex(p.stderr, r"codex is at a usage limit until \d\d:\d\d; retried on claude")
        self.assertNotIn("junk.txt", self.fo("diff", "hw").stdout, "the limited worker's half-done edit is gone")

    def test_a_worker_at_its_limit_falls_back_and_is_skipped(self):
        p = self.fo("run", "codex", "limited codex say rescued", "--fallback", "claude", "-n", "fb")
        self.assertEqual(p.stdout, "rescued\n")
        self.assertRegex(p.stderr, r"codex is at a usage limit until \d\d:\d\d; retried on claude")
        self.assertEqual(self.meta("fb")["worker"], "claude", "the session moves to the worker that answered")
        self.assertEqual(self.fo("send", "fb", "recall").stdout, "limited codex say rescued\n")
        limits = json.loads((self.state / "limits.json").read_text())
        self.assertGreater(limits["codex"]["until"], time.time() + 60, "'try again in 2 minutes' is honoured")
        self.assertEqual(limits["codex"]["why"], "limit")
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"fallback": ["codex", "copilot"]}')
        self.assertIn("[copilot-1 · copilot]", self.fo("run", "auto", "say hi").stderr, "auto skips a limited worker")
        codex_runs = len([r for r in self.runs() if r["name"] == "codex"])
        p = self.fo("run", "codex", "say direct", "--fallback", "claude")
        self.assertEqual(p.stdout, "direct\n")
        self.assertIn("codex is at a usage limit until", p.stderr)
        self.assertEqual(len([r for r in self.runs() if r["name"] == "codex"]), codex_runs, "a known-limited worker isn't tried")
        stats = self.json("stats")["workers"]
        self.assertEqual((stats["codex"]["limited"], stats["claude"]["ok"]), (1, 3))
        self.assertIn("codex is at a usage limit until", self.fo("stats").stdout)
        self.assertRegex(self.fo("status", "--line").stdout, r"codex limited till \d\d:\d\d")


class OneSubscriptionTest(CliBase):
    """What a single subscription gets: several of its models side by side, a fallback model for its limits, a
    conversation that changes model or worker, and nothing lost when a worker is logged out."""

    def test_fallback_is_on_by_default_and_settings_can_turn_it_off(self):
        p = self.fo("run", "codex", "limited codex say rescued", "-n", "d")
        self.assertEqual(p.stdout, "rescued\n")
        self.assertIn("retried on antigravity", p.stderr, "the next ready worker, in the usual order")
        (self.state / "limits.json").unlink()
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text('{"fallback": []}')
        p = self.fo("run", "codex", "limited codex say rescued", rc=1)
        self.assertIn("usage limit", p.stdout)
        (self.work / ".farmout" / "settings.json").write_text('{"workers": ["codex", "copilot"]}')
        (self.state / "limits.json").unlink()
        self.assertIn("retried on copilot", self.fo("run", "codex", "limited codex say ok").stderr,
                      "never a worker the repo doesn't allow")

    def test_a_fallback_can_be_another_model_of_the_same_worker(self):
        p = self.fo("run", "claude", "busymodel opus say from sonnet", "--model", "opus", "--fallback", "claude:sonnet",
                    "-n", "m")
        self.assertEqual(p.stdout, "from sonnet\n")
        self.assertIn("claude:opus is at a usage limit", p.stderr)
        self.assertEqual((self.meta("m")["worker"], self.meta("m")["model"]), ("claude", "sonnet"))
        limits = json.loads((self.state / "limits.json").read_text())
        self.assertEqual(list(limits), ["claude:opus"], "Opus at its limit leaves Sonnet free")
        self.fo("run", "claude", "say plain")
        self.assertEqual(flag(self.last("claude")["argv"], "--model"), "sonnet")

    def test_limits_follow_the_model_the_settings_chose(self):
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text('{"models": {"claude": "opus"}}')
        p = self.fo("run", "claude", "busymodel opus say from sonnet", "--fallback", "claude:opus,claude:sonnet")
        self.assertEqual(p.stdout, "from sonnet\n")
        self.assertEqual([flag(r["argv"], "--model") for r in self.runs()], ["opus", "sonnet"],
                         "claude ran Opus by the settings; claude:opus is that same lane, so it isn't tried again")
        self.assertEqual(list(json.loads((self.state / "limits.json").read_text())), ["claude:opus"])
        self.assertIn("left out claude:opus is at a usage limit", self.fo("review", rc=None).stderr)

    def test_a_lost_login_blocks_every_model_of_that_worker(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "limits.json").write_text(json.dumps({"claude": {"until": time.time() + 600, "why": "login"}}))
        p = self.fo("run", "claude:opus", "say elsewhere", "--fallback", "codex")
        self.assertEqual(p.stdout, "elsewhere\n")
        self.assertIn("claude isn't logged in", p.stderr)
        self.assertEqual([r["name"] for r in self.runs()], ["codex"], "claude wasn't tried")

    def test_a_lane_in_a_group_never_falls_back_onto_another_lane(self):
        p = self.json("ask", "limited codex say hi", "-w", "codex,claude")
        workers = [x["worker"] for x in p["answers"]]
        self.assertEqual(workers[1], "claude")
        self.assertNotIn(workers[0], ("codex", "claude"), "the codex lane went to a worker outside the group")

    def test_a_logged_out_worker_falls_back_and_says_how_to_log_in(self):
        p = self.fo("run", "codex", "loggedout codex say rescued", "--fallback", "claude")
        self.assertEqual(p.stdout, "rescued\n")
        self.assertIn("codex isn't logged in, or its subscription ended", p.stderr)
        self.assertEqual(json.loads((self.state / "limits.json").read_text())["codex"]["why"], "login")
        self.assertRegex(self.fo("status", "--line").stdout, r"codex logged out till")
        self.assertIn("skipped  codex isn't logged in", self.fo("doctor").stdout)
        (self.state / "limits.json").unlink()
        p = self.fo("run", "codex", "loggedout codex say x", "--fallback", "", rc=1)
        self.assertIn("run `codex login`", p.stdout, "with nowhere to go, it says how to fix it")

    def test_doctor_with_one_subscription_names_its_models(self):
        out = self.fo("doctor", PATH=str(self.make_bin(["claude"]))).stdout
        self.assertIn("`farmout bench -w claude:opus,claude:sonnet`", out)
        self.assertIn('`fallback = ["claude:sonnet"]`', out)

    def test_doctor_with_only_claude_code_says_how_to_use_it(self):
        out = self.fo("doctor", PATH=str(self.make_bin(["claude"])), FARMOUT_CLAUDE_DIR="").stdout
        self.assertIn("missing  claude", out)
        self.assertIn("`export FARMOUT_CLAUDE_MAIN=1` makes your main Claude login a worker", out)
        self.assertIn("No worker is ready yet", self.fo("doctor", PATH=str(self.make_bin([])), FARMOUT_CLAUDE_DIR="").stdout)

    def test_doctor_says_when_the_settings_cant_be_read(self):
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text("{not json")
        self.assertIn("this repository's settings can't be read", self.fo("doctor").stdout)

    def test_doctor_says_where_a_task_at_its_limit_goes(self):
        self.assertRegex(self.fo("doctor").stdout,
                         r"\d subscriptions ready\. .* a task at its limit moves on in this order here: antigravity, codex,")
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text(json.dumps({"fallback": ["copilot", "claude:opus"]}))
        self.assertIn("in this order here: copilot, claude:opus.", self.fo("doctor").stdout, "this repository's own order")

    def test_a_conversation_moves_to_another_worker_at_a_limit_with_its_history(self):
        self.fo("run", "codex", "say first answer", "-n", "c", "--fallback", "claude")
        p = self.fo("send", "c", "limited codex say second answer")
        self.assertEqual(p.stdout, "second answer\n")
        self.assertIn("· claude · ok", p.stderr)
        brief = self.last("claude")["brief"]
        self.assertIn("continues an earlier conversation (with codex)", brief)
        ctx = Path(re.search(r"^- (.+context-0002\.md)$", brief, re.M).group(1)).read_text()
        self.assertIn("say first answer", ctx)
        self.assertIn("first answer", ctx, "the reply too")
        self.assertNotIn("second answer", ctx, "not the turn being asked")
        self.assertEqual(self.meta("c")["worker"], "claude")
        self.assertEqual(self.fo("send", "c", "recall").stdout, "limited codex say second answer\n",
                         "from then on, claude's own conversation")

    def test_send_moves_a_conversation_to_another_model_or_worker(self):
        self.fo("run", "claude", "say one", "-n", "s")
        native = self.meta("s")["native_id"]
        p = self.fo("send", "s", "--model", "opus", "say two")
        self.assertIn("[s · claude → claude:opus]", p.stderr)
        argv = self.last("claude")["argv"]
        self.assertEqual((flag(argv, "--model"), flag(argv, "--resume")), ("opus", native), "same conversation, new model")
        self.fo("send", "s", "-w", "codex", "say three")
        self.assertIn("continues an earlier conversation (with claude)", self.last("codex")["brief"])
        self.assertEqual((self.meta("s")["worker"], self.meta("s")["model"]), ("codex", None))
        self.fo("send", "s", "-w", "copilot:gpt-x", "say four")
        self.assertEqual(flag(self.last("copilot")["argv"], "--model"), "gpt-x")
        self.assertIn("say three", self.last("copilot")["brief"] + Path(re.search(
            r"^- (.+context-0004\.md)$", self.last("copilot")["brief"], re.M).group(1)).read_text())
        self.assertIn("unavailable worker(s): nope", self.fo("send", "s", "-w", "nope", "x", rc=1).stderr)

    def test_one_subscription_reviews_with_two_models_or_twice(self):
        self.repo()
        (self.work / "a.py").write_text("x = 1 / 0\ny = 2\n")
        self.reviewers_say(codex="a.py:1 | high | divides by zero | import | fix\n")
        r = self.json("review", "-w", "codex,codex")
        self.assertEqual(r["workers"], ["codex", "codex#2"])
        self.assertEqual(r["clusters"][0]["workers"], ["codex", "codex#2"], "two runs of one model, counted apart")
        r = self.json("review", "-w", "codex:gpt-a,codex:gpt-b")
        self.assertEqual(sorted(r["sessions"]), sorted(f"{r['group']}-codex-gpt-{x}" for x in "ab"))
        self.assertEqual(len(r["clusters"][0]["workers"]), 2)
        p = self.fo("review", "-w", "claude:opus,claude:sonnet")
        self.assertIn("2 reviewers (claude:opus, claude:sonnet)", p.stdout)
        race = self.json("race", "write b.py ok", "-w", "claude:sonnet,claude:haiku")
        self.assertEqual(sorted(r["lane"] for r in race["attempts"]), ["claude:haiku", "claude:sonnet"])

    def test_a_review_never_falls_back_and_leaves_out_limited_workers(self):
        self.repo()
        (self.work / "a.py").write_text("x = 3\n")
        self.reviewers_say(codex="a.py:1 | low | x | y | z\n", claude="a.py:1 | low | x | y | z\n")
        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "limits.json").write_text(json.dumps({"copilot": {"until": time.time() + 600, "why": "limit"}}))
        r = self.json("review", "-w", "codex,claude,copilot")
        self.assertIn("copilot", r["workers"], "asked for by name: tried anyway")
        self.assertEqual(r["clusters"][0]["workers"], ["codex", "claude"], "and nobody stood in for anybody")
        (self.state / "limits.json").write_text(json.dumps({"copilot": {"until": time.time() + 600, "why": "limit"}}))
        p = self.fo("review", "--json")
        self.assertIn("left out copilot is at a usage limit", p.stderr)
        r = json.loads(p.stdout)
        self.assertNotIn("copilot", list(r["failed"]) + r["workers"])

    def test_the_models_setting_picks_each_workers_default(self):
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"models": {"codex": "gpt-y", "antigravity": "gemini-z"}}')
        self.fo("run", "codex", "say hi")
        self.assertEqual(flag(self.last("codex")["argv"], "-m"), "gpt-y")
        self.fo("run", "codex", "say hi", "--model", "gpt-q")
        self.assertEqual(flag(self.last("codex")["argv"], "-m"), "gpt-q", "an explicit model wins")
        self.repo()
        (self.work / "a.py").write_text("x = 3\n")
        self.fo("review", "-w", "antigravity")
        self.assertEqual(flag(self.last("agy")["argv"], "--model"), "gemini-z", "over review's own fast model")
        (cfg / "settings.json").write_text('{"models": {"codex": 5}}')
        self.assertIn("models maps a worker to its model", self.fo("run", "codex", "say x", rc=1).stdout)

    def test_bench_apply_uses_the_winning_model_here(self):
        self.repo()
        (self.work / "m.py").write_text("def ratio(a, b):\n    return a / b\n")
        self.git("add", "m.py")
        self.git("commit", "-qm", "add ratio")
        (self.work / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        self.git("commit", "-qam", "fix division by zero in ratio")
        (self.log / "review-claude-sonnet.txt").write_text("m.py:2 | high | divides by zero | ratio(1, 0) | guard\n")
        out = self.json("bench", "-n", "1", "-w", "claude:opus,claude:sonnet,codex", "--apply")
        applied = out["applied"]
        self.assertEqual(applied["models"], {"claude": "sonnet"}, "Sonnet found it, Opus didn't")
        self.assertEqual(applied["fallback"][0], "claude:sonnet")
        self.assertEqual(applied["fallback"][-2:], ["antigravity", "copilot"], "the workers bench didn't try stay last")
        written = json.loads((self.work / ".farmout" / "settings.json").read_text())
        self.assertEqual(written["models"], {"claude": "sonnet"})
        self.fo("run", "claude", "say hi")
        self.assertEqual(flag(self.last("claude")["argv"], "--model"), "sonnet")
        self.assertIn("· claude ·", self.fo("run", "auto", "say auto").stderr)
        self.assertEqual(flag(self.last("claude")["argv"], "--model"), "sonnet")
        before = len(self.runs())
        p = self.fo("bench", "--apply", "--last")
        self.assertIn("default models: claude → sonnet", p.stdout)
        self.assertEqual(len(self.runs()), before, "--last spends no quota")
        self.assertIn("--last goes with --apply", self.fo("bench", "--last", rc=1).stderr)
        (self.work / ".farmout" / "settings.json").unlink()
        (self.work / ".farmout" / "settings.toml").write_text('web = true\n')
        self.assertIn('[models]\nclaude = "sonnet"', self.fo("bench", "--apply", "--last", rc=1).stderr)
        (self.work / ".farmout" / "settings.toml").unlink()
        self.reviewers_say(codex="EXIT boom")
        p = self.fo("bench", "-n", "1", "-w", "codex", "--apply", rc=1)
        self.assertIn("codex", p.stdout, "the paid-for results still show")
        self.assertIn("nothing to apply", p.stderr)

    def test_a_pipeline_stage_can_use_two_models_of_one_worker(self):
        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True)
        (d / "two.json").write_text(json.dumps({"stage": [{"name": "a", "workers": ["claude:opus", "claude:sonnet"],
                                                           "prompt": "say piped"}]}))
        self.fo("pipe", "run", "two")
        models = sorted(flag(r["argv"], "--model") for r in self.runs() if r["name"] == "claude")
        self.assertEqual(models, ["opus", "sonnet"])

    def test_claudes_own_error_message_is_shown(self):
        p = self.fo("run", "claude", "claudeout", "--fallback", "", rc=1)
        self.assertTrue(p.stdout.startswith("RuntimeError: claude: Not logged in · Please run /login\n"), p.stdout)
        self.assertNotIn("terminal_reason", p.stdout)
        self.assertEqual(json.loads((self.state / "limits.json").read_text())["claude"]["why"], "login")

    def test_an_empty_answer_from_agy_is_a_failure(self):
        p = self.fo("run", "antigravity", "agyempty", "--fallback", "", rc=1)
        self.assertIn("agy returned an empty answer (it wanted a tool headless mode denies: command)", p.stdout)


class HookTest(CliBase):
    def test_pre_commit_hook_blocks_agreed_high_findings(self):
        self.repo()
        hooks = self.work / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")
        self.assertIn("--force", self.fo("hook", "install", rc=1).stderr)
        self.fo("hook", "install", "--force")
        self.assertTrue((hooks / "pre-commit.farmout-backup").exists())
        (self.work / "a.py").write_text("x = 1 / 0\ny = 2\n")
        self.git("add", "a.py")
        self.reviewers_say(codex="a.py:1 | high | crash | import | fix\n", claude="a.py:1 | high | ZeroDivision | load | fix\n")
        env = {**os.environ, **self.env}
        commit = subprocess.run(["git", "-C", str(self.work), "commit", "-qm", "bad"], capture_output=True, text=True, env=env)
        self.assertNotEqual(commit.returncode, 0, commit.stdout + commit.stderr)
        self.assertIn("2/4", commit.stdout + commit.stderr)
        skipped = subprocess.run(["git", "-C", str(self.work), "commit", "-qm", "bad"], capture_output=True, text=True,
                                 env={**env, "FARMOUT_SKIP": "1"})
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.fo("hook", "uninstall")
        self.assertEqual((hooks / "pre-commit").read_text(), "#!/bin/sh\necho mine\n", "the old hook is back")


class PipelineTest(CliBase):
    def pipeline(self, name, defn):
        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(defn))

    def test_stages_placeholders_gates_and_approval(self):
        self.repo()
        self.pipeline("demo", {"params": {"word": "hello"}, "stage": [
            {"name": "plan", "worker": "claude", "prompt": "say plan for {word} {args}"},
            {"name": "build", "worker": "codex", "isolate": True, "prompt": "write built.txt from {plan}",
             "test": "test -f built.txt", "gate": "tests"},
            {"name": "ship", "worker": "copilot", "approve": True, "prompt": "say shipping {build.patch}"},
            {"name": "cleanup", "worker": "antigravity", "on": "failure", "prompt": "say cleaning"}]})
        state = self.json("pipe", "run", "demo", "the", "args")
        self.assertEqual(state["status"], "waiting")
        self.assertEqual([s["status"] for s in state["stages"]], ["ok", "ok", "waiting", "pending"])
        self.assertEqual(Path(state["stages"][0]["output"]).read_text(), "plan for hello the args\n")
        build = self.last("codex")
        self.assertIn(state["stages"][0]["output"], build["brief"], "{plan} is the plan's output file, also listed")
        self.assertIn("tests passed", state["stages"][1]["gate"])
        state = self.json("pipe", "approve", state["id"])
        self.assertEqual([s["status"] for s in state["stages"]], ["ok", "ok", "ok", "skipped"])
        self.assertIn("shipping " + state["stages"][1]["patch"], Path(state["stages"][2]["output"]).read_text())
        self.assertEqual(state["status"], "ok")

    def test_a_repository_cant_take_over_a_built_in_or_ship_a_broken_stage(self):
        self.pipeline("fix", {"stage": [{"name": "x", "worker": "codex", "isolate": True, "prompt": "say x",
                                         "test": "touch hijacked", "gate": "tests"}]})
        self.pipeline("mine", {"stage": [{"name": "x", "worker": "codex", "prompt": "say x"}]})
        listed = {p["name"]: p["source"] for p in self.json("pipe", "ls")}
        self.assertEqual(listed["fix"], "built-in", "a cloned repo's fix.json must not replace farmout's fix")
        self.assertTrue(listed["mine"].endswith("mine.json"), "new names from the repo are fine")
        for stage, why in (({"name": "b", "foreach": "t", "worker": "codex", "prompt": "do {item}", "test": "echo {item}"},
                            "{item} works only in a foreach stage's prompt"),
                           ({"name": "b", "workers": "codex", "prompt": "do it"}, "workers must be a list")):
            self.pipeline("bad", {"stage": [{"name": "t", "worker": "claude", "prompt": "lines a|b"}, stage]})
            self.assertIn(why, self.fo("pipe", "run", "bad", rc=1).stderr)

    def test_resume_asks_again_for_an_approval_whose_input_changed(self):
        self.pipeline("ap", {"stage": [
            {"name": "build", "worker": "codex", "prompt": "say v1", "gate": 'contains "v2"', "on": "always"},
            {"name": "ship", "worker": "claude", "approve": True, "on": "always", "prompt": "say shipping {build}"}]})
        state = self.json("pipe", "run", "ap", rc=None)
        self.assertEqual([s["status"] for s in state["stages"]], ["gated", "waiting"])
        state = self.json("pipe", "approve", state["id"], rc=None)
        self.assertEqual(state["stages"][1]["status"], "ok", "approved once, it ran")
        state = self.json("pipe", "resume", state["id"], rc=None)  # build runs again, with new output
        self.assertEqual(state["stages"][1]["status"], "waiting", "the old approval doesn't cover new input")

    def test_a_gate_stops_the_run_and_resume_continues(self):
        self.pipeline("g", {"stage": [
            {"name": "one", "worker": "codex", "prompt": "say step one", "gate": 'contains "step"'},
            {"name": "two", "worker": "claude", "prompt": "say not yet", "gate": 'contains "go"'},
            {"name": "three", "worker": "copilot", "prompt": "say done"}]})
        p = self.fo("pipe", "run", "g", rc=1)
        self.assertIn("✗ two: gated", p.stderr)
        state = self.json("pipe", "status", "g-1", rc=1)
        self.assertEqual([s["status"] for s in state["stages"]], ["ok", "gated", "skipped"])
        self.pipeline("g", {"stage": []})  # the run keeps the definition it started with
        self.assertEqual(self.fo("pipe", "resume", "g-1", rc=1).returncode, 1)
        state = self.json("pipe", "status", "g-1", rc=1)
        self.assertEqual(state["stages"][1]["attempt"], 2, "resume reruns the gated stage, under new session names")
        self.assertTrue((self.state / "sessions" / "g-1-two-2-claude").exists())

    def test_foreach_fans_items_out_round_robin(self):
        self.repo()
        self.pipeline("fan", {"stage": [
            {"name": "tasks", "worker": "claude", "prompt": "lines alpha|beta|gamma"},
            {"name": "build", "foreach": "tasks", "workers": ["codex", "copilot"], "isolate": True,
             "prompt": "write {item}.txt made"}]})
        state = self.json("pipe", "run", "fan")
        self.assertEqual(state["status"], "ok")
        names = state["stages"][1]["sessions"]
        self.assertEqual(names, ["fan-1-build-1", "fan-1-build-2", "fan-1-build-3"])
        self.assertEqual([self.meta(n)["worker"] for n in names], ["codex", "copilot", "codex"])
        out = Path(state["stages"][1]["output"]).read_text()
        self.assertEqual(len(re.findall(r"^- .+\.patch$", out, re.M)), 3)
        for n, item in zip(names, ("alpha", "beta", "gamma")):
            self.assertIn("+made", self.fo("diff", n).stdout)
            self.assertIn(f"{item}.txt", self.fo("diff", n).stdout)

    def test_failure_runs_only_on_failure_stages(self):
        self.pipeline("f", {"stage": [
            {"name": "bad", "worker": "codex", "prompt": "fail nope"},
            {"name": "next", "worker": "claude", "prompt": "say skipped"},
            {"name": "rescue", "worker": "copilot", "on": "failure", "prompt": "say rescue"},
            {"name": "always", "worker": "copilot", "on": "always", "prompt": "say always"}]})
        state = self.json("pipe", "run", "f", rc=1)
        self.assertEqual([s["status"] for s in state["stages"]], ["failed", "skipped", "ok", "ok"])
        self.assertEqual(state["status"], "failed")

    def test_definition_errors_are_explained(self):
        cases = [({"stage": [{"name": "a", "prompt": "x", "wrker": "codex"}]}, "unknown key(s) wrker"),
                 ({"stage": [{"name": "a", "prompt": "x", "gate": "maybe"}]}, "gate must be"),
                 ({"stage": [{"name": "a", "prompt": "{b}"}, {"name": "b", "prompt": "x"}]}, "{b} is not an earlier stage"),
                 ({"stage": [{"name": "a", "prompt": "x"}, {"name": "a", "prompt": "y"}]}, "unique identifier"),
                 ({"stage": [{"name": "a", "foreach": "z", "prompt": "x"}]}, "foreach must name an earlier stage"),
                 ({"stage": [{"name": "a", "skill": "ask"}]}, "needs a prompt"),
                 ({"stage": []}, "needs a list of stages")]
        for defn, why in cases:
            with self.subTest(why):
                self.pipeline("bad", defn)
                self.assertIn(why, self.fo("pipe", "run", "bad", rc=1).stderr)
        self.assertIn("needs: args", self.fo("pipe", "run", "spec", rc=1).stderr)
        self.assertIn("no pipeline named 'nope'", self.fo("pipe", "run", "nope", rc=1).stderr)

    def test_builtin_fix_reviews_fixes_and_checks(self):
        self.repo()
        (self.work / "a.py").write_text("x = 1 / 0\ny = 2\n")
        self.reviewers_say(codex="a.py:1 | high | crash | import | fix\n", claude="a.py:1 | high | ZeroDivision | load | fix\n")
        state = self.json("pipe", "run", "fix")
        self.assertEqual([(s["name"], s["status"]) for s in state["stages"]],
                         [("review", "ok"), ("fix", "ok"), ("check", "ok")])
        self.assertIn("agree = 2", state["stages"][0]["gate"])
        fix_brief = self.last("codex")["brief"]
        self.assertIn(state["stages"][0]["output"], fix_brief)
        self.reviewers_say(claude="NO FINDINGS")
        state = self.json("pipe", "run", "fix", rc=1)
        self.assertEqual([s["status"] for s in state["stages"]], ["gated", "skipped", "skipped"])

    def test_background_run_status_and_stop(self):
        self.pipeline("slow", {"stage": [{"name": "wait", "worker": "codex", "prompt": "sleep pipe"}]})
        self.assertIn("runs in the background", self.fo("pipe", "run", "slow", "--bg").stdout)
        pids = self.pids("pipe")
        self.assertEqual(self.json("pipe", "status", "slow-1", rc=1)["status"], "running")
        self.assertIn("stopped", self.fo("pipe", "stop", "slow-1").stdout)
        self.assertAllDead(pids, "the pipeline's worker outlived pipe stop")
        self.assertTrue(wait_until(lambda: self.json("pipe", "status", "slow-1", rc=None)["status"] == "stopped"))

    def test_reject_fails_the_run(self):
        self.pipeline("r", {"stage": [{"name": "go", "worker": "codex", "approve": True, "prompt": "say go"}]})
        self.assertEqual(self.json("pipe", "run", "r")["status"], "waiting")
        state = self.json("pipe", "approve", "r-1", "--reject", rc=1)
        self.assertEqual((state["status"], state["stages"][0]["note"]), ("failed", "rejected by you"))

    def test_toml_pipeline(self):
        if importlib.util.find_spec("tomllib") is None:
            self.skipTest("TOML needs Python 3.11+")
        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True)
        (d / "t.toml").write_text('description = "toml"\n[[stage]]\nname = "a"\nworker = "codex"\nprompt = "say from toml"\n')
        state = self.json("pipe", "run", "t")
        self.assertEqual(Path(state["stages"][0]["output"]).read_text(), "from toml\n")
        self.assertIn("toml", self.fo("pipe", "ls").stdout)


class RegressionTest(CliBase):
    """Bugs found when farmout reviewed its own code with four models."""

    def test_crlf_files_survive_isolation_both_ways(self):
        self.repo()
        (self.work / "w.txt").write_bytes(b"one\r\ntwo\r\n")
        self.git("add", "w.txt")
        self.git("commit", "-qm", "crlf")
        (self.work / "w.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")  # uncommitted, CRLF
        self.fo("run", "codex", "write new.txt x", "--isolate", "-n", "crlf")
        wt = Path(self.meta("crlf")["iso"]["worktree"])
        self.assertEqual((wt / "w.txt").read_bytes(), b"one\r\ntwo\r\nthree\r\n", "the base keeps CRLFs")
        (wt / "w.txt").write_bytes(b"one\r\ntwo\r\nthree\r\nfour\r\n")
        self.fo("apply", "crlf")
        self.assertEqual((self.work / "w.txt").read_bytes(), b"one\r\ntwo\r\nthree\r\nfour\r\n")

    def test_farmouts_own_hook_does_not_fire_on_internal_commits(self):
        self.repo()
        self.fo("hook", "install")
        (self.work / "a.py").write_text("x = 3\ny = 2\n")
        self.reviewers_say(codex="a.py:1 | high | x | y | z\n", claude="a.py:1 | high | x | y | z\n")
        self.fo("run", "codex", "write f.txt ok", "--isolate", "-n", "h")
        self.assertFalse([r for r in self.runs() if r["brief"].startswith("Review a code change")],
                         "making the worktree's base commit started a review")

    def test_a_dropped_message_ends_the_wait(self):
        self.fo("run", "codex", "sleep drop", "-n", "d", "--bg")
        pids = self.pids("drop")
        waiter = subprocess.Popen([sys.executable, str(SERVER), "send", "d", "say queued"], env=self.env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertTrue(wait_until(lambda: self.json("ls")[0]["queued"] == 1))
        self.fo("stop", "d")
        out, err = waiter.communicate(timeout=20)
        self.assertEqual(waiter.returncode, 1)
        self.assertIn("dropped before it ran", out)
        self.assertAllDead(pids, "worker survived")

    def test_a_worktrees_own_settings_cant_loosen_the_repos(self):
        self.repo()
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text('{"web": false}')
        self.fo("run", "codex", "write .farmout/settings.json {}", "--isolate", "-n", "w")
        wt = Path(self.meta("w")["iso"]["worktree"])
        self.assertEqual((wt / ".farmout" / "settings.json").read_text(), "{}")
        self.fo("run", "claude", "say from inside", "-C", str(wt))
        self.assertEqual(flag(self.last("claude")["argv"], "--tools"), "Read,Grep,Glob")
        self.fo("run", "codex", "say off", "-C", str(wt))
        self.assertIn('web_search="disabled"', self.last("codex")["argv"])

    def test_one_executor_per_pipeline_run(self):
        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True)
        (d / "s.json").write_text(json.dumps({"stage": [{"name": "w", "worker": "codex", "prompt": "sleep one"}]}))
        self.fo("pipe", "run", "s", "--bg")
        self.pids("one")
        self.assertIn("still running", self.fo("pipe", "resume", "s-1", rc=1).stderr)
        self.assertIn("stopped", self.fo("pipe", "stop", "s-1").stdout)

    def test_citations_relative_to_a_subdirectory_count(self):
        self.repo()
        (self.work / "pkg").mkdir()
        (self.work / "pkg" / "mod.py").write_text("a = 1\nb = 2\n")
        self.reviewers_say(codex="mod.py:2 | high | b is wrong | x | y\n", claude="pkg/mod.py:2 | high | b wrong | x | y\n")
        [c] = [c for c in self.json("review", "-w", "codex,claude")["clusters"] if c["valid"]]
        self.assertEqual((c["path"], c["line"], sorted(c["workers"])), ("pkg/mod.py", 2, ["claude", "codex"]))

    def test_isolated_worktree_is_kept_when_the_first_turn_is_stopped(self):
        self.repo()
        self.fo("run", "codex", "sleep iso", "--isolate", "-n", "st", "--bg")
        self.pids("iso")
        self.fo("stop", "st")
        self.assertTrue(wait_until(lambda: self.json("ls")[0]["state"] == "stopped"))
        self.assertIsNotNone(self.meta("st")["iso"])
        self.assertIn("+x", self.fo("send", "st", "write after.txt x").stdout + self.fo("diff", "st").stdout)

    def test_auto_hooks_and_delegate_names(self):
        self.repo()
        (self.work / ".farmout").mkdir()
        (self.work / ".farmout" / "settings.json").write_text('{"workers": ["copilot"]}')
        self.assertIn("[copilot-1 · copilot]", self.fo("run", "auto", "say hi").stderr)
        hooks = self.work / ".git" / "hooks"
        (hooks / "pre-commit").write_text("#!/bin/sh\necho b\n")
        (hooks / "pre-commit.farmout-backup").write_text("#!/bin/sh\necho a\n")
        self.assertIn("already holds an earlier hook", self.fo("hook", "install", "--force", rc=1).stderr)
        self.assertEqual((hooks / "pre-commit.farmout-backup").read_text(), "#!/bin/sh\necho a\n")

    def test_rewrite_only_whole_paths(self):
        from farmout.isolate import rewrite
        top, wt = "/r/app", "/r/app/.farmout/worktrees/s"
        self.assertEqual(rewrite("cd /r/app && ls /r/app/src", top, wt), f"cd {wt} && ls {wt}/src")
        self.assertEqual(rewrite("see /r/app2 and /r/app.", top, wt), f"see /r/app2 and {wt}.")
        self.assertEqual(rewrite(f"open {wt}/x.py", top, wt), f"open {wt}/x.py", "already inside the worktree")


class HooksAndSkillsTest(CliBase):
    """P3: farmout's own hooks, trust, redaction, skills."""

    def settings(self, data, repo=False):
        d = (self.work / ".farmout") if repo else (self.home / ".config" / "farmout")
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(json.dumps(data))

    def test_task_start_hooks_rewrite_or_block_and_task_end_hooks_see_the_result(self):
        seen = self.tmp / "events.jsonl"
        self.settings({"hooks": [
            {"event": "TaskStart", "command": f"{sys.executable} -c \"import json,sys; e=json.load(sys.stdin); "
                                              "print(json.dumps({'prompt': e['prompt'].replace('say', 'say HOOKED')}))\""},
            {"event": "TaskStart", "matcher": "^copilot$", "command": "echo not copilot >&2; exit 2"},
            {"event": "TaskEnd", "command": f"cat >> {seen}"},
            {"event": "SessionStart", "command": f"cat >> {seen}"},
            {"event": "MessageReceived", "command": f"cat >> {seen}"}]})
        p = self.fo("run", "codex", "say hi", "-n", "h")
        self.assertEqual(p.stdout, "HOOKED hi\n")
        self.assertIn("rewrote the brief", p.stderr)
        p = self.fo("run", "copilot", "say hi", rc=1)
        self.assertIn("Blocked: hook `echo not copilot >&2; exit 2`: not copilot", p.stdout)
        self.assertNotIn("copilot", [r["name"] for r in self.runs()], "a blocked task never reaches the worker")
        events = [json.loads(line) for line in seen.read_text().splitlines()]
        kinds = [(e["event"], e.get("session")) for e in events]
        self.assertIn(("SessionStart", "h"), kinds)
        self.assertIn(("MessageReceived", "h"), kinds)
        end = next(e for e in events if e["event"] == "TaskEnd" and e["worker"] == "codex")
        self.assertEqual((end["ok"], end["session"], end["text"]), (True, "h", "HOOKED hi"))

    def test_repo_hooks_run_only_when_trusted_and_unchanged(self):
        self.repo()
        flagfile = self.tmp / "ran"
        self.settings({"hooks": [{"event": "TaskEnd", "command": f"touch {flagfile}"}]}, repo=True)
        p = self.fo("run", "codex", "say a")
        self.assertIn("defines hooks you haven't trusted", p.stderr)
        self.assertFalse(flagfile.exists())
        self.assertIn("TaskEnd: touch", self.fo("trust").stdout)
        self.fo("run", "codex", "say b")
        self.assertTrue(flagfile.exists())
        flagfile.unlink()
        self.settings({"hooks": [{"event": "TaskEnd", "command": f"touch {flagfile} changed"}]}, repo=True)
        self.fo("run", "codex", "say c")
        self.assertFalse(flagfile.exists(), "an edited settings file needs trusting again")
        self.assertIn("haven't trusted", self.fo("hooks").stdout)
        self.fo("trust", "--revoke")
        self.assertIn("haven't trusted", self.fo("trust", "--list").stdout)

    def test_a_branch_start_hook_can_message_the_parent(self):
        hook = self.tmp / "tell_parent.py"
        hook.write_text("import json, subprocess, sys\n"
                        "e = json.load(sys.stdin)\n"
                        "if e.get('parent'):\n"
                        f"    subprocess.run([sys.executable, {str(SERVER)!r}, 'send', e['parent'], 'say hooked', '--bg'])\n")
        self.settings({"hooks": [{"event": "SessionStart", "command": f"{sys.executable} {hook}", "timeout": 20}]})
        self.fo("run", "codex", "say one", "-n", "t")
        t0 = time.time()
        self.fo("fork", "t", "say branch")
        self.assertLess(time.time() - t0, 15, "the hook's send didn't wait on the branch being made")
        self.assertTrue(wait_until(lambda: "say hooked" in [x["prompt"] for x in self.json("log", "t")], 20))

    def test_redaction_and_bad_hooks(self):
        self.settings({"redact": True, "hooks": [{"event": "TaskEnd", "command": "exit 7"}]})
        p = self.fo("run", "codex", "say key sk-ant-abcdefghijklmnopqrstuvwxyz0123 and password=hunter2hunter2")
        self.assertEqual(p.stdout, "key [REDACTED] and password=[REDACTED]\n")
        self.assertIn("redacted 2 secrets", p.stderr)
        self.assertIn("hook `exit 7` failed", p.stderr)
        self.fo("run", "codex", "say piped", input="AKIAABCDEFGHIJKLMNOP\n")
        stash = re.search(r"^- (\S+-stdin\.txt)$", self.last("codex")["brief"], re.M).group(1)
        self.assertEqual(Path(stash).read_text(), "[REDACTED]\n")
        self.settings({"hooks": [{"event": "Never", "command": "x"}]})
        self.assertIn("needs an event", self.fo("run", "codex", "say x", rc=1).stdout)

    def test_skills_run_from_project_and_yours(self):
        self.repo()
        d = self.work / ".farmout" / "skills" / "hello"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: hello\ndescription: >\n  says hello\n  politely\nworkers: [copilot]\n---\nsay hello $ARGUMENTS\n")
        rows = self.json("skill", "ls")
        self.assertEqual([(r["name"], r["description"], r["origin"]) for r in rows], [("hello", "says hello politely", "project")])
        self.assertEqual(self.fo("hello", "world").stdout, "hello world\n", "farmout <skill> is short for skill run")
        self.assertEqual(self.last("copilot")["brief"].splitlines()[0], "say hello world")
        self.assertIn(str(d), self.last("copilot")["brief"], "the skill's folder is handed over")
        rv = self.home / ".config" / "farmout" / "skills" / "rv"
        rv.mkdir(parents=True)
        (rv / "SKILL.md").write_text("---\nname: rv\ndescription: x\nmode: review\n---\nOnly look at errors.\n")
        (self.work / "a.py").write_text("x = 1 / 0\ny = 2\n")
        self.reviewers_say(codex="a.py:1 | high | ZeroDivisionError | import | fix\n")
        out = self.json("rv", "-w", "codex")
        self.assertEqual([(c["path"], c["line"]) for c in out["clusters"]], [("a.py", 1)])
        self.assertIn("Focus: Only look at errors.", self.last("codex")["brief"])

    def test_skill_add_from_anywhere_but_code_only_from_the_store(self):
        src = self.tmp / "skillrepo"
        (src / "skills" / "plain").mkdir(parents=True)
        (src / "skills" / "plain" / "SKILL.md").write_text("---\nname: plain\ndescription: d\n---\nsay plain\n")
        (src / "skills" / "coded" / "scripts").mkdir(parents=True)
        (src / "skills" / "coded" / "SKILL.md").write_text("---\nname: coded\ndescription: d\n---\nrun the script\n")
        (src / "skills" / "coded" / "scripts" / "x.py").write_text("print(1)\n")
        self.git("init", "-q", cwd=src)
        self.git("add", ".", cwd=src)
        self.git("commit", "-qm", "skills", cwd=src)
        self.assertIn("choose one with --path: skills/coded, skills/plain", self.fo("skill", "add", f"file://{src}", rc=1).stderr)
        p = self.fo("skill", "add", f"file://{src}", "--path", "skills/plain")
        self.assertIn("Installed skill plain", p.stdout)
        info = json.loads((self.home / ".config" / "farmout" / "skills" / "plain" / ".farmout-source.json").read_text())
        self.assertRegex(info["commit"], r"^[0-9a-f]{40}$")
        self.assertIn("contains code (scripts/x.py)", self.fo("skill", "add", f"file://{src}", "--path", "skills/coded", rc=1).stderr)
        self.assertIn("already installed", self.fo("skill", "add", f"file://{src}", "--path", "skills/plain", rc=1).stderr)
        p = self.fo("skill", "add", str(src / "skills" / "coded"))
        self.assertIn("installed as it is, from your own files", p.stdout)
        self.fo("skill", "rm", "plain")
        self.assertEqual(sorted(r["name"] for r in self.json("skill", "ls")), ["coded"])

    def test_frontmatter_subset(self):
        from farmout.skills import frontmatter
        meta, body = frontmatter("---\nname: x\nworkers:\n  - codex\n  - 'claude'\nwrite: true\nn: 3\n"
                                 "metadata:\n  owner: me\ndescription: |\n  line one\n  line two\n---\nbody\n")
        self.assertEqual(meta, {"name": "x", "workers": ["codex", "claude"], "write": True, "n": 3,
                                "metadata": {"owner": "me"}, "description": "line one\nline two"})
        self.assertEqual(body, "body\n")
        self.assertEqual(frontmatter("no frontmatter"), ({}, "no frontmatter"))

    def test_a_pipeline_stage_can_run_your_skill(self):
        d = self.work / ".farmout" / "skills" / "greet"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: greet\ndescription: x\nworkers: [claude]\n---\nsay greetings {not a placeholder} $ARGUMENTS\n")
        pdir = self.work / ".farmout" / "pipelines"
        pdir.mkdir(parents=True)
        (pdir / "g.json").write_text(json.dumps({"stage": [{"name": "a", "skill": "greet", "prompt": "to {args}"}]}))
        state = self.json("pipe", "run", "g", "everyone")
        self.assertEqual(Path(state["stages"][0]["output"]).read_text(), "greetings {not a placeholder} to everyone\n")


class StoreTest(CliBase):
    """P4: the store, every file pinned by SHA-256, and tools."""

    def setUp(self):
        super().setUp()
        self.store = self.tmp / "store"
        import shutil
        shutil.copytree(SERVER.parent / "store", self.store)
        self.env["FARMOUT_STORE"] = str(self.store / "index.json")

    def test_index_matches_the_store_files(self):
        out = subprocess.run([sys.executable, str(SERVER.parent / "scripts" / "build_store_index.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_install_hook_skill_and_tool_with_verified_hashes(self):
        names = [e["name"] for e in self.json("store", "ls")["entries"]]
        self.assertEqual(sorted(names), ["guard-secrets", "notify", "prune", "security-review", "test-gaps"])
        self.assertIn("sha256 verified", self.fo("store", "show", "guard-secrets").stdout)
        self.fo("store", "add", "guard-secrets")
        (self.work / ".env").write_text("X=1\n")
        p = self.fo("run", "codex", "say leak", "-f", ".env", rc=1)
        self.assertIn("look like secrets", p.stdout)
        self.fo("store", "add", "security-review")
        self.assertEqual([r["name"] for r in self.json("skill", "ls")], ["security-review"])
        self.fo("store", "add", "prune")
        self.fo("run", "codex", "say old", "-n", "old")
        self.assertRegex(self.fo("tool", "run", "prune", "--days", "0", "--dry-run").stdout, r"^Would delete: .*\bold\b")
        self.assertIn("removed hook guard-secrets", self.fo("store", "rm", "guard-secrets").stdout)
        self.fo("run", "codex", "say fine", "-f", ".env")

    def test_a_tampered_file_installs_nothing(self):
        with open(self.store / "tools" / "prune" / "prune.py", "a") as f:
            f.write("# changed after review\n")
        p = self.fo("store", "add", "prune", rc=1)
        self.assertIn("nothing was installed", p.stderr)
        self.assertFalse((self.home / ".config" / "farmout" / "tools" / "prune").exists())

    def test_update_reinstalls_new_versions(self):
        self.fo("store", "add", "test-gaps")
        index = json.loads((self.store / "index.json").read_text())
        for e in index["entries"]:
            if e["name"] == "test-gaps":
                e["version"] = "1.1.0"
        (self.store / "index.json").write_text(json.dumps(index))
        self.assertIn("test-gaps 1.0.0 -> 1.1.0", self.fo("store", "update").stdout)
        self.assertIn("up to date", self.fo("store", "update").stdout)


class MoreTest(CliBase):
    """The deferred table items: export, several patches, undo, status, dashboard, debate, bench, fix-pr."""

    def test_export_markdown_and_html(self):
        self.fo("run", "codex", "say <b>hi</b>", "-n", "x")
        md = self.fo("export", "x").stdout
        self.assertTrue(md.startswith("# farmout session x (codex)"))
        self.assertIn("### Reply\n\n<b>hi</b>", md)
        self.fo("export", "x", "--html", "-o", str(self.tmp / "x.html"))
        html = (self.tmp / "x.html").read_text()
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", html)
        self.assertNotIn("<b>hi</b>", html)

    def test_apply_several_in_order_then_undo(self):
        self.repo()
        self.fo("run", "codex", "write one.txt 1", "--isolate", "-n", "p1")
        self.fo("run", "claude", "write one.txt clash", "--isolate", "-n", "p2")
        self.fo("run", "copilot", "write three.txt 3", "--isolate", "-n", "p3")
        p = self.fo("apply", "p1", "p2", "p3", rc=1)
        self.assertIn("p2:", p.stderr)
        self.assertIn("already applied: p1", p.stderr)
        self.assertEqual((self.work / "one.txt").read_text(), "1")
        self.assertFalse((self.work / "three.txt").exists(), "the queue stops at the first patch that doesn't apply")
        self.assertIn("took p1's patch back out", self.fo("undo").stdout, "a bare undo takes back the last apply")
        self.assertFalse((self.work / "one.txt").exists())
        self.assertIn("no applied patch", self.fo("undo", "p1", rc=1).stderr)
        self.assertIn("nothing to undo", self.fo("undo", rc=1).stderr)

    def test_fan_out_commands_refuse_when_no_worker_is_ready(self):
        self.repo()
        (self.work / "a.py").write_text("x = 3\n")
        bare = self.tmp / "only-git"
        bare.mkdir()
        (bare / "git").symlink_to(shutil.which("git"))
        for args in (["review"], ["ask", "hi"], ["race", "do it"], ["bench"]):
            with self.subTest(args[0]):
                p = self.fo(*args, rc=1, PATH=str(bare), FARMOUT_CLAUDE_DIR="")
                self.assertIn("no workers are ready here", p.stderr)
                self.assertNotIn("No findings", p.stdout, "an empty review must not look like a clean change")

    def test_an_isolated_session_isnt_told_to_delete_its_own_worktree(self):
        self.repo()
        out = self.fo("run", "codex", "write x.txt 1", "--isolate", "-n", "iso").stdout
        self.assertIn("farmout rm iso removes it with the session", out)
        self.assertNotIn("worktree remove", out, "the session's next turn still works in that worktree")

    def test_a_turn_left_by_a_killed_runner_is_never_overwritten(self):
        self.fo("run", "codex", "say first", "-n", "k")
        turns = self.state / "sessions" / "k" / "turns"
        crashed = {"n": 2, "id": "lost", "prompt": "say lost", "files": [], "worker": "codex", "started": 1, "status": "running"}
        (turns / "0002.json").write_text(json.dumps(crashed))  # written, then the runner died before meta said 2 turns
        self.fo("send", "k", "say third")
        self.assertEqual(json.loads((turns / "0002.json").read_text())["id"], "lost")
        self.assertEqual(json.loads((turns / "0003.json").read_text())["prompt"], "say third")

    def test_bare_undo_never_reaches_into_another_repository(self):
        self.repo()
        self.fo("run", "codex", "write one.txt 1", "--isolate", "-n", "p1")
        self.fo("apply", "p1")
        other = self.tmp / "other"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        self.assertIn("nothing to undo here", self.fo("undo", cwd=other, rc=1).stderr)
        self.assertIn("nothing to undo here", self.fo("undo", cwd=self.tmp, rc=1).stderr, "outside any repository too")
        self.assertEqual((self.work / "one.txt").read_text(), "1", "the patch applied in the other project stays")
        self.assertIn("took p1's patch back out", self.fo("undo", "-C", str(self.work), cwd=other).stdout)

    def test_race_in_a_repository_without_commits_says_why(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        self.git("init", "-q", cwd=bare)
        self.assertIn("no commits yet", self.fo("race", "do it", cwd=bare, rc=1).stderr)
        self.assertEqual(self.json("ls"), [], "no worker was started")

    def test_status_line_and_watch(self):
        self.assertEqual(self.fo("status", "--line").stdout, "farmout: idle\n")
        self.fo("run", "claude", "sleep busy", "-n", "b", "--bg")
        self.pids("busy")
        self.assertEqual(self.fo("status", "--line").stdout, "farmout: 1 running\n")
        self.assertEqual(self.json("status")["running"], ["b"])
        watch = subprocess.Popen([sys.executable, str(SERVER), "ls", "--watch", "--interval", "0.2"], env=self.env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
        time.sleep(1.5)
        watch.send_signal(signal.SIGINT)
        out = watch.communicate(timeout=10)[0]
        self.assertIn("farmout: 1 running", out)
        self.assertGreater(out.count("\033[H\033[2J"), 1, "it redraws")

    def test_debate_continues_each_conversation_with_the_others_answers(self):
        out = self.json("ask", "--debate", "-w", "codex,claude", "say my view")
        self.assertEqual(len(out["rounds"]), 2)
        brief = self.last("claude")["brief"]
        self.assertTrue(brief.startswith("The other models answered"))
        other = re.search(r"^- (\S+debate-1-codex/turns/0001\.md)$", brief, re.M).group(1)
        self.assertEqual(Path(other).read_text(), "my view")
        self.assertEqual(flag(self.last("claude")["argv"], "--resume"), self.meta("debate-1-claude")["native_id"])

    def test_bench_replays_your_bug_fixes(self):
        self.repo()
        (self.work / "m.py").write_text("def ratio(a, b):\n    return a / b\n")
        self.git("add", "m.py")
        self.git("commit", "-qm", "add ratio")
        (self.work / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        self.git("commit", "-qam", "fix division by zero in ratio")
        self.reviewers_say(codex="m.py:2 | high | divides by zero | ratio(1, 0) | guard\n", claude="NO FINDINGS")
        out = self.json("bench", "-n", "2", "-w", "codex,claude")
        self.assertEqual([c["subject"] for c in out["commits"]], ["fix division by zero in ratio"])
        self.assertEqual(out["scoreboard"][0], {"worker": "codex", "found": 1, "of": 1, "failed": 0})
        self.assertEqual(self.git("worktree", "list").count("\n"), 1, "the scratch worktree is gone")
        self.assertEqual(self.git("status", "--porcelain"), "", "your tree is untouched")
        self.assertEqual(self.json("ls"), [], "nor do its reviews linger in `farmout ls`")
        # --share: a post and an image card with scores only, never the repository's names or code
        p = self.fo("bench", "-n", "2", "-w", "codex,claude", "--share")
        self.assertIn("Which AI reviews my code best? I replayed 1 real bug fix", p.stdout)
        self.assertRegex(p.stdout, r"Codex\s+█ 1/1")
        self.assertIn("https://x.com/intent/tweet?text=", p.stdout)
        card = re.search(r"^Image: (\S+)", p.stdout, re.M).group(1)
        self.assertTrue(card.startswith("~/"), "paths under your home print as ~/...")
        svg = Path(str(self.home) + card[1:]).with_suffix(".svg").read_text()
        self.assertIn("Which AI reviews my code best?", svg)
        for private in ("ratio", "m.py", "division", self.work.name):
            self.assertNotIn(private, p.stdout.split("Report:")[1], private)
            self.assertNotIn(private, svg, private)
        shared = self.json("bench", "-n", "1", "-w", "codex", "--share")["share"]
        self.assertTrue(Path(shared["svg"]).exists())
        self.assertLessEqual(len(shared["text"]), 280, "fits in one post")

    def test_workers_can_name_their_model(self):
        self.repo()
        self.fo("ask", "say hi", "-w", "claude:opus,codex")
        claude = self.last("claude")["argv"]
        self.assertEqual(claude[claude.index("--model") + 1], "opus")
        self.assertNotIn("-m", self.last("codex")["argv"], "codex keeps its own default")
        (self.work / "a.py").write_text("x = 2\n")
        self.fo("review", "-w", "codex:gpt-x")
        codex = self.last("codex")["argv"]
        self.assertEqual(codex[codex.index("-m") + 1], "gpt-x")
        p = self.fo("ask", "say both", "-w", "claude:opus,claude:sonnet")
        self.assertIn("── claude:opus · ok", p.stdout)
        self.assertIn("── claude:sonnet · ok", p.stdout)
        models = sorted(flag(r["argv"], "--model") for r in self.runs() if r["name"] == "claude" and "say both" in r["brief"])
        self.assertEqual(models, ["opus", "sonnet"], "one subscription, two models, side by side")

    def test_bench_compares_models_of_one_worker(self):
        self.repo()
        (self.work / "m.py").write_text("def ratio(a, b):\n    return a / b\n")
        self.git("add", "m.py")
        self.git("commit", "-qm", "add ratio")
        (self.work / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        self.git("commit", "-qam", "fix division by zero in ratio")
        self.reviewers_say(codex="m.py:2 | high | divides by zero | ratio(1, 0) | guard\n", copilot="NO FINDINGS")
        out = self.json("bench", "-n", "1", "-w", "codex,copilot:gpt-x,copilot:other-model")
        board = {b["worker"]: (b["found"], b["of"]) for b in out["scoreboard"]}
        self.assertEqual(board, {"codex": (1, 1), "copilot:gpt-x": (0, 1), "copilot:other-model": (0, 1)})
        models = [r["argv"][r["argv"].index("--model") + 1] for r in self.runs() if r["name"] == "copilot"]
        self.assertEqual(sorted(models), ["gpt-x", "other-model"], "one review per model, each on its own model")
        self.assertEqual(self.json("ls"), [], "both rounds' sessions are cleaned up")
        card = self.json("bench", "-n", "1", "-w", "copilot:gpt-x", "--share")["share"]["text"]
        self.assertIn("Copilot · gpt-x", card)
        self.assertIn("unavailable worker(s): nope", self.fo("bench", "-w", "nope:m", rc=1).stderr)

    def test_bench_ranks_your_main_claude_login_when_there_is_no_second_account(self):
        self.repo()
        (self.work / "m.py").write_text("def ratio(a, b):\n    return a / b\n")
        self.git("add", "m.py")
        self.git("commit", "-qm", "add ratio")
        (self.work / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        self.git("commit", "-qam", "fix division by zero in ratio")
        self.reviewers_say(claude="m.py:2 | high | divides by zero | ratio(1, 0) | guard\n")
        p = self.fo("bench", "-n", "1", "-w", "claude", FARMOUT_CLAUDE_DIR="")
        self.assertIn("your main Claude login takes part", p.stderr)
        self.assertRegex(p.stdout, r"claude\s+1/1")
        self.assertEqual(self.last("claude")["env"], {"FARMOUT_WORKER": "1"}, "the default login: no CLAUDE_CONFIG_DIR")
        inside = self.fo("bench", "-n", "1", "-w", "claude", rc=1, FARMOUT_CLAUDE_DIR="", CLAUDECODE="1")
        self.assertIn("unavailable worker(s): claude", inside.stderr, "inside Claude Code it's the head's own account")
        self.assertIn("unavailable worker(s): claude", self.fo("run", "claude", "x", rc=1, FARMOUT_CLAUDE_DIR="").stderr,
                      "other commands use it only when asked")
        self.fo("run", "claude", "say hi", "-n", "m", FARMOUT_CLAUDE_DIR="", FARMOUT_CLAUDE_MAIN="1")
        self.assertEqual(self.last("claude")["env"], {"FARMOUT_WORKER": "1"})
        mine = str(self.tmp / "my-claude")  # a main login kept outside ~/.claude
        self.fo("run", "claude", "say hi", "-n", "m2", FARMOUT_CLAUDE_DIR="", FARMOUT_CLAUDE_MAIN="1", CLAUDE_CONFIG_DIR=mine)
        self.assertEqual(self.last("claude")["env"], {"CLAUDE_CONFIG_DIR": mine, "FARMOUT_WORKER": "1"},
                         "the login `claude` uses in your shell")

    def test_fix_pr_turns_feedback_into_a_race(self):
        self.repo()
        head = self.git("rev-parse", "HEAD").strip()
        gh = self.bindir / "gh"
        gh.write_text(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
user = lambda login, kind="User": {{"login": login, "type": kind}}
if a[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 7, "title": "Speed up", "url": "u", "headRefOid": "{head}", "author": {{"login": "me"}}}}))
elif a[0] == "api" and a[-1].endswith("/reviews"):
    print(json.dumps([{{"body": "Handle the empty list", "state": "CHANGES_REQUESTED", "user": user("rev")}},
                      {{"body": "LGTM, ship it", "state": "APPROVED", "user": user("rev2")}}]))
elif a[0] == "api" and "/issues/" in a[-1]:  # two pages, as `gh api --paginate` prints them
    print(json.dumps([{{"body": "## Bundle size check", "user": user("github-actions[bot]", "Bot")}}]))
    print(json.dumps([{{"body": "Will do", "user": user("me")}}, {{"body": "Also check None", "user": user("rev")}}]))
elif a[0] == "api":
    print(json.dumps([{{"path": "a.py", "line": 2, "body": "y is wrong", "user": user("rev")}}]))
elif a[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "tests", "bucket": "fail", "link": "http://ci/1"}}, {{"name": "lint", "bucket": "pass"}}]))
""")
        gh.chmod(0o755)
        runs = len(self.runs())
        dry = self.fo("fix-pr", "7", "--dry-run", PATH=f"{self.bindir}:/usr/bin:/bin", FARMOUT_CLAUDE_DIR="").stdout
        self.assertIn("inline comment at a.py:2: y is wrong", dry)
        self.assertEqual(len(self.runs()), runs, "a dry run starts no worker")
        out = self.json("fix-pr", "7", "-w", "codex", rc=1)
        self.assertEqual(out["winner"], None, "the fake worker changes nothing")
        brief = self.last("codex")["brief"]
        for want in ("pull request #7 (Speed up)", "review comment: Handle the empty list", "review comment: Also check None",
                     "inline comment at a.py:2: y is wrong", "failing check: tests (http://ci/1)"):
            self.assertIn(want, brief)
        for noise in ("lint", "LGTM", "Bundle size", "Will do"):  # a passing check, an approval, a bot, the PR's author
            self.assertNotIn(noise, brief)
        self.git("commit", "--allow-empty", "-qm", "moved on")
        self.assertIn("gh pr checkout 7", self.fo("fix-pr", "7", rc=1).stderr)


class SecondReviewTest(CliBase):
    """Bugs found when four models reviewed the skills, hooks and store code."""

    def make_skill_repo(self, files):
        src = self.tmp / "remote"
        for rel, text in files.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(text)
        return src

    def commit_all(self, src):
        self.git("init", "-q", cwd=src)
        self.git("add", ".", cwd=src)
        self.git("commit", "-qm", "s", cwd=src)

    def test_remote_skills_cant_smuggle_links_or_commands(self):
        secret = self.tmp / "id_rsa"
        secret.write_text("PRIVATE")
        src = self.make_skill_repo({"SKILL.md": "---\nname: linky\ndescription: d\n---\nsay hi\n"})
        (src / "ref").symlink_to(secret)
        self.commit_all(src)
        self.assertIn("symbolic links (ref)", self.fo("skill", "add", f"file://{src}", rc=1).stderr)
        (src / "ref").unlink()
        (src / "SKILL.md").write_text("---\nname: racer\ndescription: d\nmode: race\nworkers: [codex]\n"
                                      f"test: touch {self.tmp}/pwned\n---\nwrite f.txt x\n")
        self.git("add", "-A", cwd=src)
        self.git("commit", "-qm", "t", cwd=src)
        self.fo("skill", "add", f"file://{src}")
        self.repo()
        self.fo("racer", rc=None)
        self.assertFalse((self.tmp / "pwned").exists(), "a fetched skill's test command never runs")
        self.fo("racer", "--test", "true")
        self.assertFalse((self.tmp / "pwned").exists())

    def test_a_projects_skill_cant_write_or_run_commands_either(self):
        self.repo()
        d = self.work / ".farmout" / "skills" / "proj"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: proj\ndescription: d\nworkers: [codex]\nwrite: true\n---\nwrite x.txt hi\n")
        self.fo("proj")
        self.assertEqual(flag(self.last("codex")["argv"], "-s"), "read-only", "write: true in a project's skill is ignored")
        self.fo("proj", "--write")
        self.assertEqual(flag(self.last("codex")["argv"], "-s"), "workspace-write", "you can still say --write yourself")

    def test_store_index_paths_stay_inside(self):
        store = self.tmp / "evil"
        store.mkdir()
        target = self.tmp / "outside.txt"
        for path in (str(target), "../outside.txt"):
            with self.subTest(path):
                index = {"version": 1, "entries": [{"name": "evil", "kind": "skill", "files": [
                    {"path": path, "sha256": "0" * 64}]}]}
                (store / "index.json").write_text(json.dumps(index))
                p = self.fo("store", "add", "evil", rc=1, FARMOUT_STORE=str(store / "index.json"))
                self.assertIn("relative path", p.stderr)
                self.assertFalse(target.exists())

    def test_store_never_replaces_your_own_files(self):
        mine = self.home / ".config" / "farmout" / "skills" / "test-gaps"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("---\nname: test-gaps\ndescription: mine\n---\nmine\n")
        env = {"FARMOUT_STORE": str(SERVER.parent / "store" / "index.json")}
        self.assertIn("is yours, not from the store", self.fo("store", "add", "test-gaps", rc=1, **env).stderr)
        self.assertIn("mine", (mine / "SKILL.md").read_text())
        self.fo("store", "add", "test-gaps", "--force", **env)

    def test_hook_settings_are_checked_and_guard_fails_closed(self):
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"hooks": [{"event": "TaskEnd", "command": "x", "matcher": "(["}]}')
        self.assertIn("matcher isn't a valid regex", self.fo("run", "codex", "say x", rc=1).stdout)
        (cfg / "settings.json").unlink()
        guard = SERVER.parent / "store" / "hooks" / "guard-secrets" / "guard.py"
        folder = self.tmp / "conf"
        (folder / "deep").mkdir(parents=True)
        (folder / "deep" / ".env").write_text("X=1")
        for stdin, why in ((json.dumps({"files": [str(folder)]}), "look like secrets"), ("not json", "blocking it to be safe")):
            out = subprocess.run([sys.executable, str(guard)], input=stdin, capture_output=True, text=True)
            self.assertEqual(out.returncode, 2)
            self.assertIn(why, out.stderr)

    def test_fix_pr_tolerates_gh_exit_codes_for_failing_checks(self):
        self.repo()
        head = self.git("rev-parse", "HEAD").strip()
        gh = self.bindir / "gh"
        gh.write_text(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
if a[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 3, "title": "t", "headRefOid": "{head}", "reviews": [], "comments": []}}))
elif a[0] == "api":
    assert "--paginate" in a
    print("[]")
elif a[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "ci", "bucket": "fail"}}]))
    sys.exit(1)  # what gh does when a check failed
""")
        gh.chmod(0o755)
        self.fo("fix-pr", "3", "-w", "codex", rc=1)
        self.assertIn("failing check: ci", self.last("codex")["brief"])

    def test_undo_after_a_three_way_apply_unstages_too(self):
        self.repo()
        (self.work / "f.txt").write_text("".join(f"line {i}\n" for i in range(1, 11)))
        self.git("add", "f.txt")
        self.git("commit", "-qm", "f")
        self.fo("run", "codex", "write g.txt x", "--isolate", "-n", "w")
        wt = Path(self.meta("w")["iso"]["worktree"])
        (wt / "f.txt").write_text((wt / "f.txt").read_text().replace("line 1\n", "line ONE\n"))
        (self.work / "f.txt").write_text((self.work / "f.txt").read_text().replace("line 3\n", "line THREE\n"))
        self.git("add", "f.txt")  # git's --3way works on what the index holds
        self.assertIn("3-way", self.fo("apply", "w").stdout)
        self.fo("undo", "w")
        self.assertNotIn("line ONE", self.git("diff", "--cached"))
        self.assertNotIn("line ONE", (self.work / "f.txt").read_text())

    def test_foreach_with_your_skill_and_bench_skips_added_files(self):
        d = self.work / ".farmout" / "skills" / "mk"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: mk\ndescription: d\nworkers: [codex]\n---\nsay made $ARGUMENTS\n")
        pdir = self.work / ".farmout" / "pipelines"
        pdir.mkdir(parents=True)
        (pdir / "f.json").write_text(json.dumps({"stage": [{"name": "t", "worker": "claude", "prompt": "lines a|b"},
                                                           {"name": "b", "foreach": "t", "skill": "mk", "prompt": "for {item}"}]}))
        state = self.json("pipe", "run", "f")
        out = Path(state["stages"][1]["output"]).read_text()
        self.assertIn("made for a", out)
        self.assertIn("made for b", out)
        self.repo()
        (self.work / "new.py").write_text("x = 1\n")
        self.git("add", "new.py")
        self.git("commit", "-qm", "fix: add the missing module")
        self.assertIn("no small bug-fix commits", self.fo("bench", "-n", "1", rc=1).stderr)


class HistoryTest(CliBase):
    """Conversations outlive a renamed or moved folder, branch off any turn, and show as a tree."""

    def test_a_session_follows_its_renamed_folder(self):
        self.repo()
        (self.work / "sub").mkdir()
        self.fo("run", "codex", "say one", "-n", "c", cwd=self.work / "sub")
        self.fo("run", "claude", "say uno", "-n", "k", cwd=self.work / "sub")
        moved = self.tmp / "renamed"
        os.rename(self.work, moved)
        p = self.fo("send", "c", "recall", cwd=self.tmp, rc=1)
        self.assertIn("is gone (renamed or moved?)", p.stderr, "from outside the repository it can't be found")
        self.assertIn("(folder gone)", self.fo("ls", cwd=self.tmp).stdout)
        self.assertEqual(self.runs()[-1]["name"], "claude", "nothing ran")

        p = self.fo("send", "c", "recall", cwd=moved)
        self.assertEqual(p.stdout, "say one\n", "codex resumes its own conversation, by id")
        self.assertIn(f"[c · its folder is now {moved / 'sub'}]", p.stderr)
        self.assertEqual((self.meta("c")["cwd"], self.last("codex")["cwd"]), (str(moved / "sub"),) * 2,
                         "the same folder inside the repository, found from the repository's top")
        self.assertIn(f"folder moved from {self.work / 'sub'} to {moved / 'sub'}", self.last("codex")["brief"])
        self.fo("send", "c", "say again", cwd=moved)
        self.assertNotIn("folder moved", self.last("codex")["brief"], "said once")

        p = self.fo("send", "k", "say dos", cwd=moved)  # this claude can't find it from the new folder: a file instead
        self.assertEqual(p.stdout, "dos\n")
        self.assertIn("claude couldn't find its own conversation", p.stderr)
        run = self.last("claude")
        self.assertNotIn("--resume", run["argv"])
        self.assertIn("continues an earlier conversation (with claude)", run["brief"])
        ctx = Path(re.search(r"^- (.+context-0002\.md)$", run["brief"], re.M).group(1)).read_text()
        self.assertIn("say uno", ctx)
        self.assertEqual(self.fo("send", "k", "recall", cwd=moved).stdout, "say dos\n",
                         "then claude resumes natively again, from the new folder")
        self.assertEqual(flag(self.last("claude")["argv"], "--resume"), self.meta("k")["native_id"])


    def test_an_empty_login_folder_reads_as_logged_out_with_other_notes_too(self):
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps({"redact": True}))
        self.fo("run", "claude", "say uno", "-n", "k")
        empty = self.tmp / "empty-login"
        empty.mkdir()
        p = self.fo("send", "k", "say tres password=hunter2hunter2", FARMOUT_CLAUDE_DIR=str(empty),
                    FAKE_EMPTY_LOGIN=str(empty))
        self.assertIn("redacted 1 secret", p.stderr)
        self.assertNotIn("couldn't find its own conversation", p.stderr)

    def test_an_empty_login_folder_reads_as_logged_out(self):
        self.fo("run", "claude", "say uno", "-n", "k")
        empty = self.tmp / "empty-login"
        empty.mkdir()
        p = self.fo("send", "k", "say dos", FARMOUT_CLAUDE_DIR=str(empty), FAKE_EMPTY_LOGIN=str(empty))
        self.assertEqual(p.stdout, "dos\n")
        self.assertRegex(p.stderr, r"turn 2 · \w+ · ok .*claude isn't logged in.*; retried on \w+")
        self.assertNotIn("couldn't find its own conversation", p.stderr, "it found none because it isn't logged in")
        run = self.runs()[-1]
        self.assertNotEqual(run["name"], "claude")
        ctx = Path(re.search(r"^- (.+context-0002\.md)$", run["brief"], re.M).group(1)).read_text()
        self.assertIn("say uno", ctx, "the next worker got the conversation")
    def test_a_branch_off_an_earlier_session_follows_the_moved_folder_too(self):
        self.repo()
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("fork", "t", "say two", "-n", "b")
        moved = self.tmp / "moved"
        os.rename(self.work, moved)
        self.fo("fork", "b", "--at", "1", "say again", "-n", "c", cwd=moved)  # turn 1 is t's, from before b began
        self.assertEqual([self.meta(n)["cwd"] for n in ("t", "b", "c")], [str(moved)] * 3)
        self.assertEqual(self.meta("c")["parent"], "t")

    def test_a_renamed_subfolder_is_never_swapped_for_another_folder(self):
        self.repo()
        (self.work / "sub").mkdir()
        self.fo("run", "codex", "say one", "-n", "s", "--write", cwd=self.work / "sub")
        os.rename(self.work / "sub", self.work / "sub2")
        p = self.fo("send", "s", "say two", rc=1)  # from the repository's top: sub is gone from it too
        self.assertIn("has no sub either", p.stderr)
        self.fo("send", "s", "--here", "say two", cwd=self.work / "sub2")
        self.assertEqual(self.meta("s")["cwd"], str(self.work / "sub2"))

    def test_a_branch_whose_first_turn_failed_still_gets_its_history(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("fork", "t", "-w", "copilot", "fail boom", "-n", "b", rc=1)
        self.fo("send", "b", "say retry")
        brief = self.last("copilot")["brief"]
        self.assertIn("continues an earlier conversation (with copilot)", brief)
        self.assertIn("say one", Path(re.search(r"^- (.+context-0002\.md)$", brief, re.M).group(1)).read_text())

    def test_no_branch_is_made_off_a_folder_that_is_gone(self):
        self.repo()
        self.fo("run", "codex", "write b.py x", "--isolate", "-n", "w")
        shutil.rmtree(self.meta("w")["iso"]["worktree"])
        self.assertIn("its worktree", self.fo("fork", "w", "say hi", rc=1).stderr)
        self.assertEqual([r["name"] for r in self.json("ls")], ["w"], "no empty branch left behind")

    def test_send_here_moves_a_session_to_any_folder(self):
        self.fo("run", "codex", "say one", "-n", "plain")  # not a git repository: no way to tell it's the same folder
        os.rename(self.work, self.tmp / "elsewhere")
        self.assertIn("--here", self.fo("send", "plain", "recall", cwd=self.tmp / "elsewhere", rc=1).stderr)
        p = self.fo("send", "plain", "--here", "recall", cwd=self.tmp / "elsewhere")
        self.assertEqual(p.stdout, "say one\n")
        self.assertEqual(self.meta("plain")["cwd"], str(self.tmp / "elsewhere"))

    def test_an_isolated_session_keeps_its_worktree_when_the_repository_moves(self):
        self.repo()
        self.fo("run", "codex", "write b.py one", "--isolate", "-n", "w")
        moved = self.tmp / "moved"
        os.rename(self.work, moved)
        self.fo("send", "w", "write c.py two", cwd=moved)
        iso = self.meta("w")["iso"]
        self.assertTrue(iso["worktree"].startswith(str(moved) + os.sep), iso)
        patch = self.fo("diff", "w", cwd=moved).stdout
        self.assertIn("+++ b/b.py", patch)
        self.assertIn("+++ b/c.py", patch)
        self.assertIn(iso["worktree"], self.git("worktree", "list", cwd=moved), "git's links were repaired")

    def test_tree_here_shows_this_projects_conversations(self):
        self.repo()
        away = self.tmp / "elsewhere"
        away.mkdir()
        self.fo("run", "codex", "say away", "-n", "away", cwd=away)
        self.fo("run", "codex", "say home", "-n", "home", cwd=self.work)
        self.assertIn("away", self.fo("tree").stdout)
        here = self.fo("tree", "--here").stdout
        self.assertIn("home", here)
        self.assertNotIn("away", here)
        self.assertEqual([r["name"] for r in self.json("ls", "--here")], ["home"], "ls --here too")

    def test_tree_here_counts_a_linked_worktree_of_the_repository(self):
        self.repo()
        linked = self.tmp / "feature"
        self.git("worktree", "add", "-q", str(linked))
        self.fo("run", "codex", "say linked", "-n", "lw", cwd=linked)
        self.assertIn("lw", self.fo("tree", "--here", cwd=self.work).stdout, "the same project, checked out elsewhere")

    def test_branch_off_any_turn_and_see_the_tree(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("send", "t", "say two")
        self.fo("send", "t", "say three")
        p = self.fo("fork", "t", "--at", "1", "-w", "copilot", "say other", "-n", "b")
        self.assertIn("[b · branched from t after turn 1 · copilot]", p.stderr)
        ctx = (self.state / "sessions" / "b" / "context.md").read_text()
        self.assertIn("say one", ctx)
        self.assertNotIn("say two", ctx, "only the turns up to the branch point")
        self.assertIn("Read it first", self.last("copilot")["brief"])
        self.assertEqual(self.json("log", "b")[0]["prompt"], "say other", "the pointer to it isn't part of the question")
        self.fo("send", "b", "say more")  # turn 3 of b's conversation
        self.fo("fork", "b", "--at", "1", "say sibling", "-n", "c")
        self.assertEqual(self.meta("c")["parent"], "t", "turn 1 is t's: the new branch starts from t")
        self.fo("fork", "b", "--at", "3", "say deeper", "-n", "d")
        self.assertEqual([self.meta("d")[k] for k in ("parent", "fork_at", "base")], ["b", 2, 3])
        export = self.fo("export", "d").stdout
        self.assertEqual(re.findall(r"^## Turn (\d+) \((\w+)", export, re.M),
                         [("1", "codex"), ("2", "copilot"), ("3", "copilot"), ("4", "copilot")])
        self.assertNotIn("say two", export)
        self.assertIn("no finished turn 9 to branch from; its turns are 1 to 3", self.fo("fork", "t", "--at", "9", "x", rc=1).stderr)

        lines = self.fo("tree", "d").stdout.splitlines()
        self.assertRegex(lines[0], r"^t · codex · \d+s ago$", "a conversation in this folder doesn't name it")
        self.assertEqual(lines[1:], ["● 1  say one",
                                     "├─╮ ⑂ b · copilot",
                                     "│ ● 2  say other",
                                     "│ ● 3  say more",
                                     "│ ╰─╮ ⑂ d · copilot  ◀",
                                     "│   ● 4  say deeper",
                                     "├─╮ ⑂ c · codex",
                                     "│ ● 2  say sibling",
                                     "● 2  say two",
                                     "● 3  say three"])
        self.assertIn(f"t · codex · {self.work} · ", self.fo("tree", "d", cwd=self.tmp).stdout, "elsewhere it does")
        [root] = self.json("tree")
        self.assertEqual([(b["name"], b["fork_at"]) for b in root["branches"]], [("b", 1), ("c", 1)])
        self.assertEqual([t["num"] for t in root["branches"][0]["branches"][0]["turns"]], [4])

    def test_claude_branches_natively_only_off_the_end(self):
        self.fo("run", "claude", "say one", "-n", "k")
        self.fo("send", "k", "say two")
        self.assertEqual(self.fo("fork", "k", "--at", "1", "recall", "-n", "k1").stdout, "none\n")
        run = self.last("claude")
        self.assertNotIn("--fork-session", run["argv"])
        ctx = Path(re.search(r"^- (\S+context\.md)$", run["brief"], re.M).group(1)).read_text()
        self.assertIn("say one", ctx)
        self.assertNotIn("say two", ctx)
        self.assertEqual(self.fo("fork", "k", "recall", "-n", "k2").stdout, "say two\n")
        self.assertIn("--fork-session", self.last("claude")["argv"])


class UiTest(CliBase):
    """`farmout ui --web`: a local page that needs its key, and continues or branches conversations."""

    def start_ui(self):
        p = subprocess.Popen([sys.executable, str(SERVER), "ui", "--no-open"], cwd=self.work, env=self.env, text=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.communicate(timeout=10)))
        m = re.search(r"http://127\.0\.0\.1:(\d+)/\?key=(\S+)", p.stdout.readline())
        self.assertTrue(m, p.stderr.read() if p.poll() is not None else "no address printed")
        return int(m.group(1)), m.group(2)

    def call(self, port, path, key=None, body=None, host=None, origin=None):
        headers = {"Host": host or f"127.0.0.1:{port}", **({"X-Farmout-Key": key} if key else {}),
                   **({"Origin": origin} if origin else {}), **({"Content-Type": "application/json"} if body else {})}
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None,
                  headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, r.getheader("Content-Security-Policy"), raw

    def test_a_pasted_image_goes_with_the_message_and_nothing_else_can(self):
        import base64
        port, key = self.start_ui()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
        status, _, raw = self.call(port, "/api/paste", key, {"type": "image/png", "data": base64.b64encode(png).decode()})
        self.assertEqual(status, 200, raw)
        path = json.loads(raw)["path"]
        self.assertEqual(Path(path).read_bytes(), png)
        self.assertTrue(path.startswith(str(self.home / ".cache" / "farmout" / "pastes")))
        self.assertEqual(self.call(port, "/api/paste", key, {"type": "text/html", "data": "eA=="})[0], 400)
        self.assertEqual(self.call(port, "/api/paste", None, {"type": "image/png", "data": "eA=="})[0], 403)
        secret = self.tmp / "secret.txt"
        secret.write_text("not for workers")
        link = Path(path).parent / "link.png"
        link.symlink_to(secret)  # a link in the pastes folder to a file outside it
        status, _, raw = self.call(port, "/api/send", key, {"mode": "new", "prompt": "say hi", "worker": "codex",
                                                              "files": [path, str(secret), str(link),
                                                                        ".cache/farmout/pastes/" + Path(path).name]})
        self.assertEqual(status, 200, raw)
        wait_until(lambda: self.runs(), 20)
        brief = self.last("codex")["brief"]
        self.assertIn(path, brief)
        self.assertNotIn("secret.txt", brief, "only pasted images ride along; any other path is dropped")
        self.assertNotIn("link.png", brief, "a link out of the pastes folder too")
        self.assertEqual(brief.count(".cache/farmout/pastes/"), 1, "a relative path isn't taken")
        argv = self.last("codex")["argv"]
        self.assertEqual(argv[argv.index("-i") + 1], path)

    def test_the_page_needs_its_key_and_continues_or_branches(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("send", "t", "say two")
        port, key = self.start_ui()
        self.assertEqual(self.call(port, "/")[0], 403)
        status, csp, page = self.call(port, f"/?key={key}")
        self.assertEqual(status, 200)
        nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
        self.assertIn(f'<script nonce="{nonce}">', page.decode())
        self.assertEqual(self.call(port, "/api/state")[0], 403, "no key")
        self.assertEqual(self.call(port, "/api/state", key="nope")[0], 403, "a wrong key")
        self.assertEqual(self.call(port, "/api/state", key=key, host="evil.example")[0], 403, "DNS rebinding")
        self.assertEqual(self.call(port, "/?key=%C3%A9")[0], 403, "an odd key is refused, not a crash")
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/send", body=b"{}", headers={"X-Farmout-Key": key, "Content-Length": "-1"})
        self.assertEqual(c.getresponse().status, 400, "a negative length isn't read until the client hangs up")
        c.close()
        send = {"mode": "new", "worker": "codex", "prompt": "say x"}
        self.assertEqual(self.call(port, "/api/send", key=key, body=send, origin="https://evil.example")[0], 403)

        until = time.time() + 900
        (self.state / "limits.json").write_text(json.dumps({"claude": {"until": until, "why": "login"}}))
        state = json.loads(self.call(port, "/api/state", key=key)[2])
        self.assertEqual(state["held"], {"claude": {"until": until, "why": "login"}}, "the page names who's out, and till when")
        [t] = [s for s in state["sessions"] if s["name"] == "t"]
        self.assertEqual([h["prompt"] for h in t["history"]], ["say one", "say two"])
        self.assertEqual(json.loads(self.call(port, "/api/turn?session=t&n=1", key=key)[2])["text"], "one")

        status, _, raw = self.call(port, "/api/send", key=key, body={"mode": "continue", "session": "t", "n": 1, "prompt": "say x"})
        self.assertEqual((status, json.loads(raw)["error"]), (400, "turn 1 isn't the newest in t: branch from it instead"))
        r = json.loads(self.call(port, "/api/send", key=key, body={"mode": "continue", "session": "t", "n": 2, "prompt": "say three"})[2])
        self.assertEqual(r["session"], "t")
        self.assertTrue(wait_until(lambda: [x["status"] for x in self.json("log", "t")] == ["ok"] * 3, 30))

        edit = {"mode": "branch", "edit": True, "session": "t", "n": 2, "prompt": "say deux", "worker": "copilot"}
        b = json.loads(self.call(port, "/api/send", key=key, body=edit)[2])["session"]
        self.assertEqual([self.meta(b)[k] for k in ("parent", "base", "worker")], ["t", 1, "copilot"],
                         "asking question 2 differently keeps turn 1")
        self.assertTrue(wait_until(lambda: [x["text"] for x in self.json("log", b)] == ["deux"], 30))
        new = json.loads(self.call(port, "/api/send", key=key, body=send)[2])["session"]
        self.assertTrue(wait_until(lambda: [x["text"] for x in self.json("log", new)] == ["x"], 30))
        self.assertEqual(self.meta(new)["cwd"], str(self.work), "a new conversation starts where farmout ui runs")


DRIVER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from farmout import tui
steps, w, h = json.loads(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
app, screens = tui.App(sys.argv[5]), []
for s in steps:
    if s == ["screen"]:
        screens.append(app.frame(w, h).text())
    elif s == ["external"]:  # what the terminal would do next: open the pager or editor, or copy
        screens.append(json.dumps(app.external))
    elif s[0] == "write":  # a file changes meanwhile, as another program would change it
        open(s[1], "w").write(s[2])
    elif s == ["idle"]:  # until the message sent has its turn, and nothing runs
        deadline = time.time() + 60
        app.load()
        while app.busy() or app.wanted:
            assert time.time() < deadline, "still running"
            time.sleep(0.1)
            app.load()
    elif s[0] == "running":
        deadline = time.time() + 30
        while not any(t["status"] == "running" for n in app.by.values() for t in n["turns"]):
            assert time.time() < deadline, "never started"
            time.sleep(0.1)
            app.load()
    elif s[0] == "type":
        for c in s[1]:
            app.key(c)
    elif s[0] in ("paste", "mouse"):
        app.key(tuple(s))
    else:
        app.key(s)
print("\f".join(screens))
"""


class TerminalUiTest(CliBase):
    """`farmout ui` in the terminal: every conversation as a graph; read, continue, branch or ask again from any turn."""

    def ui(self, *steps, size=(120, 30)):
        """Drive the screen without a terminal. A step is a key name, ["type", text], ["paste", text],
        ["mouse", x, y, kind], ["idle"] (until what was sent has its turn and nothing runs), ["running"], ["screen"]
        (keep the screen) or ["external"] (keep, as JSON, what the terminal would do next). Returns what was kept."""
        p = subprocess.run([sys.executable, "-c", DRIVER, str(SERVER.parent), json.dumps(steps), *map(str, size),
                            str(self.work)], cwd=self.work, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.rstrip("\n").split("\f")

    @staticmethod
    def graph(screen, width=66):
        """The graph pane's lines (it is the left 66 columns of a 120-column screen), ages and times as N."""
        return [re.sub(r"\d+s\b", "Ns", line[2:width - 2].rstrip()) for line in screen.splitlines()[1:]]

    def conversation(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("send", "t", "say two")
        self.fo("send", "t", "say three")
        self.fo("fork", "t", "--at", "1", "-w", "copilot", "say other", "-n", "b")

    def test_C_carries_on_your_own_claude_code_session(self):
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True)
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"content": "plan the cache layer"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [
                    {"type": "text", "text": "A 5-minute TTL."}]}},
                {"type": "ai-title", "aiTitle": "Cache layer"}]
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        box, after = self.ui("C", ["screen"], "enter", ["idle"], ["screen"])
        self.assertIn("Carry on your Claude Code session s1: Cache layer", box)
        [row] = self.json("ls")
        meta = self.meta(row["name"])
        self.assertEqual((meta["imported"]["id"], meta["write"]), ("s1", False), "read-only, like the ui's others")
        brief = self.runs()[-1]["brief"]
        self.assertTrue(brief.startswith("Carry on from where this conversation stopped."), brief)
        self.assertIn("continues an earlier conversation (in Claude Code)", brief)
        self.assertRegex(after, rf"Sent to {row['name']}|{row['name']} answered turn 2", "sent, and maybe answered already")
        (d / "s1.jsonl").unlink()
        self.assertIn("No Claude Code, Codex or Copilot CLI session in this folder", self.ui("C", ["screen"])[0])

    def test_the_graph_draws_every_branch_off_its_turn(self):
        self.conversation()
        self.fo("run", "claude", "lines a|b", "-n", "older")
        self.fo("send", "b", "say more")
        first, find, narrow, back, shut, helped = self.ui(
            ["screen"], "/", ["type", "TWO"], ["screen"], "enter", "enter", ["screen"], "esc", ["screen"],
            "g", "j", "j", "j", "j", "j", "j", "j", "h", ["screen"], "?", ["screen"], size=(80, 24))
        self.assertEqual(self.graph(first, 80)[:9], ["▾ t · codex · Ns ago", "● 1  say one", "├─╮ ⑂ b · copilot",
                                                     "│ ● 2  say other", "│ ◉ 3  say more", "● 2  say two",
                                                     "● 3  say three", "", "▾ older · claude · Ns ago"],
                         "the newest turn of the newest conversation is selected")
        self.assertIn("● 1  lines a|b", self.graph(first, 80))
        self.assertRegex(find.splitlines()[-1], r"^ / TWO +1 of 1 · ↑↓ next")
        self.assertIn("◉ 2  say two", find, "find jumps to the question")
        self.assertIn("▌You", narrow, "⏎ reads it: on a narrow screen the reader fills it")
        self.assertIn("╭─ t · turn 2", narrow)
        self.assertNotIn("● 1  say one", narrow)
        self.assertIn("◉ 2  say two", back, "Esc goes back to the graph")
        self.assertIn("▸ older · claude · Ns ago · 1 turn", self.graph(shut, 80), "← folds it and says what it holds")
        self.assertIn("╭─ Keys", helped)

        wide, = self.ui(["screen"])
        self.assertIn("╭─ b · turn 3", wide, "the reader sits beside the graph")
        self.assertEqual([re.sub(r"\d+s\b", "Ns", x[66:].strip("│ ")) for x in wide.splitlines()[1:8]],
                         ["copilot · Ns · ok · Ns ago", "⑂ a branch of t, after its turn 1", "", "▌You", "say more",
                          "", "▌copilot"])
        self.assertIn("│ more", "\n".join(x[66:] for x in wide.splitlines()))
        _, clicked = self.ui(["screen"], ["mouse", 5, 2, "press"], ["screen"])
        self.assertIn("╭─ t · turn 1", clicked, "a click picks a turn")

    def test_continue_branch_or_ask_again_from_any_turn(self):
        self.conversation()
        screens = self.ui(
            "g", "j", "c", ["screen"],  # turn 1 isn't the newest
            "b", ["type", "say forked"], "enter", ["idle"], ["screen"],
            "e", ["screen"], "^U", ["type", "say autre"], "tab", ["type", "copilot"], "enter", ["idle"],
            "/", ["type", "three"], "enter", "c", ["paste", "say four\nand more"], ["screen"], "enter", ["idle"],
            ["screen"])
        refused, forked, asking, pasted, went_on = screens
        self.assertIn("Turn 1 isn't the newest in t: b branches from it, or continue from turn 3", refused)
        self.assertEqual(self.graph(forked)[1:6], ["● 1  say one", "├─╮ ⑂ b · copilot", "│ ● 2  say other",
                                                   "├─╮ ⑂ t-1 · codex", "│ ◉ 2  say forked"])
        self.assertEqual([self.meta("t-1")[k] for k in ("parent", "base", "worker")], ["t", 1, "codex"])
        self.assertIn("Ask turn 2 differently: a branch after turn 1", asking)
        self.assertIn("│ say forked", asking, "its question to start from")
        self.assertEqual([self.meta("t-2")[k] for k in ("parent", "base", "worker")], ["t", 1, "copilot"],
                         "asking t-1's first turn again branches off t, where that turn began")
        self.assertEqual(self.json("log", "t-2")[0]["prompt"], "say autre")
        self.assertIn("│ say four", pasted, "a pasted newline doesn't send it")
        self.assertIn("│ and more", pasted)
        self.assertEqual([x["prompt"] for x in self.json("log", "t")][-1], "say four\nand more")
        self.assertIn("◉ 4  say four", went_on)

    def test_a_new_conversation_and_stopping_a_turn(self):
        self.fo("run", "codex", "say one", "-n", "t")
        drafted, new, stopped = self.ui("c", ["type", "keep me"], "^C", "c", ["screen"], "esc",
                                        "n", ["type", "say fresh"], "enter", ["idle"], ["screen"],
                                        "c", ["type", "nap 30"], "enter", ["running"], "x", "y", ["idle"], ["screen"])
        self.assertIn("│ keep me", drafted, "Ctrl+C closed the composer and kept what was written")
        name = re.search(r"▾ (\S+) · codex", new).group(1)
        self.assertNotEqual(name, "t")
        self.assertIn("◉ 1  say fresh", new)
        self.assertEqual(self.meta(name)["cwd"], str(self.work), "a new conversation starts where farmout ui runs")
        self.assertIn("○ 2  nap 30", stopped)
        self.assertEqual([x["status"] for x in self.json("log", name)], ["ok", "stopped"])

    def test_escape_sequences_never_reach_the_terminal(self):
        sneaky = "say \x1b]52;c;cHduZWQ=\x07hi\x1b[2J there\x9b31m"  # sets the clipboard, clears the screen
        self.fo("run", "codex", sneaky, "-n", "t")
        out = self.fo("tree").stdout
        self.assertIn("● 1  say hi there", out)
        self.assertNotRegex(out, "[\x1b\x07\x9b]")
        screen, = self.ui(["screen"])
        self.assertIn("◉ 1  say hi there", screen)
        self.assertIn("│ hi there", screen, "the answer too")
        self.assertNotRegex(screen, "[\x00-\x09\x0b-\x1f\x7f-\x9f]")

    def test_copy_a_turn_read_its_patch_and_delete_it(self):
        self.repo()
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("run", "codex", "write f.txt hello", "-n", "iso", "--isolate")
        patch, copied, ask, gone = self.ui("d", ["external"], "y", ["external"], "D", ["screen"], "y", ["screen"])
        kind, text = json.loads(patch)
        self.assertEqual(kind, "pager")
        self.assertIn("f.txt", text)
        self.assertIn("+hello", text, "the isolated session's patch, in the pager")
        kind, text, what = json.loads(copied)
        self.assertEqual((kind, what), ("copy", f"turn 1's answer ({len(text):,} characters)"))
        self.assertIn("Delete iso with its 1 turn and worktree? This can't be undone.", ask)
        self.assertIn("Deleted iso", gone)
        self.assertEqual([r["name"] for r in self.json("ls")], ["t"])
        self.assertIn("t has no worktree", self.ui("d", ["screen"])[0], "a session that isn't isolated says so")

    def test_deleting_a_conversation_takes_its_branches(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("send", "t", "say two")
        self.fo("fork", "t", "--at", "1", "say other")
        self.fo("fork", "t-1", "say deeper")
        self.fo("run", "codex", "say keep", "-n", "k")
        ask, gone = self.ui("g", "j", "j", "D", ["screen"], "y", ["screen"])  # k first (newest), then t's head
        self.assertIn("Delete t with its 4 turns and the 2 branches off it? This can't be undone.", ask)
        self.assertIn("Deleted t and the 2 branches off it", gone)
        self.assertEqual([r["name"] for r in self.json("ls")], ["k"])

    def test_find_looks_in_answers_too(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("run", "codex", "lines alpha|beta|" + "|".join(f"filler {i}" for i in range(40)) + "|zebra", "-n", "z")
        self.fo("run", "codex", "big 30", "-n", "x")
        self.fo("run", "codex", "say newest", "-n", "n")
        _, zebra, xs = self.ui(["screen"], "/", ["type", "zebr"], ["screen"], "esc", "/", ["type", "xxxxx"], ["screen"])
        self.assertIn("1 of 1", zebra)
        self.assertIn("z · turn 1", zebra)
        self.assertRegex(zebra, r"│ • zebra\s+[│┃]", "the reader opens where the answer says it")
        self.assertIn("1 of 1", xs, "words only the answer has count too")
        self.assertIn("x · turn 1", xs)

    def test_f_shows_this_projects_conversations_only(self):
        self.repo()
        away = self.tmp / "elsewhere"
        away.mkdir()
        self.fo("run", "codex", "say away", "-n", "away", cwd=away)
        self.fo("run", "codex", "say home", "-n", "home")
        every, only, back = self.ui(["screen"], "f", ["screen"], "f", ["screen"])
        self.assertIn("2 conversations", every)
        self.assertIn("1 conversation in this project", only)
        self.assertIn("▾ home", only)
        self.assertNotIn("away", only.split("\n ")[0], "the other folder's conversation is hidden")
        self.assertIn("2 conversations", back)

    def test_a_whole_conversation_opens_in_the_pager(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("send", "t", "say two")
        self.fo("fork", "t", "--at", "1", "say other")
        kind, text = json.loads(self.ui("O", ["external"])[0])  # on the newest turn: the branch's
        self.assertEqual(kind, "pager")
        self.assertTrue(text.startswith("# t-1 (codex)"), text[:80])
        for seen in ("say one", "say other", "other"):
            self.assertIn(seen, text, "the turns before the branch come too")
        self.assertNotIn("say two", text, "but not the ones after it")

    def test_a_deleted_name_that_comes_back_shows_its_own_answers(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("fork", "t", "say other")
        _, gone, back = self.ui(["screen"], "D", "y", ["screen"], "g", "j", "b", ["type", "say fresh"], "enter",
                                ["idle"], ["screen"])
        self.assertIn("Deleted t-1", gone)
        self.assertIn("t-1 · turn 2", back, "the new branch took the free name")
        self.assertIn("│ fresh", back)
        self.assertNotIn("│ other", back, "not the deleted branch's answer, cached under the same name")

    def test_a_new_conversation_starts_on_a_worker_that_can_answer(self):
        self.fo("run", "codex", "say one", "-n", "t")
        same, = self.ui("n", ["screen"])
        self.assertIn(" on codex ", same, "the conversation you're on suggests its worker")
        (self.state / "limits.json").write_text(json.dumps({"codex": {"until": time.time() + 900, "why": "limit"}}))
        out, = self.ui("n", ["screen"])
        self.assertNotIn(" on codex ", out, "not one at its limit")
        self.assertRegex(out, r" on (antigravity|claude|copilot) ")

    def test_new_answers_are_marked_until_you_read_them(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.ui(["screen"])  # the first run counts everything already there as read
        self.fo("send", "t", "say two")
        before, after = self.ui(["screen"], "g", "u", "enter", ["screen"], size=(90, 30))  # narrow: no reader yet
        self.assertIn("1 conversation · 1 new", before)
        self.assertRegex(before, r"2  say two\s+new")
        self.assertNotRegex(before, r"1  say one\s+new")
        self.assertIn("t · turn 2", after, "u went to it, and ⏎ read it")
        again, = self.ui(["screen"], size=(90, 30))
        self.assertNotIn("1 new", again)
        self.assertNotRegex(again, r"say two\s+new")

    def test_u_opens_a_folded_conversation(self):
        for name in "abcd":
            self.fo("run", "codex", f"say {name}1", "-n", name)
        self.ui(["screen"])  # counts all of that as read
        for name in "abcd":  # a's new answer is the oldest, so a starts folded (the three newest start open)
            self.fo("send", name, f"say {name}2")
        marks = self.state / "ui-read.json"
        data = json.loads(marks.read_text())
        data["read"] += [f"{n}@{self.meta(n)['created']}#2" for n in "bcd"]  # read in another farmout ui
        marks.write_text(json.dumps(data))
        screen, = self.ui("u", ["screen"])
        self.assertIn("a · turn 2", screen, "u unfolds a to its unread answer")

    def test_a_mangled_read_marks_file_starts_afresh(self):
        self.fo("run", "codex", "say one", "-n", "t")
        marks = self.state / "ui-read.json"
        for junk in ("[1]", '{"since": 1, "read": [{}]}', "not json"):
            marks.write_text(junk)
            screen, = self.ui(["screen"])
            self.assertIn("1 conversation", screen, junk)

    def test_two_uis_keep_each_others_read_marks(self):
        self.fo("run", "codex", "say one", "-n", "t")
        self.fo("run", "codex", "say other", "-n", "o")
        self.ui(["screen"])
        self.fo("send", "t", "say two")
        marks = self.state / "ui-read.json"
        data = json.loads(marks.read_text())
        theirs, mine = f"t@{self.meta('t')['created']}#1", f"t@{self.meta('t')['created']}#2"  # no ui here showed t's 1st
        self.ui(["write", str(marks), json.dumps({**data, "read": data["read"] + [theirs]})],  # another ui, meanwhile
                "g", "u", "enter", ["screen"], size=(90, 30))
        read = json.loads(marks.read_text())["read"]
        self.assertIn(theirs, read, "the other farmout ui's mark stays")
        self.assertIn(mine, read)

    def test_a_new_conversation_skips_a_model_at_its_limit(self):
        self.fo("run", "claude:opus", "say one", "-n", "t")
        (self.state / "limits.json").write_text(json.dumps({"claude:opus": {"until": time.time() + 900, "why": "limit"}}))
        screen, = self.ui("n", ["screen"])
        self.assertNotIn(" on claude:opus ", screen)
        self.assertRegex(screen, r" on (antigravity|codex|copilot) ", "the next worker that can answer")

    def test_an_answer_that_comes_in_elsewhere_is_announced(self):
        self.fo("run", "codex", "say one", "-n", "t")
        screen, = self.ui("c", ["type", "nap 1"], "enter", ["running"], "g", ["idle"], ["screen"], size=(90, 30))
        self.assertIn("t answered turn 2: u goes there", screen)
        later, = self.ui("c", ["type", "nap 1"], "enter", ["running"], "g", "c", ["idle"], "esc", ["idle"], ["screen"],
                         size=(90, 30))
        self.assertIn("t answered turn 3: u goes there", later, "said once the message box closes")
        sent, = self.ui("c", ["type", "nap 1"], "enter", ["running"], "g", "c", ["idle"], ["type", "say more"], "enter",
                        ["screen"], size=(90, 30))
        self.assertIn("Sent to t", sent, "sending says so, even with an answer noted at that moment")

    def test_s_shows_what_each_subscription_did(self):
        self.fo("run", "codex", "say one", "-n", "t")
        kind, text = json.loads(self.ui("S", ["external"])[0])
        self.assertEqual(kind, "pager")
        self.assertIn("Last 7 days", text)
        self.assertRegex(text, r"codex\s+1\s+1\s+0")

    def test_j_and_k_jump_between_conversations(self):
        for name in "abc":
            self.fo("run", "codex", f"say {name}", "-n", name)
        screens = self.ui("g", "J", ["screen"], "J", ["screen"], "J", ["screen"], "K", ["screen"], "j", "K", ["screen"])
        titles = [re.search(r"╭─ (\w) ", sc.split("\n")[0][60:]).group(1) for sc in screens]
        self.assertEqual(titles, ["b", "a", "a", "b", "c"], "c is the newest, so first; J stops at the last one; "
                         "K from inside b goes to c, not to b's own head")

    def test_an_empty_ui_says_where_to_start(self):
        screen, = self.ui(["screen"])
        self.assertIn("Start here", screen)
        self.assertIn("n · start a conversation", screen)
        self.assertIn("No conversations yet.", screen)

    def test_r_sends_a_failed_turn_again(self):
        self.fo("run", "codex", "fail boom", "-n", "t", rc=1)
        nope, again, done = self.ui("g", "R", ["screen"], "G", "R", ["screen"], "^U", ["type", "say fixed"], "enter",
                                    ["idle"], ["screen"])
        self.assertIn("R sends a failed or stopped newest turn again", nope, "not on the conversation's head row")
        self.assertIn("Send turn 1 of t again (failed)", again)
        self.assertIn("fail boom", again, "its message, ready to send or change")
        self.assertIn("t · turn 2", done)
        self.assertIn("│ fixed", done)

    def test_a_limit_days_away_says_the_day(self):
        self.fo("run", "codex", "say one", "-n", "t")
        later = time.time() + 3 * 86400
        (self.state / "limits.json").write_text(json.dumps({"codex": {"until": later, "why": "limit"}}))
        screen, = self.ui(["screen"])
        self.assertIn(f"codex at its limit till {time.strftime('%a %H:%M', time.localtime(later))}", screen)

    def test_a_running_patch_waits_until_its_turn_is_done(self):
        self.repo()
        self.fo("run", "codex", "nap 20", "--isolate", "-n", "busy", "--bg")
        screen, = self.ui(["running"], "d", ["screen"], "x", "y")
        self.assertIn("busy is still working: its patch is ready when it's done", screen)

    def test_Y_copies_one_command_whole_and_ctrl_p_attaches_the_clipboards_image(self):
        self.fo("run", "codex", "say use `git status` and then `git log -1`", "-n", "t")
        picker, copied = self.ui("Y", ["screen"], "down", "enter", ["external"])
        self.assertIn("Copy a command from turn 1", picker)
        self.assertIn(" 1  git status", picker)
        self.assertIn(" 2  git log -1", picker)
        self.assertEqual(json.loads(copied), ["copy", "git log -1", "the command (1 line)"])
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 4, 4) + b"\x08\x02\x00\x00\x00"
        (self.tmp / "shot.png").write_bytes(png)
        (self.bindir / "wl-paste").write_text("#!/bin/sh\n"
                                              'if [ "$1" = --list-types ]; then echo image/png; exit 0; fi\n'
                                              f"cat {self.tmp / 'shot.png'}\n")
        (self.bindir / "wl-paste").chmod(0o755)
        self.env["WAYLAND_DISPLAY"] = "wayland-0"
        composer, = self.ui("c", "^P", ["screen"], ["type", "say look"], "enter", ["idle"])
        self.assertIn("📎 1 image", composer)
        argv = self.last("codex")["argv"]
        pasted = argv[argv.index("-i") + 1]
        self.assertEqual(Path(pasted).read_bytes(), png, "the image goes to codex attached, not only as a path")

    def test_copy_uses_the_clipboard_tool_or_the_terminal(self):
        from farmout import tui
        bin_ = self.tmp / "clip-bin"
        bin_.mkdir()
        (bin_ / "wl-copy").write_text(f"#!/bin/sh\nexec /bin/cat > {self.tmp / 'clipboard'}\n")
        (bin_ / "wl-copy").chmod(0o755)
        old = os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        os.environ["PATH"] = str(bin_)
        self.assertEqual(tui.copy("a turn's answer"), "wl-copy")
        self.assertEqual((self.tmp / "clipboard").read_text(), "a turn's answer")

    def test_a_logged_out_or_limited_worker_shows(self):
        self.fo("run", "codex", "say one", "-n", "t")
        soon = time.time() + 900
        (self.state / "limits.json").write_text(json.dumps({"claude": {"until": soon, "why": "login"},
                                                             "codex": {"until": soon, "why": "limit"}}))
        at = time.strftime("%H:%M", time.localtime(soon))
        graph_, compose = self.ui(["screen"], "c", ["screen"])
        self.assertIn(f"╰─ claude logged out till {at} ─ codex at its limit till {at} ─", graph_)
        self.assertIn(f"on same (codex) · codex at its limit till {at}", compose, "before you send it there")

    def test_the_reader_shows_bold_code_and_bullets(self):
        self.fo("run", "codex", "say - **Bold** and `code` and enough more words to wrap this item onto a second line",
                "-n", "t")
        screen, = self.ui(["screen"])
        reader = [line[66:] for line in screen.split("\n")]
        at = next(i for i, line in enumerate(reader) if "• Bold and code and" in line)
        self.assertRegex(reader[at + 1], r"^│   \w", "the item's next line hangs under its text")
        self.assertNotRegex(reader[at] + reader[at + 1], r"\*\*|`", "the marks are styles now, not text")

    def test_emoji_and_accents_keep_the_borders_straight(self):
        self.fo("run", "codex", "say \u26a0\ufe0f careful,\u200f cafe\u0301 \u202eturned", "-n", "t")
        self.assertIn("\u25cf 1  say \u26a0\ufe0f careful, cafe\u0301 turned", self.fo("tree").stdout, "no bidi controls")
        screen, = self.ui(["screen"])
        rows = [line for line in screen.split("\n") if line.endswith("│")]
        self.assertGreater(len(rows), 10)
        self.assertEqual({graph.cols(line) for line in rows}, {120}, "a variation selector or an accent takes no column")

    def test_a_turn_recorded_without_times_still_draws(self):
        self.fo("run", "codex", "say one", "-n", "t")
        rec = self.state / "sessions" / "t" / "turns" / "0001.json"
        rec.write_text(json.dumps({k: v for k, v in json.loads(rec.read_text()).items() if k not in ("started", "secs")}))
        screen, = self.ui(["screen"])
        self.assertIn("◉ 1  say one", screen)
        self.assertIn("│ codex · ok", screen)

    def test_it_runs_in_a_terminal(self):
        if not importlib.util.find_spec("_curses"):
            self.skipTest("no curses here")
        import pty
        self.fo("run", "codex", "say one", "-n", "t")
        p = self.fo("ui", rc=2)
        self.assertIn("farmout ui --web", p.stderr, "without a terminal it says what to use")

        def spawn(term):
            env = {**self.env, "TERM": term, "COLUMNS": "120", "LINES": "30"}
            pid, fd = pty.fork()
            if pid == 0:  # the child: become `farmout ui`, and never run the rest of the test
                try:
                    os.chdir(self.work)
                    os.execve(sys.executable, [sys.executable, str(SERVER), "ui"], env)
                finally:
                    os._exit(127)
            self.addCleanup(os.close, fd)
            return pid, fd

        def wait(pid, what):
            deadline = time.monotonic() + 20
            while True:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    return os.waitstatus_to_exitcode(status)
                self.assertLess(time.monotonic(), deadline, what)
                read() or time.sleep(0.1)

        def read():
            nonlocal seen
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    seen += os.read(fd, 65536)
                except OSError:  # the program has exited
                    return False
            return True

        seen = b""
        pid, fd = spawn("dumb")  # a terminal that can't move the cursor: say so, don't scribble
        self.assertEqual(wait(pid, "a dumb terminal didn't end it"), 2)
        read()
        self.assertIn("TERM=xterm-256color farmout ui", seen.decode())
        pid, fd = spawn("xterm-256color")
        seen = b""

        def until(text, since=0):
            deadline = time.monotonic() + 20
            while text not in seen[since:].decode("utf-8", "replace"):
                self.assertLess(time.monotonic(), deadline, seen[-800:])
                read()
        until("say one")
        for keys, expect in ((b"c", "Continue t after turn 1"), (b"\x1b[200~say two\rmore\x1b[201~", "more"),
                             (b"\x1b", "differently")):
            since = len(seen)
            os.write(fd, keys)
            until(expect, since)
        os.write(fd, b"q")
        self.assertEqual(wait(pid, "q didn't quit"), 0, seen[-800:])
        self.assertEqual([x["prompt"] for x in self.json("log", "t")], ["say one"], "nothing was sent")


class McpSessionTest(Base):
    def last_brief(self, cli):
        recs = [json.loads(p.read_text()) for p in sorted(self.log.glob("[0-9]*.json"), key=lambda p: p.stat().st_mtime_ns)]
        return [r for r in recs if r["name"] == cli][-1]["brief"]

    def call(self, s, tool, timeout=30, **args):
        res = s.call("tools/call", {"name": tool, "arguments": args}, timeout)
        self.assertNotIn("error", res, res)
        return res["result"]["isError"], res["result"]["content"][0]["text"]

    def test_send_sessions_and_pipeline_tools(self):
        s = self.start()
        names = [t["name"] for t in s.call("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["delegate", "send", "sessions", "pipeline"])
        err, text = self.call(s, "send", session="m1", worker="codex", message="say hello", cwd=str(self.work))
        self.assertFalse(err)
        self.assertRegex(text, r"^## m1 turn 1 \(codex\) ok [\d.]+s -> \S+0001\.md\nhello$")
        self.assertEqual(self.call(s, "send", session="m1", message="recall")[1].splitlines()[1], "say hello")
        self.assertIn("give `worker`", s.call("tools/call", {"name": "send", "arguments": {"session": "nope", "message": "x"}})["error"]["message"])
        listing = self.call(s, "sessions")[1]
        self.assertIn("m1  codex  ok  turns=2", listing)
        self.assertIn("turn 2 ok", self.call(s, "sessions", name="m1")[1])
        self.assertRegex(self.call(s, "send", session="m1", worker="codex", message="say same")[1],
                         r"^## m1 turn 3 \(codex\)", "naming the session's own worker changes nothing")
        moved = self.call(s, "send", session="m1", worker="claude", model="haiku", message="say moved")[1]
        self.assertRegex(moved, r"^## m1 turn 4 \(claude\) ok")
        self.assertIn("continues an earlier conversation (with codex)", self.last_brief("claude"))

        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True)
        (d / "p.json").write_text(json.dumps({"stage": [{"name": "a", "worker": "claude", "prompt": "say piped"}]}))
        self.assertIn("p: ", self.call(s, "pipeline", action="list", cwd=str(self.work))[1])
        started = self.call(s, "pipeline", action="run", name="p", cwd=str(self.work))[1]
        run_id = re.search(r"run (\S+) in", started).group(1)
        self.assertTrue(wait_until(lambda: ": ok" in self.call(s, "pipeline", action="status", run=run_id)[1], 30))

    def test_send_follows_a_moved_folder(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.work), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
                           check=True, capture_output=True)
        git("init", "-q")
        (self.work / "a.py").write_text("x = 1\n")
        git("add", ".")
        git("commit", "-qm", "init")
        s = self.start()
        self.call(s, "send", session="mv", worker="codex", message="say one", cwd=str(self.work))
        moved = self.tmp / "moved"
        os.rename(self.work, moved)
        err, text = self.call(s, "send", session="mv", message="recall", cwd=str(moved))
        self.assertFalse(err, text)
        self.assertIn(f"(its folder moved; the session follows it to {moved})", text)
        self.assertEqual(text.splitlines()[-1], "say one")

    def test_cancelling_a_send_stops_that_session(self):
        s = self.start()
        rid = s.request("tools/call", {"name": "send", "arguments": {"session": "c1", "worker": "claude",
                                                                      "message": "sleep mcp", "cwd": str(self.work)}})
        pids = self.pids("mcp")
        s.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid}})
        self.assertAllDead(pids, "a cancelled send kept its worker running")
        self.assertIsNone(s.reply(rid, timeout=2))



class SessionGuardTest(unittest.TestCase):
    """Removing a session doesn't interleave with a message being queued for it or a branch being made off it."""

    def setUp(self):
        import tempfile
        from farmout import sessions
        self.tmp = Path(tempfile.mkdtemp(prefix="farmout-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for attr, value in (("ROOT", self.tmp / "sessions"), ("STATE", self.tmp)):
            old = getattr(sessions, attr)
            setattr(sessions, attr, value)
            self.addCleanup(setattr, sessions, attr, old)
        old_event, old_enabled = sessions.event, sessions.ENABLED
        sessions.event = lambda *a, **k: None  # no hooks from the real settings in an in-process test
        sessions.ENABLED = {**sessions.ENABLED, "codex": None}
        self.addCleanup(setattr, sessions, "event", old_event)
        self.addCleanup(setattr, sessions, "ENABLED", old_enabled)
        self.make("s")

    def test_a_message_queued_during_a_removal_waits_then_finds_it_gone(self):
        from farmout import sessions
        result = []

        def post():
            try:
                result.append(sessions.post("s", "say hi"))
            except Exception as e:
                result.append(e)
        with sessions.guarded("s"):  # what a removal holds while it checks and moves the folder away
            t = threading.Thread(target=post)
            t.start()
            time.sleep(0.3)
            self.assertTrue(t.is_alive(), "the message waits for the removal")
            os.replace(self.tmp / "sessions" / "s", self.tmp / "gone")
        t.join(10)
        self.assertIsInstance(result[0], KeyError, result)
        self.assertEqual(list((self.tmp / "gone" / "inbox").iterdir()), [], "nothing was queued into the removed one")

    def test_a_message_waiting_through_a_removal_doesnt_land_in_a_new_session_of_that_name(self):
        from farmout import sessions
        result = []
        t = threading.Thread(target=lambda: result.append(self.attempt(sessions.post, "s", "say hi")))
        with sessions.guarded("s"):
            t.start()
            time.sleep(0.3)
            os.replace(self.tmp / "sessions" / "s", self.tmp / "gone")
            self.make("s")  # a new session under the same name, before the waiting message wakes
        t.join(10)
        self.assertIsInstance(result[0], KeyError, result)
        self.assertEqual(list((self.tmp / "sessions" / "s" / "inbox").iterdir()), [], "not into the new one")

    def test_a_branch_inside_a_worktree_waits_for_a_removal_of_its_owner(self):
        from farmout import sessions
        wt = self.tmp / "wt"
        wt.mkdir()
        self.make("a", iso={"worktree": str(wt), "rel": ".", "top": str(self.tmp), "branch": "b", "patch": None})
        self.make("b", cwd=str(wt), parent="a", base=1)
        result = []
        t = threading.Thread(target=lambda: result.append(self.attempt(sessions.fork, "b")))
        with sessions.guarded("a"):  # what removing a holds while it checks for branches in its worktree
            t.start()
            time.sleep(0.3)
            self.assertTrue(t.is_alive(), "branching off b waits: it would work in a's worktree")
        t.join(10)
        self.assertEqual(result[0][0]["parent"], "b", result)

    def make(self, name, **meta):
        d = self.tmp / "sessions" / name
        (d / "inbox").mkdir(parents=True)
        (d / "turns").mkdir()
        out = d / "turns" / "0001.md"
        out.write_text("one")
        (d / "turns" / "0001.json").write_text(json.dumps({"n": 1, "id": "m1", "prompt": "say one", "status": "ok",
                                                          "worker": "codex", "output": str(out)}))
        (d / "meta.json").write_text(json.dumps({"name": name, "worker": "codex", "cwd": str(self.tmp), "turns": 1,
                                                 "model": None, "effort": None, "native_id": None, "base": 0,
                                                 "created": time.time(), **meta}))

    @staticmethod
    def attempt(fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            return e


class LiveLanesTest(unittest.TestCase):
    """The rows `farmout review` and friends draw on a terminal while workers run."""

    def setUp(self):
        import shutil
        import tempfile
        from farmout import sessions
        self.root = Path(tempfile.mkdtemp(prefix="farmout-lanes-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        old, sessions.ROOT = sessions.ROOT, self.root
        self.addCleanup(setattr, sessions, "ROOT", old)

    def turn(self, name, **rec):
        d = self.root / name / "turns"
        d.mkdir(parents=True)
        (d / "0001.json").write_text(json.dumps({"n": 1, "started": time.time() - 3, **rec}))

    def test_a_row_per_worker_redrawn_in_place(self):
        import io
        from farmout import live
        out = self.root / "claude.md"
        out.write_text("a.py:2 | high | divides by zero | f(0) | guard\na.py:9 | low | typo | - | fix\n")
        self.turn("review-9-codex", status="running", worker="codex")
        self.turn("review-9-claude", status="ok", worker="claude", secs=12.3, output=str(out))
        self.turn("review-9-agy", status="failed", worker="copilot", secs=4)
        buf = io.StringIO()
        lanes = live.Lanes("review-9", ["codex", "claude", "agy", "copilot"], "reviewing", title="farmout review",
                           findings=True, out=buf)
        lanes.draw()
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.getvalue())
        self.assertRegex(text, r"codex\s+reviewing…")
        self.assertRegex(text, r"claude\s+2 findings\s+12s")
        self.assertRegex(text, r"agy\s+failed → copilot")
        self.assertRegex(text, r"copilot\s+starting…")
        lanes.note("review-9-claude", "tests pass")
        lanes.draw()
        self.assertIn("\x1b[5A", buf.getvalue(), "the next frame moves back up over the title and four rows")
        self.assertIn("tests pass", buf.getvalue())

    def test_times_line_up_whatever_a_row_says(self):
        import io
        from farmout import live
        one, two = self.root / "one.md", self.root / "two.md"
        one.write_text("a.py:2 | high | divides by zero | f(0) | guard\n")
        two.write_text("a.py:2 | high | divides by zero | f(0) | guard\na.py:9 | low | typo | - | fix\n")
        self.turn("review-6-codex", status="ok", worker="codex", secs=7, output=str(one))
        self.turn("review-6-claude", status="ok", worker="claude", secs=12, output=str(two))
        self.turn("review-6-copilot", status="running", worker="copilot")
        buf = io.StringIO()
        live.Lanes("review-6", ["codex", "claude", "copilot"], "reviewing", findings=True, out=buf).draw()
        rows = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.getvalue()).splitlines()
        self.assertEqual(len({re.search(r"\d+s$", r).start() for r in rows}), 1, rows)

    def test_nothing_is_drawn_until_a_worker_starts_or_off_a_terminal(self):
        import io
        from farmout import live
        buf = io.StringIO()
        live.Lanes("review-8", ["codex"], "reviewing", out=buf).draw()
        self.assertEqual(buf.getvalue(), "", "an empty review never shows lanes")
        self.assertFalse(live.enabled(type("A", (), {"json": True})()))

    def test_a_waiting_row_for_one_session_that_goes_when_done(self):
        import io
        from farmout import live
        self.turn("cache", status="ok", worker="claude", secs=9, id="m1")
        buf = io.StringIO()
        lanes = live.Lanes(None, None, "working", lanes=[("claude", "claude", "cache")], ids={"cache": "m3"}, keep=False,
                           out=buf)
        (self.root / "cache" / "turns" / "0002.json").write_text(json.dumps(
            {"n": 2, "id": "m2", "started": time.time() - 30, "status": "running", "worker": "claude"}))
        lanes.draw()
        self.assertRegex(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.getvalue()), r"claude\s+waiting…",
                         "neither the turn it had nor one queued before this message is this one's; it waits")
        (self.root / "cache" / "turns" / "0003.json").write_text(json.dumps(
            {"n": 3, "id": "m3", "started": time.time() - 4, "status": "running", "worker": "codex"}))
        with lanes:
            lanes.draw()
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.getvalue())
        self.assertRegex(text, r"claude\s+working… → codex\s+4s", "a fallback shows where the turn went")
        self.assertTrue(buf.getvalue().endswith("\x1b[1A\r\x1b[J\x1b[?25h"), "the row is erased at the end")

    def test_rows_never_wrap_and_drawing_never_breaks_the_command(self):
        import io
        from farmout import bench, live
        line = live.paint("[1/3] 1234567 " + "fix a very long commit subject " * 6, dim=True)
        self.assertLess(len(live.fit(line, 50)), 50, "a row wider than the terminal is cut, not wrapped")
        self.turn("review-7-codex", status="running", worker="codex")

        class Broken(io.StringIO):
            def write(self, text):
                raise OSError("terminal went away")
        with live.Lanes("review-7", ["codex"], "reviewing", out=Broken()) as lanes:
            lanes.note("review-7-codex", "tests pass")  # no exception escapes, here or on exit
        self.assertIn("I replayed 2 real bug fixes", bench.card_text([], 2), "an empty scoreboard still makes a card")


class LeaderboardTest(CliBase):
    def board(self, *args, rc=0):
        p = subprocess.run([sys.executable, str(SERVER.parent / "bench" / "leaderboard.py"), *args], cwd=self.work,
                           env={**self.env, "FARMOUT_LEADERBOARD": str(self.tmp / "board")}, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        return p.stdout

    def test_lanes_resume_and_render(self):
        self.repo()
        (self.work / "m.py").write_text("def ratio(a, b):\n    return a / b\n")
        self.git("add", "m.py")
        self.git("commit", "-qm", "add ratio")
        (self.work / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        self.git("commit", "-qam", "fix division by zero in ratio")
        sha = self.git("rev-parse", "HEAD").strip()
        board = self.tmp / "board"
        board.mkdir()
        (board / "suite.json").write_text(json.dumps({"lanes": [["codex", None], ["claude", "opus"]], "repos": [
            {"repo": str(self.work), "lang": "Python",
             "fixes": [{"sha": sha, "subject": "fix division by zero in ratio", "files": ["m.py"], "date": "2026-11-01"},
                       {"sha": "0" * 40, "subject": "fix a commit that isn't there", "files": ["m.py"], "date": "2026-11-02"}]}]}))
        self.reviewers_say(codex="m.py:2 | high | divides by zero | ratio(1, 0) | guard\n", claude="NO FINDINGS")
        out = self.board("run")
        self.assertIn("✓ codex", out)
        self.assertIn("· claude:opus", out)
        self.assertIn("x codex  failed: replay failed", out, "one bad fix is recorded, and the run goes on")
        self.assertEqual(self.last("claude")["argv"][self.last("claude")["argv"].index("--model") + 1], "opus")
        self.assertIn('web_search="disabled"', self.last("codex")["argv"], "public fixes: no searching for the answer")
        self.assertNotIn("WebSearch", " ".join(self.last("claude")["argv"]))
        mine = lambda lane: next(p for p in (board / "results" / lane).glob("*.json") if sha[:12] in p.name)
        codex = json.loads(mine("codex").read_text())
        self.assertEqual((codex["found"], codex["model"], codex["findings"][0]["line"], codex["web"]), (True, None, 2, False))
        self.assertIn("0 new results", self.board("run"), "a second run resumes: nothing left to do")
        saved = mine("claude_opus")
        old = json.loads(saved.read_text())  # as if an older parser had missed this reply
        saved.write_text(json.dumps({**old, "reply": "path:2 | high | divides by zero | ratio(1, 0) | guard"}))
        self.assertIn("✓ claude:opus", self.board("rescore"))
        self.assertTrue(json.loads(saved.read_text())["found"])
        saved.write_text(json.dumps(old))
        self.board("render")
        readme = (board / "README.md").read_text()
        self.assertIn("| `codex` | 1/1 | 100% | 1 |", readme)
        self.assertIn("| `claude:opus` | 0/1 | 0% | 1 |", readme)
        self.assertEqual([b["lane"] for b in json.loads((board / "leaderboard.json").read_text())["board"]],
                         ["codex", "claude:opus"])
        self.assertIn("Claude · opus", (board / "card.svg").read_text())
        self.assertEqual(self.git("status", "--porcelain"), "", "the repository is untouched")


class BenchCopyTest(unittest.TestCase):
    """The scratch copy a replayed fix is reviewed in must not give the bug away."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="farmout-bench-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.top = self.tmp / "repo"
        self.top.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost"}
        self.git("init", "-q")
        files = {"m.py": "def ratio(a, b):\n    return a / b\n", "tests/test_m.py": "def test_one():\n    pass\n",
                 "CHANGELOG.md": "# Changes\n"}
        for name, text in files.items():
            (self.top / name).parent.mkdir(exist_ok=True)
            (self.top / name).write_text(text)
        self.git("add", ".")
        self.git("commit", "-qm", "add ratio")
        (self.top / "m.py").write_text("def ratio(a, b):\n    if b == 0:\n        return 0\n    return a / b\n")
        (self.top / "tests" / "test_m.py").write_text("def test_one():\n    pass\n\ndef test_zero_division():\n    pass\n")
        (self.top / "tests" / "test_regression.py").write_text("def test_ratio_by_zero():\n    pass\n")
        (self.top / "CHANGELOG.md").write_text("# Changes\n\n- Fixed: ratio crashed dividing by zero\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fix ZeroDivisionError in ratio")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.top), *args], check=True, capture_output=True, text=True,
                              env=self.env).stdout

    def test_non_ascii_names_and_django_tests_py(self):
        from farmout import bench
        (self.top / "café.py").write_text("def f(x):\n    return x\n")
        (self.top / "tests.py").write_text("def test_f():\n    pass\n")
        self.git("add", ".")
        self.git("commit", "-qm", "add f")
        (self.top / "café.py").write_text("def f(x):\n    return x or 0\n")
        (self.top / "tests.py").write_text("def test_f():\n    pass\n\ndef test_f_none():\n    pass\n")
        self.git("commit", "-qam", "fix f for None")
        c = bench.candidates(str(self.top), 1)[0]
        self.assertEqual(c["files"], ["café.py"], "tests.py stays as fixed; the name isn't C-quoted")
        wt = self.tmp / "wt-cafe"
        bench.prepare(str(self.top), c, wt, fresh=True)
        self.assertEqual((wt / "café.py").read_text(), "def f(x):\n    return x\n")

    def test_a_fresh_copy_keeps_export_ignored_files_and_export_subst_as_written(self):
        from farmout import bench
        (self.top / ".gitattributes").write_text("tests/** export-ignore\nVERSION export-subst\n")
        (self.top / "VERSION").write_text("$Format:%H$\n")
        self.git("add", ".")
        self.git("commit", "-qm", "attributes for source archives")
        (self.top / "m.py").write_text((self.top / "m.py").read_text().replace("return a / b", "return a / float(b)"))
        self.git("commit", "-qam", "fix integer division in ratio")
        c = bench.candidates(str(self.top), 5)[0]
        self.assertEqual(c["sha"], self.git("rev-parse", "HEAD").strip())
        wt = self.tmp / "wt-attrs"
        bench.prepare(str(self.top), c, wt, fresh=True)
        self.assertTrue((wt / "tests" / "test_regression.py").exists(), "export-ignore is for tarballs, not reviews")
        self.assertEqual((wt / "VERSION").read_text(), "$Format:%H$\n", "as the commit has it")

    def test_a_fresh_copy_needs_no_working_git_lfs(self):
        from unittest import mock
        from farmout import bench
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 3\n"
        (self.top / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        (self.top / "data.bin").write_text(pointer)
        self.git("add", ".")
        self.git("commit", "-qm", "track data in LFS")
        (self.top / "m.py").write_text((self.top / "m.py").read_text().replace("return a / b", "return a / float(b)"))
        self.git("commit", "-qam", "fix integer division in ratio")
        c = bench.candidates(str(self.top), 5)[0]
        broken = self.tmp / "gitconfig"  # git-lfs set up, but its program gone: every LFS filter run fails
        broken.write_text('[filter "lfs"]\n\tclean = false\n\tsmudge = false\n\tprocess = false\n\trequired = true\n')
        wt = self.tmp / "wt-lfs"
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(broken)}):
            bench.prepare(str(self.top), c, wt, fresh=True)
            self.assertIn("m.py", bench.changed_lines(str(wt)), "git in the copy works afterwards too")
        self.assertEqual((wt / "data.bin").read_text(), pointer, "LFS files stay pointers")
        self.assertIn("return a / b\n", (wt / "m.py").read_text(), "the fix is undone")

    def test_one_fix_that_wont_undo_doesnt_lose_the_others(self):
        from unittest import mock
        from farmout import bench
        with mock.patch.object(bench, "replay", side_effect=RuntimeError("apply -R failed")), \
                mock.patch.object(bench, "STATE", self.tmp / "state"):
            rows, report = bench.run(str(self.top), 1, ["codex"], progress=lambda m: None)
        self.assertTrue(rows[0]["failed"]["codex"].startswith("replay failed: RuntimeError: apply -R failed"))
        self.assertTrue(report.exists(), "the report is still written")

    def test_cards_name_the_models_farmout_picks_and_fit_one_post(self):
        from farmout import bench, review
        self.assertIn("flash-low", review.MODELS["antigravity"], "CHOSEN must describe the model reviews really use")
        board = [(w, 7, 10, 0) for w in ("antigravity", "codex", "claude", "copilot")]
        text = bench.card_text(board, 10)
        for name in ("Gemini Flash low", "Claude Sonnet", "Codex", "Copilot"):
            self.assertIn(name, text)
        self.assertLessEqual(len(text), 280, "four workers still fit in one post")
        self.assertIn("Gemini Flash low", bench.card_svg(board, 10, "2026-11-18"))

    def test_only_the_source_change_is_undone_and_history_hides_the_fix(self):
        from farmout import bench
        [c] = bench.candidates(str(self.top), 5)
        self.assertEqual(c["files"], ["m.py"], "a new test file doesn't disqualify a fix, and tests aren't undone")
        for fresh in (False, True):
            with self.subTest(fresh=fresh):
                wt = self.tmp / f"wt-{fresh}"
                bench.prepare(str(self.top), c, wt, fresh=fresh)
                self.assertEqual(self.git("diff", "HEAD", "--name-only", cwd=wt), "m.py\n")
                self.assertIn("return a / b", (wt / "m.py").read_text())
                self.assertNotIn("b == 0", (wt / "m.py").read_text(), "the bug is back")
                self.assertTrue((wt / "tests" / "test_regression.py").exists(), "tests stay as fixed")
                self.assertIn("Fixed: ratio", (wt / "CHANGELOG.md").read_text())
                self.assertEqual(self.git("log", "--format=%s", cwd=wt), "snapshot\n", "no fix subject in the history")
                if fresh:
                    self.assertEqual(self.git("log", "--all", "--format=%s", cwd=wt), "snapshot\n")
                else:
                    self.git("worktree", "remove", "--force", str(wt))


class RenderWidthTest(unittest.TestCase):
    def test_the_review_table_fits_the_terminal(self):
        everyone = ["antigravity", "codex", "claude", "copilot"]
        cluster = {"valid": True, "workers": everyone, "severity": "high", "path": "files_api.py", "line": 18,
                   "defect": "SQL injection: the owner is interpolated into the query, so any caller reads every row",
                   "findings": []}
        result = {"empty": False, "workers": everyone, "label": "uncommitted changes", "group": "review-1",
                  "clusters": [cluster], "failed": {}}
        for width in (100, 120, 160):
            longest = max(len(line) for line in review_mod.render(result, 1, width).splitlines())
            self.assertLess(longest, width, f"a row wraps at {width} columns")


class WaitRaceTest(unittest.TestCase):
    def test_a_turn_that_ends_between_the_checks_is_not_interrupted(self):
        # The waiter reads the turn ("running"), then sees no runner: in between, the runner wrote "ok" and exited.
        from unittest import mock
        from farmout import sessions
        running = {"id": "m1", "n": 1, "status": "running"}
        done = {"id": "m1", "n": 1, "status": "ok", "output": "x"}
        with mock.patch.object(sessions, "find", side_effect=[running, done]), \
                mock.patch.object(sessions, "busy", return_value=False):
            self.assertEqual(sessions.wait_msg("s", "m1", timeout=5)["status"], "ok")


class ShortenTest(unittest.TestCase):
    """A long conversation is handed over as an index with line ranges into the full file, the newest turns whole."""

    def turn(self, n, prompt, reply):
        return f"## Turn {n} (codex, ok)\n\n### Prompt\n\n{prompt}\n\n### Reply\n\n{reply}\n"

    def test_short_goes_whole_and_long_gets_an_index_with_line_ranges(self):
        from farmout import sessions
        full = Path("/state/context-0009-full.md")
        self.assertIsNone(sessions.shorten(self.turn(1, "hi", "hello"), full))
        text = "\n".join(["# A conversation from Claude Code\n\nnotes\n"]
                         + [self.turn(n, f"question {n}\nsecond line", f"answer {n} " * 900) for n in range(1, 9)])
        short = sessions.shorten(text, full)
        self.assertLess(len(short), len(text) // 2)
        self.assertTrue(short.startswith("# The conversation so far, shortened"))
        self.assertIn(str(full), short)
        self.assertIn("# A conversation from Claude Code", short, "what came before the turns stays")
        lines = text.split("\n")
        index = re.findall(r"^- Turn (\d+) \(codex, ok\), lines (\d+)–(\d+): question \d+ second line → answer",
                           short, re.M)
        self.assertEqual([n for n, _, _ in index], ["1", "2", "3", "4", "5"], "the older turns, one line each")
        for n, a, b in index:
            self.assertEqual(lines[int(a) - 1], f"## Turn {n} (codex, ok)")
            self.assertIn(f"answer {n}", "\n".join(lines[int(a) - 1:int(b)]))
            self.assertNotIn(f"## Turn {int(n) + 1} ", "\n".join(lines[int(a) - 1:int(b)]))
        for n in (6, 7, 8):
            self.assertIn(self.turn(n, f"question {n}\nsecond line", f"answer {n} " * 900).strip(), short,
                          "the newest turns in full")

    def test_a_heading_quoted_in_an_answer_is_not_a_turn(self):
        from farmout import sessions
        quoted = "Here is the log:\n## Turn 99 (codex, ok)\nthat was it. " + "z" * 9000
        text = "\n".join(self.turn(n, f"question {n}", quoted if n == 2 else f"answer {n} " * 900) for n in range(1, 8))
        short = sessions.shorten(text, Path("/f.md"))
        self.assertEqual(re.findall(r"^- Turn (\d+)", short, re.M), ["1", "2", "3", "4"])
        self.assertIn("7 turns", short)

    def test_a_long_summary_before_few_turns_is_shortened_too(self):
        from farmout import sessions
        text = "\n".join(["# A conversation from Codex\n\n## Summary\n\n" + "s" * 60000 + "\nSUMMARY END\n",
                          self.turn(1, "q", "a")])
        short = sessions.shorten(text, Path("/f.md"))
        self.assertIsNotNone(short)
        self.assertLess(len(short), sessions.TURN_CAP + 2000)
        self.assertIn("SUMMARY END", short)
        self.assertIn("## Turn 1 (codex, ok)", short)

    def test_a_huge_newest_turn_keeps_its_start_and_end(self):
        from farmout import sessions
        text = "\n".join([self.turn(1, "small", "ok"), self.turn(2, "paste", "START" + "y" * 80000 + "END")])
        short = sessions.shorten(text, Path("/f.md"))
        self.assertLess(len(short), sessions.TURN_CAP + 2000)
        self.assertIn("START", short)
        self.assertIn("END", short)
        self.assertRegex(short, r"\[… lines \d+–\d+ of /f\.md are left out here")

    def test_a_very_long_conversation_lists_the_first_turn_and_the_newest_ones(self):
        from farmout import sessions
        text = "\n".join(self.turn(n, f"q{n}", "a" * 500) for n in range(1, 301))
        short = sessions.shorten(text, Path("/f.md"))
        self.assertIn("- Turn 1 (codex, ok)", short)
        self.assertRegex(short, r"- Turns 2–\d+, lines 11–\d+: not listed one by one")
        self.assertIn("- Turn 297 (codex, ok)", short)
        self.assertLessEqual(short.count("\n- Turn"), sessions.INDEX_MAX)


class ContinueTest(CliBase):
    """`farmout continue`: your own Claude Code or Codex session goes on with another worker."""

    def claude_session(self, sid="s1", entry="cli", cwd=None):
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True, exist_ok=True)
        rows = []

        def add(uuid, parent, kind, **kw):
            rows.append({"uuid": uuid, "parentUuid": parent, "type": kind, "cwd": str(cwd or self.work),
                         "entrypoint": entry, "sessionId": sid, **kw})
        add("u0", None, "user", message={"role": "user", "content": "old task from before the compaction"})
        add("b1", None, "system", subtype="compact_boundary", logicalParentUuid="u0")
        add("c1", "b1", "user", isCompactSummary=True, message={"content": "SUMMARY: we refactored the cache"})
        add("u1", "c1", "user", message={"content": "fix the cache bug<system-reminder>hook noise</system-reminder>"})
        add("a1", "u1", "assistant", message={"content": [
            {"type": "thinking", "thinking": "private thoughts"}, {"type": "text", "text": "Looking at it."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{cwd or self.work}/cache.py", "old_string": "a"}}]})
        add("r1", "a1", "user", message={"content": [{"type": "tool_result", "tool_use_id": "x", "content": "edited"}]})
        add("a2", "r1", "assistant", message={"content": [{"type": "text", "text": "Fixed the TTL check."}]})
        add("at", "a2", "attachment", attachment={"type": "file"})
        add("u2", "at", "user", message={"content": "a question that was rewound"})
        add("a3", "u2", "assistant", message={"content": [{"type": "text", "text": "a rewound answer"}]})
        add("m1", "at", "user", isMeta=True, message={"content": "<local-command-caveat>x</local-command-caveat>"})
        add("u3", "m1", "user", message={"content": "now add tests, key sk-ant-api03-" + "A" * 40})
        add("a4", "u3", "assistant", message={"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]})
        add("e1", "a4", "assistant", isApiErrorMessage=True,
            message={"content": [{"type": "text", "text": "5-hour limit reached ∙ resets 3pm"}]})
        odd = [{"type": "ai-title", "aiTitle": "Cache work"}, {"type": "user", "cwd": {"bad": True}},
               {"type": "user", "uuid": "x1", "message": "bad"}, {"type": "user", "uuid": "x2", "message": {"content": {"x": 1}}},
               {"type": "assistant", "uuid": ["l"], "parentUuid": "u0"},
               {"type": "assistant", "uuid": "x3", "parentUuid": ["z"],
                "message": {"content": [{"type": "text", "text": 5}, {"type": "tool_use", "name": 7}]}}]
        f = d / f"{sid}.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in odd + rows) + "\nnot json\n")
        return f

    def test_a_claude_code_session_goes_on_with_codex(self):
        self.claude_session()
        later = self.claude_session("by-farmout", entry="sdk-cli")  # `claude -p`, as farmout's own runs are
        os.utime(later, (time.time() + 60,) * 2)
        self.claude_session("elsewhere", cwd=self.tmp)
        rows = self.json("continue", "--list")
        self.assertEqual([(r["from"], r["id"]) for r in rows], [("claude", "s1")], "this folder's, interactive only")
        self.assertIn("Cache work", self.fo("continue", "--list").stdout, "Claude Code's own title, read cheaply")

        p = self.fo("continue", "-w", "codex", "say carried on", "-n", "go")
        self.assertEqual(p.stdout, "carried on\n")
        self.assertIn("[go · codex · carries on Claude Code session s1, 2 turns and a summary · read-only", p.stderr)
        self.assertIn("redacted 1 secret from the conversation", p.stderr)
        brief = self.last("codex")["brief"]
        self.assertIn("continues an earlier conversation (in Claude Code)", brief)
        ctx = Path(re.search(r"^- (.+context-0001\.md)$", brief, re.M).group(1)).read_text()
        for want in ("SUMMARY: we refactored the cache", "fix the cache bug", "Looking at it.", "Fixed the TTL check.",
                     "- Edit cache.py", "now add tests, key [REDACTED]", "- Bash: pytest -q",
                     "[Claude Code stopped here: 5-hour limit reached ∙ resets 3pm]"):
            self.assertIn(want, ctx)
        for unwanted in ("old task", "hook noise", "private thoughts", "rewound", "local-command", "edited", "AAAAAAAA"):
            self.assertNotIn(unwanted, ctx)
        meta = self.meta("go")
        self.assertEqual((meta["imported"]["from"], meta["imported"]["id"], meta["base"]), ("claude", "s1", 2))
        self.assertEqual(self.fo("send", "go", "recall").stdout, "say carried on\n", "then codex's own conversation")
        tree = self.fo("tree", "go").stdout
        self.assertIn("● 3  say carried on", tree, "numbered on from the imported turns")
        self.assertIn("go · codex · from Claude Code ·", tree)

    def test_the_default_worker_is_not_on_the_login_that_ran_out(self):
        self.claude_session()
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps({"fallback": ["claude", "codex"]}))
        self.fo("continue", "-n", "second")
        self.assertEqual(self.meta("second")["worker"], "claude", "a second Claude account is another login")
        self.assertEqual(self.runs()[-1]["brief"].split("\n")[0], "Carry on from where this conversation stopped.")
        self.fo("continue", "-n", "main", FARMOUT_CLAUDE_DIR="", FARMOUT_CLAUDE_MAIN="1")
        self.assertEqual(self.meta("main")["worker"], "codex", "the claude worker on your main login ran out too")
        self.assertIn("--write lets it edit", self.fo("continue", "-n", "d2", "say x").stderr)
        self.fo("continue", "-n", "w", "--write", "-w", "codex", "say x")
        self.assertEqual(flag(self.last("codex")["argv"], "-s"), "workspace-write")

    def test_a_carried_conversation_never_falls_back_to_the_login_that_ran_out(self):
        self.claude_session()
        self.fo("continue", "-w", "codex", "-n", "fb", "limited codex say rescued", FARMOUT_CLAUDE_DIR="",
                FARMOUT_CLAUDE_MAIN="1")
        self.assertNotIn("claude", self.meta("fb")["fallback"] or [], "farmout never saw Claude Code's limit itself")
        self.assertNotEqual(self.runs()[-1]["name"], "claude")

    def test_a_codex_session_goes_on_with_claude(self):
        day = self.home / ".codex" / "sessions" / "2026" / "11" / "25"
        day.mkdir(parents=True)

        def rollout(sid, source, *items):
            meta = {"type": "session_meta", "payload": {"id": sid, "cwd": str(self.work), "source": source}}
            (day / f"rollout-2026-11-25T10-00-00-{sid}.jsonl").write_text(
                "\n".join(json.dumps(x) for x in (meta, *items)) + "\n")

        def msg(role, *texts):
            kind = "input_text" if role == "user" else "output_text"
            return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                         "content": [{"type": kind, "text": t} for t in texts]}}
        rollout("cx1", "cli", msg("user", "# AGENTS.md instructions for /w\nbe nice", "<environment_context>x</environment_context>"),
                msg("user", "rename the flag"),
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                      "arguments": json.dumps({"command": ["bash", "-lc", "rg old_flag"]})}},
                {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                                      "input": "*** Begin Patch\n*** Update File: cli.py\n@@\n-a\n+b\n*** End Patch"}},
                msg("assistant", "Renamed it in cli.py."))
        rollout("cx2", "exec", msg("user", "a farmout run"))
        self.assertEqual([r["id"] for r in self.json("continue", "--list")], ["cx1"], "`codex exec` runs are not yours")
        self.fo("continue", "--from", "codex", "-w", "claude", "say ok", "-n", "k")
        brief = self.last("claude")["brief"]
        ctx = Path(re.search(r"^- (.+context-0001\.md)$", brief, re.M).group(1)).read_text()
        for want in ("# A conversation from Codex (session cx1", "rename the flag", "- shell: rg old_flag",
                     "- apply_patch cli.py", "Renamed it in cli.py."):
            self.assertIn(want, ctx)
        self.assertNotIn("AGENTS.md", ctx)
        self.assertNotIn("environment_context", ctx)
        self.fo("continue", "--session", "cx", "-w", "copilot", "say again", "-n", "k2")
        self.assertEqual(self.meta("k2")["imported"]["id"], "cx1", "by the start of its id")

    def test_a_codex_compaction_keeps_what_it_replaced_the_history_with(self):
        day = self.home / ".codex" / "sessions" / "2026" / "11" / "25"
        day.mkdir(parents=True)

        def msg(role, text):
            kind = "input_text" if role == "user" else "output_text"
            return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}

        def rollout(sid, *items):
            meta = {"type": "session_meta", "payload": {"id": sid, "cwd": str(self.work), "source": "cli"}}
            (day / f"rollout-2026-11-25T11-00-00-{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in (meta, *items)))

        def context(sid):
            self.fo("continue", "--session", sid, "-w", "codex", "say ok", "-n", sid)
            return Path(re.search(r"^- (.+context-0001\.md)$", self.last("codex")["brief"], re.M).group(1)).read_text()
        rollout("cx3", {"type": "response_item", "payload": msg("user", "first question")},
                {"type": "compacted", "payload": {"message": ""}},  # says nothing: nothing is dropped
                {"type": "response_item", "payload": msg("assistant", "first answer")},
                {"type": "response_item", "payload": "not an object"})
        ctx = context("cx3")
        self.assertIn("first question", ctx)
        self.assertIn("first answer", ctx)
        rollout("cx4", {"type": "response_item", "payload": msg("user", "old question")},
                {"type": "compacted", "payload": {"message": "", "replacement_history": [
                    msg("user", "kept question"), msg("assistant", "kept answer")]}},
                {"type": "response_item", "payload": msg("user", "after the compaction")})
        ctx = context("cx4")
        for want in ("kept question", "kept answer", "after the compaction"):
            self.assertIn(want, ctx)
        self.assertNotIn("old question", ctx, "replaced by the history the compaction kept")
        self.assertEqual(self.meta("cx4")["base"], 2)

    def test_an_api_session_gets_the_earlier_turns_once(self):
        api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        api.seen, api.release = [], threading.Event()
        threading.Thread(target=api.serve_forever, daemon=True).start()
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        env = {"FARMOUT_API_BASE": f"http://127.0.0.1:{api.server_port}/v1", "FARMOUT_API_MODEL": "m"}
        self.claude_session()
        self.fo("continue", "-w", "api", "first", "-n", "a", **env)
        self.fo("send", "a", "second", **env)
        self.assertIn("fix the cache bug", api.seen[0]["body"]["messages"][-1]["content"], "the conversation, inlined")
        self.assertEqual(api.seen[1]["body"]["messages"][-1]["content"], "second", "and not again")

    def test_nothing_to_continue_says_so(self):
        p = self.fo("continue", "-w", "codex", "say x", rc=1)
        self.assertIn("no Claude Code, Codex or Copilot CLI session in this folder or its git repository", p.stderr)
        self.assertEqual(self.fo("continue", "--list", rc=1).stdout,
                         "No Claude Code, Codex or Copilot CLI session here, or anywhere in this git repository.\n")
        self.assertEqual(self.runs(), [], "nothing ran")

    def test_a_long_conversation_is_handed_over_shortened(self):
        self.fo("run", "codex", "big 30000", "-n", "L")
        self.fo("send", "L", "big 30000")
        for i in range(3):
            self.fo("send", "L", f"say reply {i}")
        self.fo("send", "L", "-w", "claude", "say moved")
        brief = self.last("claude")["brief"]
        self.assertIn("has it shortened", brief)
        listed = Path(re.search(r"^- (.+context-0006\.md)$", brief, re.M).group(1))
        full = listed.with_name("context-0006-full.md")
        self.assertIn(str(full), brief)
        short, whole = listed.read_text(), full.read_text()
        self.assertLess(len(short), 12000)
        self.assertIn("x" * 30000, whole)
        a, b = map(int, re.search(r"- Turn 1 \(codex, ok\), lines (\d+)–(\d+)", short).groups())
        self.assertEqual(whole.split("\n")[a - 1], "## Turn 1 (codex, ok)")
        self.assertIn("say reply 2", short, "the newest turns in full")

        self.fo("fork", "L", "--at", "5", "-w", "copilot", "say branched", "-n", "B")
        brief = self.last("copilot")["brief"]
        self.assertRegex(brief, r"(?m)^- .+/B/context-brief\.md$")
        self.assertIn("has it shortened", brief)
        self.assertIn(str(self.state / "sessions" / "B" / "context.md"), brief)

    def test_a_copilot_session_goes_on_with_codex(self):
        def events(sid, told, *rows):
            d = self.home / ".copilot" / "session-state" / sid
            d.mkdir(parents=True)
            start = {"type": "session.start", "data": {"sessionId": sid, "context": {"cwd": str(self.work)}}}
            system = {"type": "system.message", "data": {"role": "system", "content": told}}
            (d / "events.jsonl").write_text("\n".join(json.dumps(r) for r in (start, system, *rows)) + "\n")

        def said(kind, **data):
            return {"type": kind, "data": data}
        events("cp1", "You are the GitHub Copilot CLI, a terminal assistant built by GitHub.",
               said("session.model_change", newModel="auto"), said("user.message", content="rename the flag"),
               said("assistant.message", content="Renaming.", toolRequests=[
                   {"name": "bash", "arguments": {"command": "rg old_flag"}}, {"name": "edit", "arguments": {
                       "path": f"{self.work}/cli.py"}}, "odd"]),
               said("tool.execution_complete", result={"content": "tool output stays out"}),
               said("assistant.message", content="Done: renamed in cli.py."), said("user.message", content="now the docs"),
               said("assistant.message", content=7, toolRequests="odd"),
               said("session.error", message="You've hit your rate limit. Try again later."), {"type": "x", "data": "odd"})
        events("cp2", "You are running in non-interactive mode and have no way to communicate with the user.",
               said("user.message", content="a farmout brief"))
        self.assertEqual([(r["from"], r["id"], r["title"]) for r in self.json("continue", "--list")],
                         [("copilot", "cp1", "rename the flag")])
        self.fo("continue", "--from", "copilot", "-w", "codex", "say carried on", "-n", "cp")
        ctx = Path(re.search(r"^- (.+context-0001\.md)$", self.last("codex")["brief"], re.M).group(1)).read_text()
        for want in ("# A conversation from Copilot CLI (session cp1", "rename the flag", "Renaming.", "- bash: rg old_flag",
                     "- edit cli.py", "Done: renamed in cli.py.", "now the docs",
                     "[Copilot CLI stopped here: You've hit your rate limit. Try again later.]"):
            self.assertIn(want, ctx)
        self.assertNotIn("tool output stays out", ctx)
        self.assertEqual(self.meta("cp")["imported"]["label"], "Copilot CLI")
        self.assertEqual(self.meta("cp")["base"], 2)


class RelayTest(CliBase):
    """`farmout relay`: at Claude Code's usage limit, the prompt goes to another worker; Claude catches up after."""

    def limited_session(self, sid="s1", resets=None, error=None, after=(), when=None, **extra):
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True, exist_ok=True)
        t = when or time.time()  # with milliseconds, as Claude Code writes them
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1000):03d}Z"
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"role": "user", "content": "fix the cache bug"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [
                    {"type": "text", "text": "Fixed the TTL check."}]}},
                {"uuid": "u2", "parentUuid": "a1", "type": "user", "message": {"content": "now add tests"}}]
        if error is not False:
            rows.append({"uuid": "e1", "parentUuid": "u2", "type": "assistant", "isApiErrorMessage": True,
                         "error": "rate_limit", "timestamp": now, "apiErrorStatus": 429, **extra,
                         "message": {"content": [{"type": "text", "text": error or
                                                  "You've hit your 5-hour limit · resets 3:40am (Asia/Kolkata)"}]}})
            if error is None:
                rows[-1].update(apiError="usage_limit_reached", apiErrorParams={"rate_limit_info": {
                    "status": "rejected", "resetsAt": int(resets or time.time() + 3600), "rateLimitType": "five_hour"}})
        f = d / f"{sid}.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows + list(after)) + "\n")
        return f

    def hook(self, prompt, f, sid="s1", script=None, **env):
        event = {"session_id": sid, "transcript_path": str(f), "cwd": str(self.work), "prompt": prompt,
                 "hook_event_name": "UserPromptSubmit", "permission_mode": "default"}
        if script:
            p = subprocess.run([sys.executable, script], input=json.dumps(event), capture_output=True, text=True,
                               env={**self.env}, timeout=60, cwd=self.work)
            self.assertEqual(p.returncode, 0, p.stderr)
            out = p.stdout
        else:
            out = self.fo("relay", "hook", input=json.dumps(event), **env).stdout
        return json.loads(out) if out.strip() else None

    def test_off_it_only_says_it_exists(self):
        f = self.limited_session()
        said = self.hook("say hi", f)
        self.assertEqual(list(said), ["systemMessage"], "nothing blocked, nothing sent")
        self.assertIn("Claude is at its usage limit until", said["systemMessage"])
        self.assertIn("farmout relay on", said["systemMessage"])
        self.assertEqual(self.runs(), [])
        self.assertIsNone(self.hook("say hi", self.limited_session("s2", error=False)), "no limit: silent")
        self.assertIsNone(self.hook("say hi", self.limited_session("s3", resets=time.time() - 60)), "reset passed")
        self.assertIsNone(self.hook("say hi", self.limited_session(
            "s4", error="API Error: Request rejected (429) · overloaded, retrying")), "a passing 429 isn't the limit")
        self.assertIsNone(self.hook("say hi", self.limited_session("s6", entrypoint="sdk-cli")),
                          "`claude -p` runs, farmout's own among them, handle their limits themselves")
        f = self.limited_session("s7")
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        rows[-1]["apiErrorParams"]["rate_limit_info"]["rateLimitType"] = "seven_day_opus"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertIn("(its weekly Opus limit: `/model sonnet` and then `!farmout relay back` keep you on Claude)",
                      self.hook("say hi", f, sid="s7")["systemMessage"])

    def test_on_the_prompt_goes_to_codex_and_claude_catches_up(self):
        p = self.fo("relay", "on", "-w", "codex")
        self.assertIn("Relay on", p.stdout)
        self.assertIn("run `farmout relay install` to add it", p.stdout, "no plugin here: how to add the hook")
        f = self.limited_session()
        said = self.hook("say relayed", f)
        self.assertIs(said["continue"], False, "Claude Code shows the prompt as sent and this under it")
        self.assertRegex(said["stopReason"], r"^Claude is at its usage limit until \d\d:\d\d, so codex answered "
                                             r"\(farmout session codex-1\):\n\nrelayed$")
        brief = self.last("codex")["brief"]
        self.assertIn("continues an earlier conversation (in Claude Code)", brief)
        ctx = Path(re.search(r"^- (.+context-0001\.md)$", brief, re.M).group(1)).read_text()
        self.assertIn("fix the cache bug", ctx)
        self.assertIn("Fixed the TTL check.", ctx)
        self.assertIn("[Claude Code stopped here: You've hit your 5-hour limit", ctx)
        self.assertTrue(self.hook("say again", f)["stopReason"].endswith("\n\nagain"))
        self.assertEqual(len(self.runs()), 2, "the same farmout session")
        self.assertEqual(self.meta("codex-1")["imported"]["id"], "s1")
        self.assertIsNone(self.hook("/compact", f), "Claude Code's own commands pass")
        self.assertEqual(self.json("relay")["relayed"][0]["name"], "codex-1")
        self.assertRegex(self.fo("status", "--line").stdout, r"claude out till \d\d:\d\d → codex \(codex-1\)")

        self.limited_session(resets=time.time() - 5)  # the limit is over
        back = self.hook("what happened?", f)
        self.assertNotIn("continue", back, "the prompt goes to Claude")
        ctx = back["hookSpecificOutput"]["additionalContext"]
        self.assertIn("the user's prompts went to codex through farmout (farmout session codex-1", ctx)
        self.assertIn('marked "Operation stopped by hook" since then are codex\'s, not yours', ctx)
        self.assertIn("read-only access and changed no files", ctx)
        self.assertNotIn("### Reply", ctx, "Claude Code's session file already has the replies it showed")
        self.assertIn("knows codex answered meanwhile", back["systemMessage"])
        self.assertIsNone(self.hook("and now?", f), "told once")
        self.assertEqual(self.json("relay")["relayed"], [])

    def test_an_answer_that_comes_after_claude_code_stopped_waiting(self):
        self.fo("relay", "on", "-w", "codex", "--wait", "1")
        f = self.limited_session()
        said = self.hook("nap 2.5", f)
        self.assertIn("codex is still on it (farmout session codex-1). `!farmout attach codex-1`", said["stopReason"])
        wait_until(lambda: self.json("ls")[0]["state"] not in ("running", "queued"), 30)
        said = self.hook("say next", f)["stopReason"]
        self.assertIn("(An earlier answer, which came after Claude Code stopped waiting:)\n\n## Turn 3 (codex, ok)", said)
        self.assertIn("### Prompt\n\nnap 2.5\n\n### Reply\n\nnapped", said)
        self.assertTrue(said.endswith("so codex answered (farmout session codex-1):\n\nnext"))
        self.hook("nap 2.5", f)  # still running when Claude is back
        self.limited_session(resets=time.time() - 5)
        ctx = self.hook("back?", f)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("codex is still working on «nap 2.5» (`farmout attach codex-1` shows it)", ctx)
        self.assertIsNone(self.hook("still?", f), "told once, not again with every prompt while codex works")
        wait_until(lambda: self.json("ls")[0]["state"] not in ("running", "queued"), 30)
        back = self.hook("and?", f)
        ctx = back["hookSpecificOutput"]["additionalContext"]
        self.assertIn("These replies from codex came after Claude Code stopped waiting", ctx)
        self.assertIn("## Turn 5 (codex, ok)\n\n### Prompt\n\nnap 2.5\n\n### Reply\n\nnapped", ctx)
        self.assertNotIn("Turn 4", ctx)
        self.assertNotIn("While you were at your usage limit", ctx, "said already")
        self.assertIn("codex answered «nap 2.5»:\nnapped", back["systemMessage"], "you see it too")
        self.assertIsNone(self.hook("more", f))

    def test_two_hooks_on_one_prompt_relay_it_once(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        event = json.dumps({"session_id": "s1", "transcript_path": str(f), "cwd": str(self.work), "prompt": "nap 1"})
        hooks = [subprocess.Popen([sys.executable, str(SERVER), "relay", "hook"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, env=self.env, cwd=self.work) for _ in range(2)]
        for h in hooks:  # Claude Code runs a prompt's hooks side by side
            h.stdin.write(event)
            h.stdin.close()
        outs = sorted(h.stdout.read() for h in hooks)
        for h in hooks:
            h.wait(timeout=60)
            h.stdout.close()
        self.assertEqual(outs[0], "", "the plugin's hook and one added by hand: one of them relays")
        self.assertIn("so codex answered (farmout session codex-1):\n\nnapped", json.loads(outs[1])["stopReason"])
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual([r["name"] for r in self.json("ls")], ["codex-1"])

    def test_the_prompt_is_redacted_and_a_command_waits_for_the_catch_up(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        key = "sk-ant-api03-" + "A" * 40
        self.hook(f"say {key}", f)
        self.assertNotIn("AAAAAAAA", json.dumps(self.runs()), "credentials replaced, like the conversation's")
        self.limited_session(resets=time.time() - 5)
        self.assertIsNone(self.hook("/compact", f), "a command doesn't use up the catch-up")
        self.assertIsNone(self.hook("!git status", f))
        self.assertIn("additionalContext", self.hook("hi", f)["hookSpecificOutput"])

    def test_relayed_answers_stay_in_the_conversation(self):
        self.fo("relay", "on", "-w", "codex")
        stop = {"uuid": "p1", "parentUuid": "e1", "type": "user", "message": {"content": "say one"}}
        meta = {"uuid": "m1", "parentUuid": "p1", "type": "user", "isMeta": True, "message": {"content":
                "Operation stopped by hook: Claude is at its usage limit until 03:40, so codex answered (farmout "
                "session codex-1):\n\none"}}
        info = {"uuid": "i1", "parentUuid": "m1", "type": "system", "subtype": "informational", "content": "x"}
        big = [{"uuid": f"b{i}", "parentUuid": "i1" if i == 0 else f"b{i - 1}", "type": "user", "isMeta": True,
                "message": {"content": "y" * 100_000}} for i in range(7)]
        f = self.limited_session(after=[stop, meta, info])
        self.hook("say one", f)
        f = self.limited_session(after=[stop, meta, info] + big)  # the limit is more than 512 KB back now
        said = self.hook("say two", f)
        self.assertTrue(said["stopReason"].endswith("(farmout session codex-1):\n\ntwo"), "still relayed")
        self.fo("continue", "-w", "codex", "-n", "later", "say go")
        ctx = Path(re.search(r"^- (.+context-0001\.md)$", self.last("codex")["brief"], re.M).group(1)).read_text()
        self.assertIn("### Prompt\n\nsay one\n\n### Reply\n\nClaude is at its usage limit until 03:40, so codex "
                      "answered (farmout session codex-1):\n\none", ctx, "a later hand-over keeps relayed answers")
        p = self.fo("relay", "on", "--wait", "1800", rc=1)
        self.assertIn("--wait takes 1 to 840 seconds", p.stderr)

    def test_install_puts_the_hook_into_claude_codes_settings(self):
        cfg = self.home / ".claude" / "settings.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"model": "opus", "hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "my-own-hook"}, {"type": "command", "command": "my-wrapper relay hook"}]}]}}))
        self.assertIn("Added the relay hook to", self.fo("relay", "install").stdout)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["StopFailure"][0]["matcher"], "rate_limit", "and the --auto one")
        cmds = [h["command"] for e in data["hooks"]["UserPromptSubmit"] for h in e["hooks"]]
        self.assertEqual(cmds[:2], ["my-own-hook", "my-wrapper relay hook"], "someone else's \"relay hook\" isn't ours")
        self.assertTrue(cmds[2].endswith(" relay hook"))
        self.assertEqual(data["hooks"]["UserPromptSubmit"][1]["hooks"][0]["timeout"], 900)
        data = json.loads(cfg.read_text())
        entries = data["hooks"]["UserPromptSubmit"]
        entries[0]["hooks"].append(entries.pop(1)["hooks"][0])  # ours, in an entry with the user's own hooks
        cfg.write_text(json.dumps(data))
        self.assertIn("already in", self.fo("relay", "install").stdout)
        self.assertTrue(self.json("relay")["installed"])
        self.assertNotIn("relay install", self.fo("relay", "on").stdout, "installed: nothing more to do")
        p = subprocess.run(cmds[2], shell=True, input=json.dumps({"session_id": "x", "transcript_path": "/nope",
                           "prompt": "hi"}), capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (0, ""), "the command it installed runs")
        self.assertIn("Took the relay hook out of", self.fo("relay", "uninstall").stdout)
        self.assertNotIn("StopFailure", json.loads(cfg.read_text())["hooks"])
        self.assertEqual(json.loads(cfg.read_text())["hooks"]["UserPromptSubmit"],
                         [{"hooks": [{"type": "command", "command": "my-own-hook"},
                                     {"type": "command", "command": "my-wrapper relay hook"}]}], "only ours goes")
        self.assertTrue((cfg.parent / "settings.json.farmout-backup").exists())
        cfg.write_text("{not json")
        self.assertIn("isn't valid JSON", self.fo("relay", "install", rc=1).stderr)
        self.assertEqual(cfg.read_text(), "{not json", "left as it is")
        dots = self.tmp / "dotfiles"
        dots.mkdir()
        (dots / "claude-settings.json").write_text(json.dumps({"model": "opus"}))
        os.chmod(dots / "claude-settings.json", 0o600)
        cfg.unlink()
        cfg.symlink_to(dots / "claude-settings.json")
        victim = self.tmp / "victim.txt"
        victim.write_text("keep me")
        (cfg.parent / "settings.json.farmout-backup").unlink()
        (cfg.parent / "settings.json.farmout-backup").symlink_to(victim)
        self.fo("relay", "install")
        self.assertTrue(cfg.is_symlink(), "a dotfiles link stays a link")
        self.assertIn("relay hook", (dots / "claude-settings.json").read_text())
        self.assertEqual(stat.S_IMODE((dots / "claude-settings.json").stat().st_mode), 0o600, "permissions kept")
        self.assertEqual(victim.read_text(), "keep me", "a link where the backup goes is replaced, not followed")
        self.assertFalse((cfg.parent / "settings.json.farmout-backup").is_symlink())
        cfg.unlink()
        cfg.write_text(json.dumps({"enabledPlugins": {"farmout@farmout": True}}))
        self.assertIn("has the hook already", self.fo("relay", "install").stdout)
        local = self.work / ".claude"
        local.mkdir()
        (local / "settings.local.json").write_text(json.dumps({"enabledPlugins": {"farmout@farmout": False}}))
        self.assertFalse(self.json("relay")["plugin"], "this project turned it off: the most specific file wins")
        self.assertIn("Added the relay hook", self.fo("relay", "install").stdout, "off here: the hook is needed")
        self.fo("relay", "uninstall")
        cfg.write_text("{}")
        (local / "settings.local.json").write_text(json.dumps({"enabledPlugins": {"farmout@farmout": True}}))
        self.assertTrue(self.json("relay")["plugin"])
        self.assertIn("Added the relay hook", self.fo("relay", "install").stdout,
                      "on in one project only: other projects still need the hook in your own settings")

    def test_doctor_says_where_the_relay_stands(self):
        self.assertIn("`farmout relay on --write` sends what you type next", self.fo("doctor").stdout)
        self.fo("relay", "on", "-w", "codex")
        self.assertIn("doesn't have its hook: install the farmout plugin or run `farmout relay install`",
                      self.fo("doctor").stdout)
        self.fo("relay", "install")
        self.assertIn("The relay is on: at Claude Code's usage limit, what you type goes to codex", self.fo("doctor").stdout)

    def test_relay_test_tries_it_before_a_real_limit(self):
        f = self.limited_session(error=False)  # Claude is fine
        self.assertIn("The relay is off: `farmout relay on` first", self.fo("relay", "test").stdout)
        self.fo("relay", "on", "-w", "codex")
        out = self.fo("relay", "test", "--minutes", "5", CLAUDE_CODE_SESSION_ID="s1").stdout
        self.assertRegex(out, r"Claude Code session s1 acts as if at its usage limit until \d\d:\d\d: what you type "
                              r"there goes to codex")
        said = self.hook("say tested", f)["stopReason"]
        self.assertRegex(said, r"^farmout relay test: Claude acts as if at its usage limit until \d\d:\d\d "
                               r"\(`farmout relay back` ends it\), so codex answered \(farmout session codex-1\):"
                               r"\n\ntested$")
        self.fo("relay", "back")
        back = self.hook("and now?", f)
        self.assertIn("the user's prompts went to codex through farmout", back["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook("again", f), "over")

    def stop_failure(self, f, said="You've hit your 5-hour limit · resets 3:40am (Asia/Kolkata)", sid="s1"):
        event = {"session_id": sid, "transcript_path": str(f), "cwd": str(self.work), "hook_event_name": "StopFailure",
                 "error": "rate_limit", "last_assistant_message": said}
        return self.fo("relay", "hook", input=json.dumps(event)).stdout

    def test_auto_carries_on_the_moment_the_limit_cuts_a_turn_off(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        self.assertEqual(self.stop_failure(f), "")
        self.assertEqual(self.runs(), [], "without --auto, nothing starts by itself")
        self.assertIn("With --auto", self.fo("relay", "on", "-w", "codex", "--auto").stdout)
        self.assertEqual(self.stop_failure(self.limited_session("s2", error=False),
                                           said="API Error: Request rejected (429) · retrying", sid="s2"), "")
        self.assertEqual(self.runs(), [], "a passing 429 isn't the limit")
        event = {"session_id": "s1", "transcript_path": str(f), "cwd": str(self.work), "hook_event_name": "StopFailure",
                 "error": "rate_limit", "last_assistant_message": "You've hit your 5-hour limit"}
        self.fo("relay", "hook", input=json.dumps(event), FARMOUT_WORKER="1")
        self.assertEqual(self.runs(), [], "a Claude Code that farmout itself runs as a worker: farmout moves that task")
        self.stop_failure(f)
        self.stop_failure(f)  # Claude Code firing again for the same limit doesn't start a second one
        wait_until(lambda: self.json("ls") and self.json("ls")[0]["state"] not in ("running", "queued"), 30)
        self.assertEqual(len(self.runs()), 1)
        self.assertTrue(self.runs()[0]["brief"].startswith("Carry on from where this conversation stopped."))
        self.assertEqual(self.runs()[0]["env"].get("FARMOUT_WORKER"), "1", "so hooks inside a worker's CLI can tell")
        said = self.hook("say next", f)["stopReason"]
        self.assertIn("(An earlier answer, which came after Claude Code stopped waiting:)\n\n## Turn 3 (codex, ok)\n\n"
                      "### Prompt\n\nCarry on from where this conversation stopped.", said)
        self.assertTrue(said.endswith("so codex answered (farmout session codex-1):\n\nnext"))
        self.assertEqual([r["name"] for r in self.json("ls")], ["codex-1"], "one session for the limit")
        self.stop_failure(self.limited_session("s3", error=False), sid="s3")  # the file doesn't show it yet
        wait_until(lambda: len(self.json("ls")) == 2 and self.json("ls")[0]["state"] not in ("running", "queued"), 30)
        self.assertEqual(self.meta("codex-2")["imported"]["id"], "s3", "Claude Code's own words are enough")

    def test_review4_the_test_lasts_auto_sees_a_new_limit_and_back_holds(self):
        self.fo("relay", "on", "-w", "codex", "--auto")
        new = self.home / ".claude" / "projects" / "-work" / "fresh.jsonl"  # Claude Code hasn't written it yet
        self.fo("relay", "test", CLAUDE_CODE_SESSION_ID="fresh")
        self.assertTrue(self.hook("say one", new, sid="fresh")["stopReason"].endswith("one"))
        self.assertTrue(self.hook("say two", new, sid="fresh")["stopReason"].endswith("two"), "the test goes on")
        self.fo("relay", "back")
        # an older limit, its reset passed, is still the newest entry when StopFailure fires for a new one
        f = self.limited_session("s4", resets=time.time() - 60)
        hit = time.time()
        self.stop_failure(f, sid="s4")
        wait_until(lambda: any(r["name"] != "codex-1" for r in self.json("ls")), 30)
        [carried] = [r for r in self.json("ls") if r["name"] != "codex-1"]
        self.assertEqual(self.meta(carried["name"])["imported"]["id"], "s4", "Claude Code's own words name the new one")
        wait_until(lambda: all(r["state"] not in ("running", "queued") for r in self.json("ls")), 30)
        runs = len(self.runs())
        self.fo("relay", "back")
        self.limited_session("s4", when=hit)  # the file now shows the limit; StopFailure comes again after `relay back`
        self.stop_failure(f, sid="s4")
        time.sleep(1)
        self.assertEqual(len(self.runs()), runs, "after `relay back`, that limit isn't carried on again")

    def test_review4_a_prompt_waits_while_the_carry_on_starts(self):
        import fcntl
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        lockdir = self.state / "relay"
        lockdir.mkdir(parents=True, exist_ok=True)
        with open(lockdir / "s1.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)  # what the StopFailure hook holds while it sets up
            event = json.dumps({"session_id": "s1", "transcript_path": str(f), "cwd": str(self.work),
                                "prompt": "say waited"})
            p = subprocess.Popen([sys.executable, str(SERVER), "relay", "hook"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True, env=self.env, cwd=self.work)
            p.stdin.write(event)
            p.stdin.close()
            time.sleep(1.5)
            fcntl.flock(held, fcntl.LOCK_UN)
        out = p.stdout.read()
        p.wait(timeout=60)
        p.stdout.close()
        self.assertTrue(json.loads(out)["stopReason"].endswith("waited"), "relayed, not let through to Claude")

    def test_review4_the_catch_up_names_only_what_changed_meanwhile(self):
        self.fo("relay", "on", "--write", "-w", "codex")
        self.repo()
        (self.work / "sub").mkdir()
        for name in ("old.py", "gone.py", "ab c.txt"):
            (self.work / "sub" / name).write_text("x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "more")
        (self.work / "sub" / "gone.py").unlink()  # deleted before the relay began
        (self.work / "sub" / bytes([99, 97, 102, 0xe9]).decode("latin-1")).write_text("odd name")
        f = self.limited_session()
        event = {"session_id": "s1", "transcript_path": str(f), "cwd": str(self.work / "sub"), "prompt": "write new.py 1"}
        self.fo("relay", "hook", input=json.dumps(event), cwd=self.work / "sub")
        self.git("mv", "sub/ab c.txt", "sub/d.txt")
        self.fo("relay", "back")
        event["prompt"] = "back?"
        ctx = json.loads(self.fo("relay", "hook", input=json.dumps(event), cwd=self.work / "sub").stdout)
        ctx = ctx["hookSpecificOutput"]["additionalContext"]
        changed = re.search(r"Changed since then: (.*)\.", ctx).group(1).split(", ")
        self.assertIn("sub/new.py", changed)
        self.assertIn("sub/d.txt", changed)
        self.assertNotIn("sub/gone.py", changed, "deleted before the relay began")
        self.assertNotIn("c.txt", " ".join(changed).replace("sub/d.txt", ""), "a rename's old path isn't a file")

    def test_a_week_old_record_goes(self):
        (self.state / "relay").mkdir(parents=True)
        old = time.time() - 8 * 86400
        (self.state / "relay" / "old.json").write_text(json.dumps({"session": "old", "since": old, "until": old + 3600}))
        (self.state / "relay" / "new.json").write_text(json.dumps({"session": "new", "since": time.time(), "name": "x"}))
        self.fo("relay", "back")
        self.assertEqual(sorted(p.name for p in (self.state / "relay").glob("*.json")), ["new.json"])

    def test_review5_a_quick_repeat_is_relayed_and_a_wait_past_the_reset_lets_it_go(self):
        import fcntl
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        self.assertTrue(self.hook("say twice", f)["stopReason"].endswith("twice"))
        self.assertTrue(self.hook("say twice", f)["stopReason"].endswith("twice"), "the same prompt again: relayed")
        g = self.limited_session("s8", resets=time.time() + 1.5)
        lockdir = self.state / "relay"
        with open(lockdir / "s8.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, str(SERVER), "relay", "hook"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True, env=self.env, cwd=self.work)
            p.stdin.write(json.dumps({"session_id": "s8", "transcript_path": str(g), "cwd": str(self.work),
                                      "prompt": "say late"}))
            p.stdin.close()
            time.sleep(3)
            fcntl.flock(held, fcntl.LOCK_UN)
        out = p.stdout.read()
        p.wait(timeout=60)
        p.stdout.close()
        self.assertNotIn('"continue": false', out, "the reset came while it waited: the prompt goes to Claude")

    def test_after_back_the_old_limit_isnt_relayed_again(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        self.hook("say one", f)
        self.fo("relay", "back")
        self.assertIn("hookSpecificOutput", self.hook("hello", f), "caught up")
        self.assertIsNone(self.hook("again", f), "the file still ends in that limit: it goes to Claude, as asked")
        self.assertEqual(len(self.runs()), 1)

    def test_review9_an_old_copy_of_a_relay_record_never_wins(self):
        def stale(name):  # a hook's copy of the record, read before what comes next
            return in_farmout(self.env, f"relay.keep('s9', {{'session': 's9', 'since': time.time(), 'name': {name!r}}})\n"
                                        "print(json.dumps(relay.record('s9')))").strip()
        get = lambda: json.loads(in_farmout(self.env, "print(json.dumps(relay.record('s9')))"))
        old = stale("codex-1")
        self.fo("relay", "test", CLAUDE_CODE_SESSION_ID="s9")  # on the same relay
        in_farmout(self.env, f"relay.keep('s9', {{**json.loads({old!r}), 'shown': ['m1']}})")
        self.assertIn("test", get(), "a `relay test` written meanwhile stays")
        self.assertEqual(get()["shown"], ["m1"])
        old = stale("codex-2")
        self.fo("relay", "back")
        self.fo("relay", "test", CLAUDE_CODE_SESSION_ID="s9")  # a relay of its own
        new = get()
        in_farmout(self.env, f"relay.keep('s9', {{**json.loads({old!r}), 'shown': ['m2']}})")
        self.assertEqual(get(), new, "the older relay's copy doesn't overwrite the newer relay")
        in_farmout(self.env, f"relay.retire('s9', json.loads({old!r}))")
        self.assertEqual(get(), new, "nor does its catch-up retire it")

    def test_review10_a_relay_test_written_meanwhile_is_neither_undone_nor_retired(self):
        get = lambda: json.loads(in_farmout(self.env, "print(json.dumps(relay.record('s9')))"))
        in_farmout(self.env, "relay.keep('s9', {'session': 's9', 'since': time.time(), 'name': 'codex-1'})")
        self.fo("relay", "test", "--minutes", "60", CLAUDE_CODE_SESSION_ID="s9")
        old = json.dumps(get())  # a hook's copy, then a shorter test
        self.fo("relay", "test", "--minutes", "1", CLAUDE_CODE_SESSION_ID="s9")
        short = get()["test"]
        in_farmout(self.env, f"relay.keep('s9', {{**json.loads({old!r}), 'shown': ['m1']}})")
        self.assertEqual(get()["test"], short, "the shorter test stands")
        in_farmout(self.env, f"relay.retire('s9', {{**json.loads({old!r}), 'announced': True, 'shown': ['m9']}})")
        self.assertEqual(get()["test"], short, "the 60-minute test's catch-up doesn't retire the shorter one after it")
        self.assertEqual((get().get("announced"), get()["shown"]), (True, ["m9"]), "what the catch-up did is kept")

    def test_review8_after_back_a_test_or_a_new_limit_is_a_relay_of_its_own(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        self.assertIn("farmout session codex-1", self.hook("say one", f)["stopReason"])
        self.fo("relay", "back")
        self.fo("relay", "test", CLAUDE_CODE_SESSION_ID="s1")  # before any prompt reached Claude
        self.assertIn("farmout session codex-2", self.hook("say two", f)["stopReason"])
        self.fo("relay", "back")
        self.limited_session(resets=time.time() + 7200)  # a new limit, again before any prompt reached Claude
        self.assertIn("farmout session codex-3", self.hook("say three", f)["stopReason"])

    def test_review8_a_back_that_came_while_the_catch_up_ran_is_kept(self):
        stale = in_farmout(self.env, "relay.keep('s9', {'session': 's9', 'since': time.time(), 'name': 'codex-1'})\n"
                                     "print(json.dumps(relay.record('s9')))").strip()
        self.fo("relay", "back")  # while the catch-up held its copy of the record
        in_farmout(self.env, f"relay.retire('s9', json.loads({stale!r}))")
        self.assertTrue(json.loads(in_farmout(self.env, "print(json.dumps(relay.record('s9')))"))["back"])

    def test_what_back_leaves_behind_holds_back_no_later_relay(self):
        self.fo("relay", "on", "-w", "codex")
        f = self.limited_session()
        self.hook("say one", f)
        self.fo("relay", "back")
        self.assertIn("hookSpecificOutput", self.hook("hello", f), "caught up")
        self.assertIn("Nothing is being relayed", self.fo("relay", "back").stdout)
        self.fo("relay", "test", CLAUDE_CODE_SESSION_ID="s1")
        self.assertTrue(self.hook("say two", f)["stopReason"].endswith("two"), "a test after `relay back` relays")
        self.fo("relay", "back")
        self.assertIn("hookSpecificOutput", self.hook("hello again", f), "caught up again")
        self.limited_session(resets=time.time() + 7200)  # a real limit later
        said = self.hook("say three", f)["stopReason"]
        name = re.search(r"farmout session (codex-\d+)", said).group(1)
        self.assertIn(f"({name})", self.fo("status", "--line").stdout, "the new relay shows in the status line")
        self.assertIn("the relayed conversation goes to Claude again", self.fo("relay", "back").stdout)

    def test_back_early_and_a_new_limit_starts_afresh(self):
        self.fo("relay", "on", "--write", "-w", "codex")
        self.repo()
        f = self.limited_session()
        self.hook("write a.py x = 2", f)
        self.assertTrue(self.meta("codex-1")["write"])
        self.assertIn("Claude is back: the relayed conversation goes to Claude again", self.fo("relay", "back").stdout)
        back = self.hook("hello again", f)
        ctx = back["hookSpecificOutput"]["additionalContext"]
        self.assertIn("`git status` and `git diff` show what it changed. Changed since then: a.py.", ctx)
        self.limited_session(resets=time.time() + 7200)  # Claude ran out again later: a new limit, a new session
        self.assertIn("farmout session codex-2", self.hook("say two", f)["stopReason"])
        self.fo("relay", "off")
        self.assertIn("farmout relay on", self.hook("say three", f)["systemMessage"])

    def test_an_older_wording_and_the_plugin_script(self):
        self.fo("relay", "on", "--isolate", "-w", "codex")
        self.repo()
        until = int(time.time()) + 600
        f = self.limited_session(error=f"Claude AI usage limit reached|{until}")
        said = self.hook("say from the plugin", f, script=str(SERVER.parent / "hooks" / "relay.py"))
        self.assertIn(time.strftime("until %H:%M", time.localtime(until)), said["stopReason"])
        self.assertTrue(self.meta("codex-1")["isolate"])
        bad = subprocess.run([sys.executable, str(SERVER.parent / "hooks" / "relay.py")], input="not json",
                             capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual((bad.returncode, bad.stdout), (0, ""), "a hook that fails never stops the prompt")
        self.assertIsNone(self.hook("say x", self.tmp / "nope.jsonl", sid="s5"), "no session file: nothing to do")

    def test_the_default_worker_is_not_on_the_login_that_ran_out(self):
        self.fo("relay", "on")
        cfg = self.home / ".config" / "farmout"
        (cfg / "settings.json").write_text(json.dumps({"fallback": ["claude", "codex"]}))
        said = self.hook("say hi", self.limited_session())
        self.assertIn("so claude answered", said["stopReason"], "a second Claude account is another login")
        said = self.hook("say hi", self.limited_session("s9"), sid="s9", FARMOUT_CLAUDE_DIR="", FARMOUT_CLAUDE_MAIN="1")
        self.assertIn("so codex answered", said["stopReason"], "farmout's claude is on the login that ran out")
        (cfg / "settings.json").write_text(json.dumps({"workers": ["claude"]}))
        said = self.hook("say hi", self.limited_session("s10"), sid="s10", FARMOUT_CLAUDE_DIR="", FARMOUT_CLAUDE_MAIN="1")
        self.assertIn("nothing could take over: no worker but claude is ready here", said["stopReason"])


class CompletionTest(CliBase):
    """`farmout completion bash|zsh|fish`, made from the parser."""

    def test_bash_completes_commands_options_workers_and_sessions(self):
        (self.bindir / "farmout").write_text(f"#!/bin/sh\nexec {sys.executable} {SERVER} \"$@\"\n")
        (self.bindir / "farmout").chmod(0o755)
        self.fo("run", "codex", "say hi", "-n", "alpha")
        script = self.tmp / "c.bash"
        script.write_text(self.fo("completion", "bash").stdout)
        probe = self.tmp / "probe.sh"
        probe.write_text('source "$1"; shift\nCOMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]}-1)); _farmout\n'
                         'echo "${COMPREPLY[*]}"\n')

        def complete(*words):
            p = subprocess.run(["bash", str(probe), str(script), "farmout", *words], capture_output=True, text=True,
                               env=self.env, timeout=60)
            self.assertEqual(p.stderr, "")
            return p.stdout.split()
        self.assertEqual(complete("rel"), ["relay"])
        self.assertIn("alpha", complete("send", ""), "session names, as you type")
        self.assertEqual(complete("send", "alpha", "--b"), ["--bg"])
        self.assertIn("codex", complete("send", "alpha", "-w", ""))
        self.assertIn("auto", complete("run", ""))
        self.assertEqual(complete("pipe", "run", "--s"), ["--set"])
        self.assertEqual(complete("relay", ""), ["on", "off", "back", "test", "install", "uninstall", "hook"])
        if shutil.which("zsh"):
            zsh = self.tmp / "c.zsh"
            zsh.write_text(self.fo("completion", "zsh").stdout)
            self.assertEqual(subprocess.run(["zsh", "-n", str(zsh)], capture_output=True).returncode, 0)
        fish = self.fo("completion", "fish").stdout
        self.assertIn("complete -c farmout -n __fish_use_subcommand -a 'relay'", fish)
        self.assertIn("complete -c farmout -n '__fish_seen_subcommand_from send' -a '(farmout _names 2>/dev/null)'", fish)
        self.assertIn("-l worker -r -a 'antigravity codex", fish)
        self.assertEqual(self.fo("_names").stdout, "alpha\n")


class RulesTest(CliBase):
    """A project's instructions for AI agents reach the workers whose CLI wouldn't read them."""

    def test_claude_md_reaches_codex_but_not_claude_or_copilot(self):
        self.repo()
        (self.work / "CLAUDE.md").write_text("Always use tabs.\n")
        (self.work / "sub").mkdir()
        self.fo("run", "codex", "say hi", cwd=self.work / "sub")
        brief = self.last("codex")["brief"]
        self.assertIn("This project's instructions for AI coding agents, written for another agent; follow them too:"
                      "\n\n<instructions file=\"../CLAUDE.md\">\nAlways use tabs.\n</instructions>", brief)
        for w in ("claude", "copilot"):
            self.fo("run", w, "say hi", cwd=self.work / "sub")
            self.assertNotIn("CLAUDE.md", self.last(w)["brief"], f"{w} reads it by itself")
        (self.work / "sub" / "deep").mkdir()
        (self.work / "sub" / "CLAUDE.md").write_text("In sub, use spaces.\n")
        self.fo("run", "codex", "say hi", cwd=self.work / "sub" / "deep")
        brief = self.last("codex")["brief"]
        self.assertLess(brief.index('file="../CLAUDE.md"'), brief.index('file="../../CLAUDE.md"'), "every folder up")
        self.fo("run", "antigravity", "say hi", "--isolate", cwd=self.work / "sub")
        run = self.last("agy")
        self.assertNotIn("--add-dir", run["argv"], "quoted, not listed: no access to the main tree for a writer")
        self.assertIn("Always use tabs.", run["brief"], "an untracked CLAUDE.md in the main tree still counts")
        self.git("add", "CLAUDE.md")
        self.git("commit", "-qm", "rules")
        (self.work / "CLAUDE.md").write_text("Always use tabs, and a local change.\n")
        self.fo("run", "codex", "say hi", "--isolate")
        brief = self.last("codex")["brief"]
        self.assertEqual(brief.count("<instructions"), 1, "the worktree's own copy (with your uncommitted change), once")
        self.assertIn('<instructions file="CLAUDE.md">\nAlways use tabs, and a local change.', brief)
        (self.work / "AGENTS.md").write_text("Use tabs.\n")
        self.fo("run", "codex", "say hi")
        self.assertNotIn("instructions for AI coding agents", self.last("codex")["brief"], "codex reads AGENTS.md")
        (self.work / "AGENTS.md").unlink()
        (self.work / ".farmout").mkdir(exist_ok=True)
        (self.work / ".farmout" / "settings.json").write_text(json.dumps({"rules": False}))
        self.fo("run", "codex", "say hi")
        self.assertNotIn("CLAUDE.md", self.last("codex")["brief"], "rules = false turns it off")

class QuotaTest(CliBase):
    """`farmout status` shows how much of Codex's limits is used, as Codex last wrote it in its session files."""

    def test_codex_usage_from_its_own_session_files(self):
        day = self.home / ".codex" / "sessions" / "2026" / "11" / "26"
        day.mkdir(parents=True)
        now = time.time()

        def count(primary, secondary, ts):
            return {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts)), "type": "event_msg",
                    "payload": {"type": "token_count", "info": None, "rate_limits": {
                        "primary": {"used_percent": primary, "window_minutes": 300, "resets_at": int(now + 3600)},
                        "secondary": {"used_percent": secondary, "window_minutes": 10080, "resets_at": int(now - 60)},
                        "plan_type": "plus"}}}
        f = day / "rollout-2026-11-26T00-00-00-x.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in (
            {"type": "session_meta", "payload": {"id": "x", "cwd": str(self.work), "source": "exec"}},
            count(10, 20, now - 900), {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": "odd"}},
            count(82, 40, now - 600))) + "\n")
        out = self.fo("status").stdout
        self.assertRegex(out, r"codex \(plus\): 5-hour 82% used, resets \d\d:\d\d · weekly 0% \(reset since\) "
                              r"\(as of 10m ago\)")
        self.assertIn("codex 5h 82%", self.fo("status", "--line").stdout, "50% or more shows in the one-liner")
        q = self.json("status")["quota"]["codex"]
        self.assertEqual([(w["name"], w["used"]) for w in q["windows"]], [("5-hour", 82), ("weekly", 0)])
        f.unlink()
        self.assertEqual(self.fo("status", "--line").stdout, "farmout: idle\n")


class AcpTest(CliBase):
    """`farmout acp`: an Agent Client Protocol agent an editor talks to over stdio."""

    def start(self):
        import queue
        p = subprocess.Popen([sys.executable, str(SERVER), "acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.work)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdin.close(), p.stdout.close(), p.stderr.close()))
        self.acp, self.seen, self.n, self.lines = p, [], 0, queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(x) for x in p.stdout], daemon=True).start()
        return p

    def say(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.acp.stdin.write(json.dumps(msg) + "\n")
        self.acp.stdin.flush()
        return None if notify else self.n

    def answer(self, mid, timeout=60):
        """The response to request mid; the notifications before it go to self.seen."""
        import queue
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=1)
            except queue.Empty:
                if self.acp.poll() is not None:  # only then: reading stderr waits for the agent to exit
                    self.fail("the agent exited: " + self.acp.stderr.read())
                continue
            msg = json.loads(line)
            self.assertEqual(msg["jsonrpc"], "2.0")
            if msg.get("id") == mid:
                return msg
            self.seen.append(msg)
        self.fail(f"no answer to {mid}")

    def test_an_editor_thread_is_a_farmout_session(self):
        self.start()
        init = self.answer(self.say("initialize", {"protocolVersion": 1, "clientCapabilities": {}}))["result"]
        self.assertEqual(init["protocolVersion"], 1)
        self.assertTrue(init["agentCapabilities"]["loadSession"])
        self.assertEqual(init["agentCapabilities"]["sessionCapabilities"], {"list": {}})
        new = self.answer(self.say("session/new", {"cwd": str(self.work), "mcpServers": []}))["result"]
        sid = new["sessionId"]
        self.assertEqual([o["id"] for o in new["configOptions"]], ["worker", "access"])
        self.assertEqual(new["configOptions"][1]["currentValue"], "read")
        r = self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "codex"}))
        self.assertEqual(r["result"]["configOptions"][0]["currentValue"], "codex")
        done = self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say hi"}]}))
        self.assertEqual(done["result"], {"stopReason": "end_turn"})
        updates = [m["params"]["update"] for m in self.seen if m.get("method") == "session/update"]
        self.assertEqual([u["sessionUpdate"] for u in updates], ["tool_call", "tool_call_update", "agent_message_chunk"])
        self.assertEqual(updates[0]["status"], "in_progress")
        self.assertEqual(updates[1]["status"], "completed")
        self.assertEqual(updates[2]["content"], {"type": "text", "text": "hi"})
        self.assertEqual(self.json("ls")[0]["name"], "acp-1", "a farmout session like any other")
        self.seen.clear()
        f = self.work / "notes.txt"
        f.write_text("x")
        self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [
            {"type": "text", "text": "recall"}, {"type": "resource_link", "uri": f.as_uri(), "name": "notes.txt"}]}))
        self.assertIn(str(f), self.last("codex")["brief"], "a linked file goes to the worker")
        self.assertEqual(self.json("ls")[0]["turns"], 2, "the same conversation")
        self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "access", "value": "write"}))
        self.seen.clear()
        self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say edits"}]}))
        rows = {r["name"]: r for r in self.json("ls")}
        self.assertTrue(self.meta("acp-2")["write"], "file access changed: a new session carries the conversation on")
        self.assertEqual(self.meta("acp-2")["base"], 2)
        self.assertIn("continues an earlier conversation", self.last("codex")["brief"])
        self.assertEqual(set(rows), {"acp-1", "acp-2"})
        bad = self.answer(self.say("session/prompt", {"sessionId": "nope", "prompt": []}))
        self.assertEqual((bad["error"]["code"], bad["error"]["data"]), (-32002, {"sessionId": "nope"}))
        bad = self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "x"}))
        self.assertEqual(bad["error"]["code"], -32602)
        self.assertEqual(self.answer(self.say("no/such"))["error"]["code"], -32601)

    def test_odd_requests_and_a_cancel_right_behind_the_prompt(self):
        self.start()
        self.assertEqual(self.answer(self.say("no/such"))["error"]["code"], -32600, "before initialize: initialize first")
        self.acp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 77}) + "\n")
        self.acp.stdin.flush()
        self.assertEqual(self.answer(77)["error"]["code"], -32600, "a request without a method")
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        self.assertEqual(self.answer(self.say("session/prompt", {"sessionId": [], "prompt": []}))["error"]["code"], -32602)
        sid = self.answer(self.say("session/new", {"cwd": str(self.work), "mcpServers": []}))["result"]["sessionId"]
        self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "codex"}))
        mid = self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "nap 20"},
                                                                         {"type": "resource_link", "name": "x",
                                                                          "uri": "file://elsewhere/etc/hostname"}]})
        self.say("session/cancel", {"sessionId": sid}, notify=True)
        start = time.time()
        self.assertEqual(self.answer(mid)["result"], {"stopReason": "cancelled"})
        self.assertLess(time.time() - start, 15)
        self.assertNotIn("/etc/hostname", json.dumps(self.runs()), "another host's file:// isn't a local file")
        self.seen.clear()
        one = self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say one"}]})
        two = self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say two"}]})
        self.assertEqual(self.answer(one)["result"], {"stopReason": "end_turn"})
        self.assertEqual(self.answer(two)["result"], {"stopReason": "end_turn"})
        said = [m["params"]["update"]["content"]["text"] for m in self.seen
                if m.get("method") == "session/update" and m["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        self.assertEqual(said, ["one", "two"], "back-to-back prompts take turns in one farmout session")
        self.assertEqual([r["name"] for r in self.json("ls")], ["acp-1"])

    def test_a_removed_session_starts_the_thread_afresh(self):
        self.start()
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        sid = self.answer(self.say("session/new", {"cwd": str(self.work), "mcpServers": []}))["result"]["sessionId"]
        self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "codex"}))
        self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say a"}]}))
        self.fo("rm", "acp-1")
        r = self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say b"}]}))
        self.assertEqual(r["result"], {"stopReason": "end_turn"}, "not stuck on the removed session")
        [row] = self.json("ls")
        self.assertEqual(row["turns"], 1, "a fresh session")

    def test_list_and_load_farmout_conversations(self):
        self.fo("run", "codex", "say alpha one", "-n", "alpha")
        self.fo("send", "alpha", "say alpha two")
        (self.tmp / "other").mkdir()
        self.fo("run", "copilot", "say elsewhere", "-n", "beta", cwd=self.tmp / "other")
        self.fo("review", rc=None)  # a review run's sessions aren't conversations
        self.start()
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        listed = self.answer(self.say("session/list", {}))["result"]
        self.assertEqual([x["sessionId"] for x in listed["sessions"]], ["beta", "alpha"], "newest first")
        self.assertEqual(listed["sessions"][1]["title"], "say alpha one")
        self.assertRegex(listed["sessions"][1]["updatedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("nextCursor", listed)
        here = self.answer(self.say("session/list", {"cwd": str(self.work)}))["result"]["sessions"]
        self.assertEqual([x["sessionId"] for x in here], ["alpha"])
        self.assertEqual(self.answer(self.say("session/list", {"cursor": "%%%"}))["error"]["code"], -32602)
        self.seen.clear()
        loaded = self.answer(self.say("session/load", {"sessionId": "alpha", "cwd": str(self.work), "mcpServers": []}))
        self.assertIn("configOptions", loaded["result"])
        replay = [(m["params"]["update"]["sessionUpdate"], m["params"]["update"]["content"]["text"]) for m in self.seen]
        self.assertEqual(replay, [("user_message_chunk", "say alpha one"), ("agent_message_chunk", "alpha one"),
                                  ("user_message_chunk", "say alpha two"), ("agent_message_chunk", "alpha two")])
        done = self.answer(self.say("session/prompt", {"sessionId": "alpha", "prompt": [{"type": "text",
                                                                                          "text": "say three"}]}))
        self.assertEqual(done["result"], {"stopReason": "end_turn"})
        self.assertEqual(self.meta("alpha")["turns"], 3, "the loaded conversation goes on")
        self.assertEqual(self.last("codex")["name"], "codex", "on its own worker")
        self.assertEqual(self.answer(self.say("session/load", {"sessionId": "nope", "cwd": str(self.work),
                                                               "mcpServers": []}))["error"]["code"], -32002)

    def test_review5_load_replays_turns_reuses_an_open_thread_and_checks_the_cursor(self):
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True)
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"content": "plan the cache"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [
                    {"type": "text", "text": "A 5-minute TTL."}]}}]
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.fo("continue", "-w", "codex", "-n", "carried", "say carried")
        self.start()
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        self.assertEqual(self.answer(self.say("session/list", {"cursor": "LTE="}))["error"]["code"], -32602)
        self.seen.clear()
        self.answer(self.say("session/load", {"sessionId": "carried", "cwd": str(self.work), "mcpServers": []}))
        replay = [(m["params"]["update"]["sessionUpdate"], m["params"]["update"]["content"]["text"]) for m in self.seen]
        self.assertEqual(replay[1:], [("user_message_chunk", "plan the cache"), ("agent_message_chunk", "A 5-minute TTL."),
                                      ("user_message_chunk", "say carried"), ("agent_message_chunk", "carried")])
        self.assertTrue(replay[0][1].startswith("# A conversation from Claude Code"))
        mid = self.say("session/prompt", {"sessionId": "carried", "prompt": [{"type": "text", "text": "nap 20"}]})
        wait_until(lambda: self.json("ls")[0]["state"] == "running", 20)
        self.answer(self.say("session/load", {"sessionId": "carried", "cwd": str(self.work), "mcpServers": []}))
        self.say("session/cancel", {"sessionId": "carried"}, notify=True)
        self.assertEqual(self.answer(mid)["result"], {"stopReason": "cancelled"}, "the reopened thread is the same one")

    def test_cancel_takes_back_only_this_threads_message(self):
        self.start()
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        sid = self.answer(self.say("session/new", {"cwd": str(self.work), "mcpServers": []}))["result"]["sessionId"]
        self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "codex"}))
        self.answer(self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say a"}]}))
        self.fo("send", "acp-1", "nap 5", "--bg")  # another front end's turn, running
        wait_until(lambda: self.json("ls")[0]["state"] == "running", 20)
        mid = self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "say b"}]})
        time.sleep(0.5)
        start = time.time()
        self.say("session/cancel", {"sessionId": sid}, notify=True)
        self.assertEqual(self.answer(mid)["result"], {"stopReason": "cancelled"})
        self.assertEqual(self.json("ls")[0]["state"], "running", "answered without waiting for the other turn")
        self.assertLess(time.time() - start, 2.5)
        wait_until(lambda: self.json("ls")[0]["state"] not in ("running", "queued"), 30)
        log = self.fo("log", "acp-1").stdout
        self.assertIn("napped", log, "the other front end's turn ran to the end")
        self.assertNotIn("say b", log, "the taken-back message never ran")

    def test_cancel_stops_the_turn(self):
        self.start()
        self.assertEqual(self.answer(self.say("session/new", {"cwd": str(self.work)}))["error"]["code"], -32600)
        self.answer(self.say("initialize", {"protocolVersion": 1}))
        sid = self.answer(self.say("session/new", {"cwd": str(self.work), "mcpServers": []}))["result"]["sessionId"]
        self.answer(self.say("session/set_config_option", {"sessionId": sid, "configId": "worker", "value": "codex"}))
        mid = self.say("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "nap 20"}]})
        wait_until(lambda: self.json("ls") and self.json("ls")[0]["state"] == "running", 20)
        start = time.time()
        self.say("session/cancel", {"sessionId": sid}, notify=True)
        self.assertEqual(self.answer(mid)["result"], {"stopReason": "cancelled"})
        self.assertLess(time.time() - start, 15)


class AccountsTest(CliBase):
    """More logins of any subscription: `accounts` in your own settings, each a worker of its own."""

    def test_more_logins_of_any_subscription(self):
        cfg = self.home / ".config" / "farmout"
        cfg.mkdir(parents=True, exist_ok=True)
        for d in ("codex-work", "agy-2", "copilot-2"):
            (self.tmp / d).mkdir()
        (cfg / "settings.json").write_text(json.dumps({"accounts": {
            "codex-work": {"worker": "codex", "home": str(self.tmp / "codex-work")},
            "agy-2": {"worker": "antigravity", "home": str(self.tmp / "agy-2")},
            "copilot-2": {"worker": "copilot", "home": str(self.tmp / "copilot-2")},
            "claude-3": {"worker": "claude", "home": str(self.tmp / "nope")},
            "Bad Name": {"worker": "codex", "home": "x"}, "codex": {"worker": "codex", "home": "x"},
            "odd": {"worker": "api", "home": "x"}, "rel": {"worker": "codex", "home": "logins/codex2"}}}))
        doc = self.fo("doctor").stdout
        self.assertRegex(doc, r"ready    codex-work +codex on the login in .*codex-work")
        self.assertIn(f"missing  claude-3     no folder {self.tmp / 'nope'} yet: log in once with "
                      f"`CLAUDE_CONFIG_DIR={self.tmp / 'nope'} claude`", doc)
        for bad in ("Bad Name", "codex ", "odd "):
            self.assertIn(f"ignored  {bad}", doc)
        self.assertIn("ignored  rel          accounts.rel in your settings: home must be the full path", doc)
        (self.work / "CLAUDE.md").write_text("Use tabs.\n")
        self.fo("run", "codex-work", "say ruled")
        self.assertIn("Use tabs.", self.last("codex")["brief"], "an account of codex gets the rules codex would")
        (self.work / "CLAUDE.md").unlink()
        self.fo("run", "codex-work", "say hi", "-n", "w")
        self.assertEqual(self.last("codex")["env"].get("CODEX_HOME"), str(self.tmp / "codex-work"))
        self.fo("run", "agy-2", "say hi")
        self.assertEqual(self.last("agy")["home"], str(self.tmp / "agy-2"), "agy keeps its login under HOME")
        self.fo("run", "copilot-2", "say hi", "-n", "c2")
        self.assertEqual(self.last("copilot")["env"].get("COPILOT_HOME"), str(self.tmp / "copilot-2"))
        self.assertIn("--session-id", self.last("copilot")["argv"], "a login of copilot still picks its own ids")
        self.assertEqual(self.fo("send", "w", "recall").stdout, "say hi\n", "an account's conversation resumes")
        p = self.fo("run", "codex", "homelimit say rescued", "-n", "f", "--fallback", "codex-work")
        self.assertEqual(p.stdout, "rescued\n", "the default login at its limit: the other login takes over")
        self.assertIn("codex is at a usage limit", p.stderr)
        day = self.home / ".codex" / "sessions" / "2026" / "11" / "26"
        day.mkdir(parents=True)
        (day / "rollout-x.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"type": "session_meta", "payload": {"id": "cx", "cwd": str(self.work), "source": "cli"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "rename the flag"}]}})) + "\n")
        settings = json.loads((cfg / "settings.json").read_text())
        (cfg / "settings.json").write_text(json.dumps({**settings, "fallback": ["codex", "codex-work"]}))
        self.fo("continue", "--from", "codex", "-n", "cx", "say go")
        self.assertEqual(self.meta("cx")["worker"], "codex-work", "not the login that ran out; another login of it")
        self.fo("ask", "-w", "codex,codex-work", "say both")
        homes = [r["env"].get("CODEX_HOME") for r in self.runs()[-2:]]
        self.assertEqual(sorted(h or "" for h in homes), ["", str(self.tmp / "codex-work")], "one lane per login")


class ClipTest(CliBase):
    """`farmout copy` (a command from the newest answer, whole) and `farmout paste` (the clipboard's image)."""

    def clipboard(self, image=None):
        (self.bindir / "wl-copy").write_text(f"#!/bin/sh\ncat > {self.tmp / 'copied'}\n")
        types = "image/png\ntext/plain" if image else "text/plain"
        (self.bindir / "wl-paste").write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = --list-types ]; then printf "{types}\\n"; exit 0; fi\n'
            f'cat {self.tmp / "image.png"}\n')
        if image:
            (self.tmp / "image.png").write_bytes(image)
        for f in ("wl-copy", "wl-paste"):
            (self.bindir / f).chmod(0o755)
        return {"WAYLAND_DISPLAY": "wayland-0"}

    def test_commands_come_out_whole(self):
        sys.path.insert(0, str(SERVER.parent))
        from farmout import clip
        text = ("Run these:\n\n```bash\n$ cd site\n$ npm install --no-audit \\\n    --prefer-offline\n```\n\n"
                "Then `farmout relay on --write`, set `rules = false`, pass `--from codex` and read `cache.py`.\n\n"
                "```python\nprint('hi')\n```\n")
        got = [(c["text"], c["lang"]) for c in clip.commands(text)]
        self.assertEqual(got, [("cd site\nnpm install --no-audit \\\n    --prefer-offline", "bash"),
                               ("print('hi')", "python"), ("farmout relay on --write", "")])

    def test_review6_odd_markdown_an_empty_read_and_two_pastes_in_a_second(self):
        sys.path.insert(0, str(SERVER.parent))
        from farmout import clip
        text = ("1. Run:\n   ```bash\n   $ npm install\n   $ for f in *.py; do\n   >   echo $f\n   > done\n   ok\n   ```\n\n"
                "Then `git add .` and `cd ..` and set `ttl = 5`.\r\n```sh\r\ngit status\r\n````\r\n")
        self.assertEqual([c["text"] for c in clip.commands(text)],
                         ["npm install\nfor f in *.py; do\n  echo $f\ndone", "git status", "git add .", "cd .."])
        env = self.clipboard(b"\x89PNG")
        (self.bindir / "wl-paste").write_text('#!/bin/sh\nif [ "$1" = --list-types ]; then echo image/png; exit 0; fi\nexit 1\n')
        self.assertIn("the clipboard holds no image", self.fo("paste", rc=1, **env).stderr, "a failed read isn't an image")
        shots = self.tmp / "shots"
        shots.mkdir()
        one, two = clip.save(b"a", ".png", str(shots)), clip.save(b"b", ".png", str(shots))
        self.assertNotEqual(one, two)
        self.assertEqual((one.read_bytes(), two.read_bytes()), (b"a", b"b"))

    def test_copy_from_your_claude_code_session_and_from_farmout(self):
        env = self.clipboard()
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True)
        reply = "Do this:\n\n```sh\ngit clone https://example.com/a/b.git ~/b && cd ~/b &&\n  make install\n```\nor `git status`."
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"content": "how do I install it?"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [
                    {"type": "text", "text": reply}]}}]
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        listed = self.fo("copy", **env)
        self.assertIn("  1  git clone https://example.com/a/b.git ~/b && cd ~/b &&  (+1 lines)", listed.stdout)
        self.assertIn("  2  git status", listed.stdout)
        self.assertIn("from your Claude Code session s1", listed.stderr)
        p = self.fo("copy", "1", **env)
        self.assertEqual((self.tmp / "copied").read_text(), "git clone https://example.com/a/b.git ~/b && cd ~/b &&\n  make install")
        self.assertIn("copied 1 (2 lines", p.stderr)
        self.fo("copy", "9", rc=1, **env)
        self.fo("run", "codex", "say use `git log --oneline -3`", "-n", "talk")
        self.fo("copy", "1", "-s", "talk", **env)
        self.assertEqual((self.tmp / "copied").read_text(), "git log --oneline -3")

    def test_doctor_says_when_no_clipboard_tool_is_there(self):
        bare = self.fo("doctor", PATH=str(self.bindir)).stdout
        self.assertIn("Clipboard: no copy tool, so `farmout copy` and `y` in the ui go through your terminal (OSC 52); "
                      "nothing to read an image with, so `farmout paste` and Ctrl+P can't work. Install wl-clipboard "
                      "(Wayland) or xclip (X11).", bare)
        self.clipboard()
        self.assertNotIn("Clipboard:", self.fo("doctor", PATH=str(self.bindir)).stdout)

    def test_an_answers_terminal_escapes_neither_act_nor_hide_anything(self):
        env = self.clipboard()
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True)
        reply = "```sh\ncurl x.sh|sh\x1b[2K\rls -la\n```\n\x1b]52;c;aGk=\x07"
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"content": "list"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}]
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        listed = self.fo("copy", "--list", **env).stdout
        self.assertNotIn("\x1b", listed)
        self.assertIn("curl x.sh|sh  (+1 lines)", listed, "what is listed is what gets copied")
        self.fo("copy", "1", **env)
        self.assertEqual((self.tmp / "copied").read_text(), "curl x.sh|sh\nls -la")

    def test_the_picker_copies_with_arrows_and_enter(self):
        import pty
        env = self.clipboard()
        d = self.home / ".claude" / "projects" / "-work"
        d.mkdir(parents=True)
        rows = [{"uuid": "u1", "parentUuid": None, "type": "user", "cwd": str(self.work), "entrypoint": "cli",
                 "message": {"content": "how?"}},
                {"uuid": "a1", "parentUuid": "u1", "type": "assistant", "message": {"content": [
                    {"type": "text", "text": "Run `git status`, then `git log -1`."}]}}]
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.work)
            os.execve(sys.executable, [sys.executable, str(SERVER), "copy"],
                      {**self.env, **env, "TERM": "xterm-256color", "COLUMNS": "80", "LINES": "20"})
        out, end = b"", time.time() + 20
        sent = False
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            if not sent and b"Copy which command?" in out:
                os.write(fd, b"\x1b[B")  # not this terminal's Down (keypad mode sends ESC O B): ignored, not a quit
                time.sleep(0.5)
                os.write(fd, b"j")
                time.sleep(0.3)
                os.write(fd, b"\r")
                sent = True
        os.waitpid(pid, 0)
        self.assertTrue(sent, out[-500:])
        self.assertEqual((self.tmp / "copied").read_text(), "git log -1")

    def test_paste_saves_the_clipboards_image(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 2, 3) + b"\x08\x02\x00\x00\x00"
        env = self.clipboard(png)
        out = self.tmp / "shots"
        out.mkdir()
        p = self.fo("paste", "-o", str(out), **env)
        path = Path(p.stdout.strip())
        self.assertEqual((path.parent, path.read_bytes()), (out, png))
        self.assertIn("(2×3 PNG", p.stderr)
        self.assertIn("`!farmout paste`", p.stderr)
        self.fo("paste", "--copy", **env)
        self.assertTrue((self.tmp / "copied").read_text().startswith(str(self.home / ".cache" / "farmout" / "pastes")))
        env = self.clipboard(None)
        self.assertIn("the clipboard holds no image", self.fo("paste", rc=1, **env).stderr)


if __name__ == "__main__":
    unittest.main()
