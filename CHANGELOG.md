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
