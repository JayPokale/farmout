# Changelog

All notable changes to farmout are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **A command line:** `farmout` (`pip install .`, or `python3 server.py <command>`), with `--json` and exit codes on every command, and prompts from stdin (`git diff | farmout ask "..."`).
- **Sessions:** `farmout run <worker>` starts a named conversation that the worker really remembers, resumed through each CLI's own session id (`claude --resume`, `codex exec resume`, `copilot --session-id`, `agy --conversation`; farmout keeps `api` history itself). `send` queues messages to a busy session, `--attach` hands over another session's reply, `fork` branches a conversation (natively for Claude, with the transcript for the others), and `ls`, `log`, `attach`, `wait`, `stop` and `rm` manage them. Each session's runner is its own process, so `stop` stops exactly one session and its worker.
- **`farmout review`**: every worker reviews your uncommitted, staged (`--staged`) or branch (`--base`) changes in parallel; findings are merged across models with agreement counts, findings that cite a missing file or line are dropped, and `--min-agree` / `--fail-on` make it a gate.
- **`farmout race`** (every worker in its own worktree; `--test` picks the winner, `--apply` lands it), **`farmout ask`**, and **`diff`** / **`apply`** / **`apply --pr`** for isolated sessions.
- **Pipelines:** stages in `.toml` or `.json` files (`<repo>/.farmout/pipelines/`, `~/.config/farmout/pipelines/`) that start when the previous one ends, with `{stage}` / `{stage.patch}` / `{args}` placeholders, gates (`tests`, `agree >= 2`, `high == 0`, `contains "..."`), `on = failure | always`, `foreach` fan-out, approval steps, background runs, stop and resume. Built in: `fix` (review, fix what 2+ models agree on, review the fix) and `spec` (requirements, design, tasks, parallel build, check).
- **Settings** in `~/.config/farmout/settings` and `<repo>/.farmout/settings`: which workers may see a repository, no writes, no web access, a fallback order, a timeout. Neither file can loosen the other, and settings are always read from the main working tree.
- **Usage limits and stats:** a worker that hits its subscription limit is left alone until it resets, and with a fallback order the task moves to the next worker; `farmout stats` shows each subscription's runs, failures, limits, time and tokens.
- **`farmout hook install`**: a pre-commit (or `--pre-push`) hook that blocks a commit when 2+ models flag a high-severity problem.
- **`.farmoutinclude`**: ignored files (such as `.env`) copied into isolated worktrees, never into patches.
- **MCP tools** `send`, `sessions` and `pipeline`, next to `delegate` (which gains `effort`).
- `--effort` for every worker (`--effort`, `--reasoning-effort`, `model_reasoning_effort`).
- **Plugin slash commands:** `/farmout:review` (every worker reviews your diff in parallel; Claude merges the findings with agreement counts and verifies each), `/farmout:race` (every worker attempts the same fix in its own git worktree; your tests pick the winner), `/farmout:ask` (one question to every worker, compared), `/farmout:setup` (which workers are ready).
- **`copilot` worker:** GitHub Copilot CLI, read-only through a tool allowlist (`--available-tools`), because `--allow-all-tools` would also auto-approve the user's MCP servers.
- **`isolate: true`** on a task: the worker writes in a private git worktree under `<repo>/.farmout/` (HEAD plus your uncommitted and untracked files, committed as a base), and the result names a patch with only its changes. In-repo paths in `files` and in the brief are rewritten to the worktree, patches are taken against the base commit (so a worker's own commits count), caches stay out of patches, and the directory is kept out of `git status` through the repository's local exclude file.
- **`background: true`** on `delegate`: returns the result paths at once so the head keeps working; each file appears when its task ends, then a `done` file. Shutdown still stops background workers.
- **`python3 server.py doctor`** shows which workers are ready and the exact step to enable each missing one; `doctor --live` sends each ready worker one tiny prompt and reports broken logins.
- **Read guard** (plugin hook): the first big read of a file in a session (more than 5 pages of a long PDF, or 500+ lines of a 200 KB+ file) is stopped once with advice to delegate; reading it again goes through. `FARMOUT_READ_GUARD=0` turns it off.
- **Claude Code plugin manifest:** `claude plugin install farmout --marketplace JayPokale/farmout`, with an icon.
- **Benchmarks** (`bench/`) with raw outputs: head tokens with and without delegation, and review recall and agreement on 23 planted bugs.
- **Other heads:** Codex CLI and Copilot CLI are tested as MCP clients.
- README demo (an animated replay of a real run), translations into Chinese, Japanese, Korean and Spanish, a FAQ, and a social preview image.
- Offline test suite (CI on Ubuntu and macOS, Python 3.9/3.12/3.14), a real-CLI smoke test (`tests/smoke.py [worker ...]`), a slash-command smoke test (`tests/plugin_smoke.sh`), and a tag-driven release workflow.
- Contributor docs, issue and PR templates, and a security policy.

### Changed

- **farmout is a package now** (`farmout/`, standard library only); `server.py` stays the entry point for the plugin and MCP clients. Isolated worktrees moved to `<repo>/.farmout/worktrees/`, so `.farmout/settings` and `.farmout/pipelines` can be committed.
- **Renamed from modelmux to farmout**, because modelmux was taken on PyPI, npm, crates.io and Homebrew. The plugin, slash commands (`/farmout:review` and so on), MCP server, environment variables (`FARMOUT_*`), worktree folder (`.farmout/`) and branch prefix (`farmout/`) all use the new name.

### Fixed

- The `claude` worker could never search the web: headless Claude Code denies any tool that would ask for permission, so WebSearch is now pre-approved (`--allowedTools`).
- Isolated tasks corrupted files with CRLF line endings: diffs were read as text, which turned CRLF into LF; patches now move as bytes.
- farmout's own git hook ran a full review on every worktree farmout made; internal commits now skip hooks.
- A worker name in `delegate` could become a path outside the results directory.
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
