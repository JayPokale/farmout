"""Offline tests for server.py: real MCP stdio sessions against fake worker CLIs (fake_cli.py).

No network, logins or real CLIs needed.  Run:  python3 -m unittest discover -s tests -v
"""
import ast
import http.server
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "server.py"
FAKE = (HERE / "fake_cli.py").read_text()
HEADER = re.compile(r"^## \[\d+\] (\S+) (ok|FAILED) \d+s -> (.+)$", re.M)
READ_TOOLS = ["view", "rg", "glob", "web_fetch"]
TRUNCATED = "\n...[truncated; full output in the file above]"
CLI_WORKERS = ("antigravity", "codex", "claude", "copilot")
CLIS = ("agy", "codex", "claude", "copilot")


class Server:
    """server.py in a subprocess; a reader thread collects its replies by request id."""

    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(SERVER)], env=env, text=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.replies, self.junk, self.last_id = {}, [], 0
        self.cv = threading.Condition()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                self.junk.append(line)
                continue
            with self.cv:
                self.replies[msg.get("id")] = msg
                self.cv.notify_all()

    def send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None):
        self.last_id += 1
        self.send({"jsonrpc": "2.0", "id": self.last_id, "method": method, "params": params or {}})
        return self.last_id

    def reply(self, rid, timeout=30):
        """The reply to request rid, or None if none arrives within timeout seconds."""
        with self.cv:
            self.cv.wait_for(lambda: rid in self.replies, timeout)
            return self.replies.pop(rid, None)

    def call(self, method, params=None, timeout=30):
        msg = self.reply(self.request(method, params), timeout)
        if msg is None:
            raise AssertionError(f"no reply to {method} within {timeout}s")
        return msg

    def delegate(self, *tasks, timeout=30):
        """Run tasks in one call. Returns (isError, [(worker, ok, result path, shown text), ...])."""
        res = self.call("tools/call", {"name": "delegate", "arguments": {"tasks": list(tasks)}}, timeout)["result"]
        parts = HEADER.split(res["content"][0]["text"])
        return res["isError"], [(w, s == "ok", Path(p), body.strip("\n"))
                                for w, s, p, body in (parts[i:i + 4] for i in range(1, len(parts), 4))]

    def close(self):
        try:
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            raise
        self.reader.join(5)
        self.p.stdout.close()


