# Contributing to farmout

## Ground rules

- Standard library only. No third-party imports, at runtime or in tests.
- Read-only is the default for every worker. Writing needs an explicit `--write`, `--isolate` or `write: true`.
- In MCP mode, stdout carries protocol messages only.
- farmout drives other AI CLIs; it never becomes an agent itself (no model loop or tools of its own).
- Respect the non-goals:
  - an API proxy or gateway
  - model hosting
  - token extraction or web-app scraping
  - a GUI
  - a general agent framework
  - Windows support before v1.0

## Layout

| file | what lives there |
|---|---|
| `server.py` | the entry point for the Claude Code plugin and MCP clients; runs the CLI (`mcp` with no arguments) |
| `farmout/workers.py` | one function per worker CLI: flags, session resume, output parsing; `WORKERS`, `SETUP` |
| `farmout/proc.py` | worker processes in their own process groups: timeouts, cancels, stops |
| `farmout/engine.py` | one task: settings checks, isolation, the worker call, usage-limit fallback, the usage log |
| `farmout/isolate.py` | git worktrees, `.farmoutinclude`, patches |
| `farmout/sessions.py` | sessions: inbox, runner processes, turns, fork, stop |
| `farmout/flows.py` | fan-out, ask, race, and diff/apply/pull request |
| `farmout/review.py` | the review brief, finding parser, cross-model merge, citation check |
| `farmout/pipeline.py` | pipeline files, stages, gates, approvals, runs, resume |
| `farmout/hooks.py` | farmout's own hooks, redaction |
| `farmout/skills.py` | `SKILL.md` parsing, discovery, running, `skill add` |
| `farmout/store.py` | the store index, hash-checked installs, tools |
| `farmout/bench.py` | `farmout bench` |
| `farmout/config.py`, `usage.py` | paths, settings and trust; the usage log and limits |
| `store/` | the store's entries and `index.json` (rebuilt by `scripts/build_store_index.py`) |
| `farmout/mcp.py`, `cli.py` | the two front ends |

## Setup

Python 3.9+. Nothing to install: `python3 server.py <command>` runs the CLI from a checkout. To have a `farmout` command, `pip install -e .`.

To try the plugin (slash commands, read guard) from a checkout for one session:

```bash
claude --plugin-dir /path/to/farmout
```

## Tests

Offline suite:

```bash
python3 -m unittest discover -s tests -v
```

It runs real CLI commands and MCP stdio sessions against fake worker CLIs (`tests/fake_cli.py`). No network, no logins, no real CLIs. CI runs it on Ubuntu and macOS with Python 3.9, 3.12 and 3.14.

Release smoke test:

```bash
python3 tests/smoke.py
```

It drives the real CLIs on your own logins and spends a little quota. Export the same `FARMOUT_*` variables as your setup first. For every enabled worker it checks that the worker can read a file it is given, cannot write without write access, can write with it, and remembers an earlier turn of a session. Run it before every release.

## Adding a worker

- [ ] One function in `farmout/workers.py` and one `WORKERS` entry (detection, whether its web access can be turned off, routing hint), plus a `SETUP` hint.
- [ ] Start processes only through `proc.run()`.
- [ ] Session support: create or report a session id, and resume by id (`resume`), or say why not.
- [ ] A README Safety row with the exact read-only and write flags, and the tested CLI version.
- [ ] Smoke-test output proving read-only mode cannot write.
- [ ] Offline tests with the fake CLI for the read-only and write flags and for resume.

## Store entries

See [store/README.md](store/README.md): one folder per entry with an `entry.json`, standard library only, then `python3 scripts/build_store_index.py`. Every entry is read line by line before it's merged, and its `version` goes up on every change.

## Pull requests

- Keep them small and focused.
- Offline tests pass.
- Add a CHANGELOG entry under Unreleased.
- For worker changes, state the OS and CLI versions tested.

## Releasing

1. Run `python3 tests/smoke.py` (workers) and `sh tests/plugin_smoke.sh` (slash commands run headless without permission errors); if CLI versions changed, update "Tested with" in README.md.
2. Bump the version in `farmout/__init__.py`, `pyproject.toml` and `.claude-plugin/plugin.json`. The offline suite fails if they differ.
3. Move the Unreleased notes in CHANGELOG.md under `## [X.Y.Z] - YYYY-MM-DD`.
4. Commit, then `git tag vX.Y.Z && git push --follow-tags`. The release workflow re-runs the tests, checks the tag against `farmout/__init__.py` and publishes a GitHub Release with that version's CHANGELOG notes.

Security reports go through [SECURITY.md](SECURITY.md), not public issues.
