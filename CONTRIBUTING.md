# Contributing to modelmux

## Ground rules

- One file: `server.py`.
- Standard library only. No third-party imports.
- `server.py` stays under 400 lines.
- stdout carries MCP protocol messages only.
- Read-only is the default for every worker. Writing needs an explicit `write: true`.
- Respect the non-goals:
  - an API proxy or gateway
  - model hosting
  - token extraction or web-app scraping
  - a GUI
  - a general agent framework
  - Windows support before v1.0

## Setup

Python 3.9+. Nothing to install. Register a local checkout:

```bash
claude mcp add -s user modelmux -- python3 /path/to/modelmux/server.py
```

## Tests

Offline suite:

```bash
python3 -m unittest discover -s tests -v
```

It runs real MCP stdio sessions against fake worker CLIs (`tests/fake_cli.py`). No network, no logins, no real CLIs. CI runs it on Ubuntu and macOS with Python 3.9, 3.12 and 3.14.

Release smoke test:

```bash
python3 tests/smoke.py
```

It drives the real CLIs on your own logins and spends a little quota. Export the same `MODELMUX_*` variables as your MCP registration first. For every enabled worker it checks that the worker can read a file it is given, cannot write without `write: true`, and can write with it. Run it before every release.

## Adding a worker

- [ ] One function and one `WORKERS` entry (detection plus routing hint).
- [ ] Start processes only through `run()`.
- [ ] A README Safety row with the exact read-only and write flags.
- [ ] The tested CLI version.
- [ ] Smoke-test output proving read-only mode cannot write.
- [ ] An offline test with a fake CLI asserting the read-only and write flags.

## Pull requests

- Keep them small and focused.
- Offline tests pass.
- Add a CHANGELOG entry under Unreleased.
- For worker changes, state the OS and CLI versions tested.

## Releasing

1. Run `python3 tests/smoke.py`; if CLI versions changed, update "Tested with" in README.md.
2. Bump `VERSION` in `server.py` and `version` in `.claude-plugin/plugin.json`. The offline suite fails if they differ.
3. Move the Unreleased notes in CHANGELOG.md under `## [X.Y.Z] - YYYY-MM-DD`.
4. Commit, then `git tag vX.Y.Z && git push --follow-tags`. The release workflow re-runs the tests, checks the tag against `server.py` and publishes a GitHub Release with that version's CHANGELOG notes.

Security reports go through [SECURITY.md](SECURITY.md), not public issues.
