# Changelog

All notable changes to modelmux are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Plugin slash commands:** `/modelmux:review` (every worker reviews your diff in parallel; Claude merges the findings with agreement counts and verifies each), `/modelmux:race` (every worker attempts the same fix in its own git worktree; your tests pick the winner), `/modelmux:ask` (one question to every worker, compared), `/modelmux:setup` (which workers are ready).
- **`copilot` worker:** GitHub Copilot CLI, read-only through a tool allowlist (`--available-tools`), because `--allow-all-tools` would also auto-approve the user's MCP servers.
- **`isolate: true`** on a task: the worker writes in a private git worktree under `<repo>/.modelmux/` (HEAD plus your uncommitted and untracked files, committed as a base), and the result names a patch with only its changes. In-repo paths in `files` and in the brief are rewritten to the worktree, patches are taken against the base commit (so a worker's own commits count), caches stay out of patches, and the directory is kept out of `git status` through the repository's local exclude file.
- **`background: true`** on `delegate`: returns the result paths at once so the head keeps working; each file appears when its task ends, then a `done` file. Shutdown still stops background workers.
- **`python3 server.py doctor`** shows which workers are ready and the exact step to enable each missing one; `doctor --live` sends each ready worker one tiny prompt and reports broken logins.
- **Read guard** (plugin hook): the first big read of a file in a session (more than 5 pages of a long PDF, or 500+ lines of a 200 KB+ file) is stopped once with advice to delegate; reading it again goes through. `MODELMUX_READ_GUARD=0` turns it off.
- **Claude Code plugin manifest:** `claude plugin install modelmux --marketplace JayPokale/modelmux`, with an icon.
- **Benchmarks** (`bench/`) with raw outputs: head tokens with and without delegation, and review recall and agreement on 23 planted bugs.
- **Other heads:** Codex CLI and Copilot CLI are tested as MCP clients.
- README demo (an animated replay of a real run), translations into Chinese, Japanese, Korean and Spanish, a FAQ, and a social preview image.
- Offline test suite (CI on Ubuntu and macOS, Python 3.9/3.12/3.14), a real-CLI smoke test (`tests/smoke.py [worker ...]`), a slash-command smoke test (`tests/plugin_smoke.sh`), and a tag-driven release workflow.
- Contributor docs, issue and PR templates, and a security policy.

### Fixed

- Read-only Antigravity tasks could end with only a plan stub ("please approve to proceed"); the brief now asks for the complete answer.
- The server now stops running workers when it receives SIGTERM, not only on EOF.
- On EOF the server exits at once, even while an `api` request is in flight.
- A JSON line on stdin that is not an object no longer crashes the server.

## [0.1.0] - unreleased

### Added

- MCP server with a `delegate` tool, 4 workers, parallel execution, cancellation that kills the process group, timeouts, result files and an output cap.
- Read-only and write flags verified against agy 1.4.6, codex-cli 0.163.0 and Claude Code 2.1.323.
- Workers are auto-detected; no personal paths in the code.
- README, LICENSE (MIT) and .gitignore.
