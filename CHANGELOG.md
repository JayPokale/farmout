# Changelog

All notable changes to modelmux are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Offline test suite.
- CI on Ubuntu and macOS.
- Release smoke test.
- Contributor docs, issue and PR templates, and a security policy.
- Claude Code plugin manifest: `claude plugin install modelmux --marketplace JayPokale/modelmux`.
- Release workflow: a `v*` tag publishes a GitHub Release with that version's CHANGELOG notes.
- `copilot` worker: GitHub Copilot CLI, read-only through a tool allowlist (`--available-tools`), because `--allow-all-tools` would also auto-approve the user's MCP servers.
- `python3 server.py doctor`: which workers are ready, and the exact step to enable each missing one.
- Plugin slash commands: `/modelmux:review` (parallel multi-model review of the diff, merged with agreement counts), `/modelmux:ask` (one question to every worker, compared), `/modelmux:setup` (runs `doctor`).
- `tests/smoke.py [worker ...]` checks only the named workers.
- `isolate: true` on a task: the worker writes in a private git worktree (HEAD plus your uncommitted changes, committed as a base) and the result names a patch with only its changes. `/modelmux:race` uses it to have every worker attempt the same fix in parallel.
- `background: true` on `delegate`: returns the result paths at once so the head keeps working; each file appears when its task ends, then a `done` file. Shutdown still stops background workers.

### Fixed

- The server now stops running workers when it receives SIGTERM, not only on EOF.
- On EOF the server exits at once, even while an `api` request is in flight.
- A JSON line on stdin that is not an object no longer crashes the server.

## [0.1.0] - unreleased

### Added

- MCP server with a `delegate` tool, 4 workers, parallel execution, cancellation that kills the process group, timeouts, result files and an output cap.
- Read-only and write flags verified against agy 1.4.6, codex-cli 0.163.0 and Claude Code 2.1.323.
- Workers are auto-detected; no personal paths in the code.
- README, LICENSE (MIT) and .gitignore.
