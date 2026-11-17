"""Offline tests for the farmout CLI and the MCP session/pipeline tools, against the fake worker CLIs.

Run:  python3 -m unittest discover -s tests -v
"""
import http.server
import importlib.util
import json
import os
import re
import signal
import socket
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
        self.assertIn("took p1's patch back out", self.fo("undo", "p1").stdout)
        self.assertFalse((self.work / "one.txt").exists())
        self.assertIn("no applied patch", self.fo("undo", "p1", rc=1).stderr)

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
        # --share: a post and an image card with scores only, never the repository's names or code
        p = self.fo("bench", "-n", "2", "-w", "codex,claude", "--share")
        self.assertIn("Which AI reviews my code best? I replayed 1 real bug fix", p.stdout)
        self.assertRegex(p.stdout, r"Codex\s+█ 1/1")
        self.assertIn("https://x.com/intent/tweet?text=", p.stdout)
        card = re.search(r"^Image: (\S+)", p.stdout, re.M).group(1)
        svg = Path(card).with_suffix(".svg").read_text()
        self.assertIn("Which AI reviews my code best?", svg)
        for private in ("ratio", "m.py", "division", self.work.name):
            self.assertNotIn(private, p.stdout.split("Report:")[1], private)
            self.assertNotIn(private, svg, private)
        shared = self.json("bench", "-n", "1", "-w", "codex", "--share")["share"]
        self.assertTrue(Path(shared["svg"]).exists())
        self.assertLessEqual(len(shared["text"]), 280, "fits in one post")

    def test_fix_pr_turns_feedback_into_a_race(self):
        self.repo()
        head = self.git("rev-parse", "HEAD").strip()
        gh = self.bindir / "gh"
        gh.write_text(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
if a[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 7, "title": "Speed up", "url": "u", "headRefOid": "{head}",
                      "reviews": [{{"body": "Handle the empty list"}}], "comments": []}}))
elif a[0] == "api":
    print(json.dumps([{{"path": "a.py", "line": 2, "body": "y is wrong"}}]))
elif a[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "tests", "bucket": "fail", "link": "http://ci/1"}}, {{"name": "lint", "bucket": "pass"}}]))
""")
        gh.chmod(0o755)
        out = self.json("fix-pr", "7", "-w", "codex", rc=1)
        self.assertEqual(out["winner"], None, "the fake worker changes nothing")
        brief = self.last("codex")["brief"]
        for want in ("pull request #7 (Speed up)", "review comment: Handle the empty list",
                     "inline comment at a.py:2: y is wrong", "failing check: tests (http://ci/1)"):
            self.assertIn(want, brief)
        self.assertNotIn("lint", brief)
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

    def test_nothing_is_drawn_until_a_worker_starts_or_off_a_terminal(self):
        import io
        from farmout import live
        buf = io.StringIO()
        live.Lanes("review-8", ["codex"], "reviewing", out=buf).draw()
        self.assertEqual(buf.getvalue(), "", "an empty review never shows lanes")
        self.assertFalse(live.enabled(type("A", (), {"json": True})()))

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

if __name__ == "__main__":
    unittest.main()
