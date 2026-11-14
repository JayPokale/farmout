"""Offline tests for the farmout CLI and the MCP session/pipeline tools, against the fake worker CLIs.

Run:  python3 -m unittest discover -s tests -v
"""
import http.server
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from test_server import CLIS, SERVER, Base, FakeAPI, flag, wait_until

sys.path.insert(0, str(SERVER.parent))
from farmout import review as review_mod  # noqa: E402


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

    def test_json_output_and_failures(self):
        out = self.json("run", "codex", "say hi", "-n", "j")
        self.assertEqual((out["session"], out["status"], out["text"]), ("j", "ok", "hi"))
        p = self.fo("run", "codex", "fail boom", rc=1)
        self.assertEqual(p.stdout, "RuntimeError: codex exited 1: boom\n")
        self.assertIn("unavailable worker(s): nope", self.fo("run", "nope", "x", rc=1).stderr)
        self.assertIn("bad session name", self.fo("run", "codex", "x", "-n", "../evil", rc=1).stderr)
        self.assertIn("already exists", self.fo("run", "codex", "x", "-n", "j", rc=1).stderr)
        self.assertIn("no session named 'ghost'", self.fo("send", "ghost", "x", rc=1).stderr)

    def test_busy_session_queues_messages_in_order(self):
        self.fo("run", "codex", "nap 1", "-n", "q", "--bg")
        self.fo("send", "q", "say second", "--bg")
        self.fo("send", "q", "say third", "--bg")
        self.assertEqual(self.fo("wait", "q").stdout, "q: ok\n")
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

    def test_a_worker_at_its_limit_falls_back_and_is_skipped(self):
        p = self.fo("run", "codex", "limited codex say rescued", "--fallback", "claude", "-n", "fb")
        self.assertEqual(p.stdout, "rescued\n")
        self.assertIn("codex hit a usage limit; retried on claude", p.stderr)
        self.assertEqual(self.meta("fb")["worker"], "claude", "the session moves to the worker that answered")
        self.assertEqual(self.fo("send", "fb", "recall").stdout, "limited codex say rescued\n")
        limits = json.loads((self.state / "limits.json").read_text())
        self.assertGreater(limits["codex"], time.time() + 60, "'try again in 2 minutes' is honoured")
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
        self.assertIn("codex hit a usage limit; farmout skips it until", self.fo("stats").stdout)


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


class McpSessionTest(Base):
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

        d = self.work / ".farmout" / "pipelines"
        d.mkdir(parents=True)
        (d / "p.json").write_text(json.dumps({"stage": [{"name": "a", "worker": "claude", "prompt": "say piped"}]}))
        self.assertIn("p: ", self.call(s, "pipeline", action="list", cwd=str(self.work))[1])
        started = self.call(s, "pipeline", action="run", name="p", cwd=str(self.work))[1]
        run_id = re.search(r"run (\S+) in", started).group(1)
        self.assertTrue(wait_until(lambda: ": ok" in self.call(s, "pipeline", action="status", run=run_id)[1], 30))

    def test_cancelling_a_send_stops_that_session(self):
        s = self.start()
        rid = s.request("tools/call", {"name": "send", "arguments": {"session": "c1", "worker": "claude",
                                                                      "message": "sleep mcp", "cwd": str(self.work)}})
        pids = self.pids("mcp")
        s.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid}})
        self.assertAllDead(pids, "a cancelled send kept its worker running")
        self.assertIsNone(s.reply(rid, timeout=2))


if __name__ == "__main__":
    unittest.main()