def wait_until(cond, timeout=10):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def alive(pid):
    """True while pid is running; a zombie counts as dead."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def flag(argv, name):
    return argv[argv.index(name) + 1]


def listed(argv, name):
    """Values of a variadic flag: everything after it up to the next flag."""
    rest = argv[argv.index(name) + 1:]
    return rest[:next((i for i, a in enumerate(rest) if a.startswith("-")), len(rest))]


def add_dirs(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]


def call_args(*tasks):
    return {"name": "delegate", "arguments": {"tasks": list(tasks)}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="modelmux-test-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home, self.log, self.work = (self.tmp / d for d in ("home", "log", "work"))
        self.log.mkdir()
        self.work.mkdir()

    def make_bin(self, bins):
        """A PATH directory holding fake CLIs under the given names."""
        bindir = Path(tempfile.mkdtemp(dir=self.tmp))
        for name in bins:
            (bindir / name).write_text(f"#!{sys.executable}\n{FAKE}")
            (bindir / name).chmod(0o755)
        (bindir / "git").symlink_to(shutil.which("git"))  # used by isolate
        return bindir

    def start(self, bins=CLIS + ("pdftotext",), **env):
        """Start server.py with only the given fake CLIs on PATH. Env values of None are left unset."""
        env = {"HOME": str(self.home), "PATH": str(self.make_bin(bins)), "FAKE_LOG": str(self.log), "no_proxy": "*",
               "MODELMUX_CLAUDE_DIR": str(self.tmp / "claude-config"), **env}
        s = Server({k: v for k, v in env.items() if v is not None})
        self.addCleanup(self.stop, s)
        return s

    def stop(self, s):
        s.close()
        self.assertEqual(s.junk, [], "stdout must carry JSON-RPC messages only")
        self.assertEqual(s.replies, {}, "unexpected replies")

    def calls(self):
        """Fake CLI runs since the last check, by CLI name."""
        out = {}
        for f in self.log.glob("*.json"):
            rec = json.loads(f.read_text())
            out[rec["name"]] = rec
            f.unlink()
        return out

    def pids(self, tag):
        """(worker, grandchild) pids of a running `sleep <tag>` task; killed at cleanup if still alive."""
        f = self.log / f"{tag}.pids"
        self.assertTrue(wait_until(f.exists), "worker never started")
        pids = [int(x) for x in f.read_text().split()]
        self.addCleanup(lambda: [os.kill(p, signal.SIGKILL) for p in pids if alive(p)])
        return pids

    def assertAllDead(self, pids, msg):
        self.assertTrue(wait_until(lambda: not any(map(alive, pids))), msg)


class ProtocolTest(Base):
    def test_handshake(self):
        s = self.start()
        res = s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})["result"]
        self.assertEqual(res["protocolVersion"], "2025-06-18")
        self.assertEqual(res["serverInfo"]["name"], "modelmux")
        self.assertIn("tools", res["capabilities"])
        for w in CLI_WORKERS:
            self.assertIn(f"\n- {w}: ", res["instructions"])
        s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(s.call("ping")["result"], {})
        self.assertEqual(s.call("resources/list")["error"]["code"], -32601)
        self.assertEqual(s.call("tools/call", {"name": "nope"})["error"]["code"], -32601)

    def test_tools_list_reflects_environment(self):
        api = {"MODELMUX_API_BASE": "http://127.0.0.1:9/v1", "MODELMUX_API_MODEL": "some-model"}
        cases = [
            ((), {}, []),
            (("codex",), {}, ["codex"]),
            (("copilot",), {}, ["copilot"]),
            (("agy", "claude"), {"MODELMUX_CLAUDE_DIR": None}, ["antigravity"]),  # claude needs its config dir
            ((), api, ["api"]),
            (("agy", "codex", "claude"), api, ["antigravity", "codex", "claude", "api"]),
        ]
        for bins, env, want in cases:
            with self.subTest(bins=bins, env=env):
                s = self.start(bins, **env)
                tools = s.call("tools/list")["result"]["tools"]
                got = tools[0]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["worker"]["enum"] if tools else []
                self.assertEqual(got, want)
                instructions = s.call("initialize")["result"]["instructions"]
                self.assertIn("Workers:" if want else "no workers available", instructions)

    def test_ignores_junk_and_client_replies(self):
        s = self.start()
        s.p.stdin.write('not json\n[]\n42\n"text"\n{}\n{"jsonrpc": "2.0", "id": 99, "result": {}}\n')
        s.p.stdin.flush()
        self.assertEqual(s.call("ping", timeout=10)["result"], {})

    def test_exits_on_eof(self):
        s = self.start()
        s.p.stdin.close()
        self.assertEqual(s.p.wait(5), 0)


class DelegateTest(Base):
    def test_tasks_run_in_parallel(self):
        s = self.start()
        room = self.tmp / "room"
        room.mkdir()
        is_error, results = s.delegate(*({"worker": w, "prompt": f"meet {room} {len(CLI_WORKERS)}"} for w in CLI_WORKERS))
        self.assertFalse(is_error)
        self.assertEqual([r[0] for r in results], list(CLI_WORKERS))
        for i, (worker, ok, path, body) in enumerate(results, 1):
            self.assertTrue(ok, body)
            self.assertEqual(body, "met")
            self.assertEqual(path.name, f"{i}-{worker}.md")
            self.assertEqual(path.read_text(), "met")
            self.assertEqual(path.parent.parent, self.home / ".cache" / "modelmux")

    def test_read_only_unless_write(self):
        s = self.start()
        for write in (None, False, True):
            with self.subTest(write=write):
                extra = {} if write is None else {"write": write}
                _, results = s.delegate(*({"worker": w, "prompt": "say hi", "cwd": str(self.work), **extra}
                                          for w in CLI_WORKERS))
                self.assertEqual([r[1] for r in results], [True] * len(CLI_WORKERS), results)
                c = self.calls()
                agy, codex, claude, copilot = (c[n]["argv"] for n in CLIS)
                self.assertIn("--allow-all-tools", copilot)
                self.assertNotIn("--deny-tool", copilot)
                if write:
                    self.assertEqual(flag(agy, "--mode"), "accept-edits")
                    self.assertIn("--sandbox", agy)
                    self.assertEqual(flag(codex, "-s"), "workspace-write")
                    self.assertEqual(flag(claude, "--permission-mode"), "acceptEdits")
                    self.assertNotIn("--tools", claude)
                    self.assertEqual(listed(copilot, "--available-tools"), [*READ_TOOLS, "apply_patch", "edit", "create"])
                else:
                    self.assertEqual(flag(agy, "--mode"), "plan")
                    self.assertNotIn("--sandbox", agy)
                    self.assertEqual(flag(codex, "-s"), "read-only")
                    self.assertEqual(flag(claude, "--tools"), "Read,Grep,Glob,WebSearch")
                    self.assertNotIn("--permission-mode", claude)
                    self.assertEqual(listed(copilot, "--available-tools"), READ_TOOLS)
                self.assertEqual(flag(codex, "-C"), str(self.work))
                for name in CLIS:
                    self.assertEqual(c[name]["cwd"], str(self.work))

    def test_model_defaults_and_overrides(self):
        s = self.start()
        s.delegate(*({"worker": w, "prompt": "say hi"} for w in CLI_WORKERS))
        c = self.calls()
        self.assertNotIn("--model", c["agy"]["argv"])
        self.assertNotIn("-m", c["codex"]["argv"])
        self.assertEqual(flag(c["claude"]["argv"], "--model"), "sonnet")
        self.assertNotIn("--model", c["copilot"]["argv"])
        s.delegate(*({"worker": w, "prompt": "say hi", "model": "m1"} for w in CLI_WORKERS))
        c = self.calls()
        self.assertEqual(flag(c["agy"]["argv"], "--model"), "m1")
        self.assertEqual(flag(c["codex"]["argv"], "-m"), "m1")
        self.assertEqual(flag(c["claude"]["argv"], "--model"), "m1")
        self.assertEqual(flag(c["copilot"]["argv"], "--model"), "m1")

    def test_claude_worker_gets_a_clean_session(self):
        s = self.start(CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli", ANTHROPIC_API_KEY="sk-head",
                       MODELMUX_CLAUDE_DIR="~/second-account")
        s.delegate({"worker": "claude", "prompt": "say hi"})
        rec = self.calls()["claude"]
        self.assertEqual(rec["env"], {"CLAUDE_CONFIG_DIR": str(self.home / "second-account")})
        self.assertIn("--no-session-persistence", rec["argv"])

    def test_files_are_listed_and_outside_dirs_granted(self):
        other, docs = self.tmp / "other", self.tmp / "docs"
        other.mkdir()
        docs.mkdir()
        (self.work / "a.txt").write_text("a")
        (other / "b.txt").write_text("b")
        s = self.start()
        files = ["a.txt", str(other / "b.txt"), str(docs)]
        s.delegate(*({"worker": w, "prompt": "say hi", "files": files, "cwd": str(self.work)} for w in CLI_WORKERS))
        c = self.calls()
        for name in CLIS:
            self.assertEqual(c[name]["brief"], f"say hi\n\nFiles:\n- {self.work / 'a.txt'}\n- {other / 'b.txt'}\n- {docs}")
        for name in ("agy", "claude", "copilot"):
            self.assertEqual(add_dirs(c[name]["argv"]), [str(docs), str(other)])
        s.delegate({"worker": "claude", "prompt": "say hi", "files": ["a.txt"], "cwd": str(self.work)})
        self.assertEqual(add_dirs(self.calls()["claude"]["argv"]), [])

    def test_failures_are_reported_per_task(self):
        s = self.start()
        is_error, results = s.delegate(
            {"worker": "codex", "prompt": "fail boom"},
            {"worker": "nope", "prompt": "say hi"},
            {"worker": "claude", "prompt": "say hi", "files": ["missing.txt"], "cwd": str(self.work)},
            {"worker": "antigravity", "prompt": "agyerror"},
            {"worker": "antigravity", "prompt": "say fine"})
        self.assertFalse(is_error, "one task succeeded")
        self.assertEqual([r[1] for r in results], [False, False, False, False, True])
        bodies = [r[3] for r in results]
        self.assertEqual(bodies[0], "RuntimeError: codex exited 1: boom")
        self.assertEqual(bodies[1], "ValueError: unknown or unavailable worker 'nope'; available: antigravity, codex, claude, copilot")
        self.assertEqual(bodies[2], f"FileNotFoundError: {self.work / 'missing.txt'}")
        self.assertIn('"status": "ERROR"', bodies[3])
        self.assertEqual(bodies[4], "fine")
        for _, _, path, body in results:
            self.assertEqual(path.read_text(), body)

        self.assertTrue(s.delegate({"worker": "nope", "prompt": "x"})[0], "every task failed")
        self.assertIn("tasks is empty", s.call("tools/call", call_args())["error"]["message"])

    def test_output_is_capped_but_kept_in_full_on_disk(self):
        s = self.start()
        _, [(_, _, path, big), (_, _, _, small)] = s.delegate({"worker": "codex", "prompt": "big 70000"},
                                                              {"worker": "claude", "prompt": "say small"})
        self.assertEqual(big, "x" * 30000 + TRUNCATED)  # 60k budget split over two tasks
        self.assertEqual(small, "small")
        self.assertEqual(path.read_text(), "x" * 70000)

    def test_timeout_kills_the_process_group(self):
        s = self.start(MODELMUX_TIMEOUT="1")
        _, [(_, ok, _, body)] = s.delegate({"worker": "codex", "prompt": "sleep t"})
        self.assertFalse(ok)
        self.assertEqual(body, "RuntimeError: timed out after 1s")
        self.assertAllDead(self.pids("t"), "worker processes survived the timeout")

    def test_cancel_kills_the_process_group(self):
        s = self.start()
        rid = s.request("tools/call", call_args({"worker": "claude", "prompt": "sleep c"}))
        pids = self.pids("c")
        self.assertEqual(s.call("ping")["result"], {}, "server must keep serving while a task runs")
        s.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid}})
        self.assertAllDead(pids, "worker processes survived the cancel")
        self.assertIsNone(s.reply(rid, timeout=1), "a cancelled request gets no reply")
        self.assertEqual(s.call("ping")["result"], {})

    def test_isolate_gives_each_write_task_its_own_worktree(self):
        repo = self.work

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout

        git("init", "-q")
        (repo / "app.py").write_text("v1\n")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "init")
        (repo / "app.py").write_text("v2, not committed\n")
        (repo / "notes.txt").write_text("untracked\n")
        s = self.start()
        _, results = s.delegate(*({"worker": w, "prompt": f"write fix.txt by {w}", "cwd": str(repo), "isolate": True,
                                   "files": [str(repo / "app.py")]} for w in ("codex", "claude")))
        c = self.calls()
        for w, ok, _, body in results:
            self.assertTrue(ok, body)
            wt = Path(re.search(r"worktree (\S+), branch", body).group(1))
            patch = Path(re.search(r"patch (\S+):", body).group(1))
            self.assertEqual(c[w]["cwd"], str(wt))
            self.assertEqual((wt / "app.py").read_text(), "v2, not committed\n", "base includes uncommitted work")
            self.assertEqual((wt / "notes.txt").read_text(), "untracked\n", "base includes untracked files")
            self.assertIn(f"- {wt / 'app.py'}", c[w]["brief"], "in-repo files are remapped into the worktree")
            self.assertNotIn("notes.txt", patch.read_text())
            self.assertIn("+by " + w, patch.read_text())
            self.assertNotIn("v2", patch.read_text(), "the patch holds only the worker's change")
        self.assertEqual(add_dirs(c["claude"]["argv"]), [], "no worker is granted the user's own tree")
        self.assertFalse((repo / "fix.txt").exists(), "the user's tree is untouched")
        git("apply", str(patch))
        self.assertEqual((repo / "fix.txt").read_text(), "by claude")

        plain = self.tmp / "plain"
        plain.mkdir()
        _, [(_, ok, _, body)] = s.delegate({"worker": "codex", "prompt": "say x", "cwd": str(plain), "isolate": True})
        self.assertFalse(ok)
        self.assertIn("RuntimeError: git rev-parse", body)

    def test_background_returns_at_once_and_still_stops_on_shutdown(self):
        s = self.start()

        def background(*tasks):
            res = s.call("tools/call", {"name": "delegate", "arguments": {"background": True, "tasks": list(tasks)}},
                         timeout=5)["result"]
            self.assertFalse(res["isError"])
            return [Path(p) for p in re.findall(r"^- (.+\.md)$", res["content"][0]["text"], re.M)]

        [a, b] = background({"worker": "antigravity", "prompt": "say a"}, {"worker": "claude", "prompt": "say b"})
        self.assertTrue(wait_until((a.parent / "done").exists), "no done file after all tasks ended")
        self.assertEqual((a.read_text(), b.read_text()), ("a", "b"))

        [slow, quick] = background({"worker": "codex", "prompt": "sleep bg"}, {"worker": "claude", "prompt": "say quick"})
        pids = self.pids("bg")
        self.assertTrue(wait_until(quick.exists))
        self.assertEqual(quick.read_text(), "quick")
        self.assertFalse(slow.exists() or (slow.parent / "done").exists())
        s.p.stdin.close()
        self.assertEqual(s.p.wait(5), 0)
        self.assertAllDead(pids, "background workers outlived the server")

    def test_eof_and_sigterm_stop_running_workers(self):
        for how in ("eof", "sigterm"):
            with self.subTest(how):
                s = self.start()
                s.request("tools/call", call_args({"worker": "codex", "prompt": f"sleep {how}"}))
                pids = self.pids(how)
                if how == "eof":
                    s.p.stdin.close()
                else:
                    s.p.send_signal(signal.SIGTERM)
                self.assertEqual(s.p.wait(5), 0)
                self.assertAllDead(pids, "worker processes outlived the server")


class DoctorTest(Base):
    def test_reports_ready_and_missing_workers(self):
        env = {"HOME": str(self.home), "PATH": str(self.make_bin(("agy", "copilot"))),
               "MODELMUX_API_BASE": "http://127.0.0.1:9/v1", "MODELMUX_API_MODEL": "some-model"}
        out = subprocess.run([sys.executable, str(SERVER), "doctor"], env=env, capture_output=True,
                             text=True, timeout=30).stdout
        self.assertRegex(out, r"ready +antigravity +/\S+/agy agy 9\.9\.9\n")
        self.assertRegex(out, r"missing +codex +install the Codex CLI")
        self.assertRegex(out, r"missing +claude +log in a second account")
        self.assertRegex(out, r"ready +copilot +/\S+/copilot copilot 9\.9\.9\n")
        self.assertRegex(out, r"ready +api +some-model at http://127\.0\.0\.1:9/v1\n")

    def test_live_check_reports_working_and_broken_logins(self):
        bindir = self.make_bin(("agy", "codex"))
        (bindir / "codex").write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
        env = {"HOME": str(self.home), "PATH": str(bindir), "FAKE_LOG": str(self.log)}
        out = subprocess.run([sys.executable, str(SERVER), "doctor", "--live"], env=env, capture_output=True,
                             text=True, timeout=60).stdout
        self.assertRegex(out, r"works +antigravity +\d+s")
        self.assertRegex(out, r"BROKEN +codex +\d+s +RuntimeError: codex exited 1: not logged in")


class FakeAPI(http.server.BaseHTTPRequestHandler):
    """POST /v1/chat/completions; the first word of the prompt picks the reply."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        word = body["messages"][0]["content"].split()[0]
        if word == "hang":
            self.server.release.wait(30)
            return
        if word == "fail":
            status, data = 500, b"boom"
        else:
            content = {"think": "<think>\nhidden\n</think>answer", "null": None}.get(word, "fine")
            status, data = 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        self.api.seen, self.api.release = [], threading.Event()
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.addCleanup(self.api.release.set)

    def start_api(self, key=None):
        return self.start(("pdftotext",), MODELMUX_API_BASE=f"http://127.0.0.1:{self.api.server_port}/v1/",
                          MODELMUX_API_MODEL="test-model", MODELMUX_API_KEY=key)

    def test_request_inlines_files(self):
        notes, pdf = self.work / "notes.txt", self.work / "paper.pdf"
        notes.write_text("hello notes")
        pdf.write_bytes(b"%PDF-1.4")
        s = self.start_api(key="sekret")
        _, [(_, ok, _, body)] = s.delegate({"worker": "api", "prompt": "summarize", "cwd": str(self.work),
                                            "files": ["notes.txt", "paper.pdf"]})
        self.assertTrue(ok, body)
        self.assertEqual(body, "fine")
        [req] = self.api.seen
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["auth"], "Bearer sekret")
        self.assertEqual(req["body"]["model"], "test-model")
        self.assertEqual(req["body"]["messages"], [{"role": "user", "content": (
            f'summarize\n\n<file path="{notes}">\nhello notes\n</file>\n\n'
            f'<file path="{pdf}">\nPDF TEXT OF {pdf}\n\n</file>')}])

    def test_reply_cleanup_without_key(self):
        s = self.start_api()
        _, results = s.delegate({"worker": "api", "prompt": "think"}, {"worker": "api", "prompt": "null"})
        self.assertEqual([(ok, body) for _, ok, _, body in results], [(True, "answer"), (True, "")])
        self.assertEqual([r["auth"] for r in self.api.seen], [None, None])

    def test_http_error(self):
        s = self.start_api()
        is_error, [(_, ok, _, body)] = s.delegate({"worker": "api", "prompt": "fail"})
        self.assertTrue(is_error)
        self.assertEqual(body, "RuntimeError: HTTP 500: boom")

    def test_eof_does_not_wait_for_a_request_in_flight(self):
        s = self.start_api()
        s.request("tools/call", call_args({"worker": "api", "prompt": "hang"}))
        self.assertTrue(wait_until(lambda: self.api.seen))
        s.p.stdin.close()
        self.assertEqual(s.p.wait(5), 0)


class RepoTest(unittest.TestCase):
    def test_server_stays_small_and_stdlib_only(self):
        src = SERVER.read_text()
        self.assertLess(src.count("\n"), 400)
        if hasattr(sys, "stdlib_module_names"):  # Python 3.10+
            nodes = list(ast.walk(ast.parse(src)))
            mods = {a.name for n in nodes if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module for n in nodes if isinstance(n, ast.ImportFrom)}
            self.assertLessEqual({m.split(".")[0] for m in mods}, set(sys.stdlib_module_names))

    def test_plugin_version_matches_server(self):
        plugin = json.loads((SERVER.parent / ".claude-plugin" / "plugin.json").read_text())
        self.assertIn(f'VERSION = "{plugin["version"]}"', SERVER.read_text())


if __name__ == "__main__":
    unittest.main()
