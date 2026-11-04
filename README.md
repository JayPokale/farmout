# modelmux

**Let Claude Code hand its grunt work to your other AI subscriptions: in parallel, read-only by default, from one dependency-free Python file.**

[![CI](https://github.com/JayPokale/modelmux/actions/workflows/ci.yml/badge.svg)](https://github.com/JayPokale/modelmux/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio-6e56cf)
![license](https://img.shields.io/badge/license-MIT-blue)

You pay for Claude. Maybe also ChatGPT, Google AI, a second Claude seat, or you run an open model on a GPU. Yet your best model's quota still goes on reading PDFs and writing boilerplate while the others sit idle.

modelmux makes Claude Code the **head**. It plans, splits a job into self-contained subtasks, sends each one to a model that is good enough for it, and keeps its own quota for the hard parts:

- a 60-page paper goes to **Antigravity** (Gemini), which reads it and returns 150 words
- "write tests for `parse_date()`" goes to **Codex**, which writes and runs them
- mid-complexity work goes to your **second Claude account**
- classifying 2,000 lines goes to **any OpenAI-compatible model** (Ollama, vLLM, OpenRouter, Qwen, ...)

These all run at once, from a single tool call. Each worker is the vendor's own CLI, logged in with your own subscription, so the CLI workers need no API keys.

## How it works

```mermaid
flowchart LR
    you([You]) --> head["Claude Code (head)<br/>plans · routes · integrates"]
    head -- "delegate(tasks)" --> mux{{modelmux}}
    mux --> ag["antigravity<br/>agy · Google account"]
    mux --> cx["codex<br/>Codex CLI · ChatGPT account"]
    mux --> cl["claude<br/>second Claude account"]
    mux --> oa["api<br/>any OpenAI-compatible model"]
    ag & cx & cl & oa --> disk[("~/.cache/modelmux<br/>full outputs")]
    disk -- "capped results + file paths" --> head
```

- **The head is the router.** modelmux ships its routing policy as MCP server instructions, which stay in Claude Code's system prompt every session, even when the tool itself is deferred behind tool search. No extra router model, no classifier to train.
- **Workers see only their brief.** Each subtask is a standalone prompt plus file paths, never your conversation.
- **Results land on disk.** The head gets a capped copy plus the file path. To chain workers, pass that path to the next task; the text never has to pass through the head's context.

## Features

- **Parallel fan-out:** one `delegate` call runs up to 8 subtasks concurrently, across different workers.
- **Subscription-first:** drives official CLIs in their headless modes with your existing logins.
- **Read-only by default:** every worker runs in its CLI's no-write mode unless the head sets `write: true` (see [Safety](#safety)).
- **Quota-safe:** cancelling in Claude Code (Esc) kills the worker's whole process tree. Each task also has a timeout.
- **Context-cheap:** at most ~60k characters come back per call; full outputs stay on disk.
- **Auto-detected workers:** only the workers you actually have are offered to the head.
- **Zero dependencies:** about 280 lines of standard-library Python. Read it before you trust it with your accounts.

## Quick start

Requirements: Python 3.9+, Linux (tested) or macOS (untested), [Claude Code](https://github.com/anthropics/claude-code), and at least one worker:

| worker | needs | good for |
|---|---|---|
| `antigravity` | [Antigravity CLI](https://antigravity.google) (`agy`), logged in | long PDFs, papers, many-file reading, web research; Gemini, plus other models listed by `agy models` |
| `codex` | [Codex CLI](https://github.com/openai/codex), logged in | well-specified coding, tests, refactors, reviews |
| `claude` | a second Claude Code account + `MODELMUX_CLAUDE_DIR` | general work on separate quota |
| `api` | `MODELMUX_API_BASE` + `MODELMUX_API_MODEL` | bulk or simple work on any OpenAI-compatible model |

```bash
git clone https://github.com/JayPokale/modelmux ~/.local/share/modelmux
claude mcp add -s user modelmux -- python3 ~/.local/share/modelmux/server.py
claude mcp list        # modelmux: ... ✔ Connected
```

Or install it as a Claude Code plugin, which `claude plugin update modelmux` keeps current:

```bash
claude plugin install modelmux --marketplace JayPokale/modelmux
```

A plugin's server reads its settings from your environment, so with the plugin, `export` the `MODELMUX_*` variables below in your shell profile instead of passing `-e`. Use one install method, not both.

Restart Claude Code. Workers are detected at startup.

**Second Claude account:**

```bash
CLAUDE_CONFIG_DIR=~/.claude-2 claude    # log in once with the other account, then exit
claude mcp remove -s user modelmux
claude mcp add -s user modelmux -e MODELMUX_CLAUDE_DIR=$HOME/.claude-2 \
  -- python3 ~/.local/share/modelmux/server.py
```

Keep `-e` after the server name: it accepts several values and would otherwise swallow the name.

**OpenAI-compatible endpoint.** The server inherits Claude Code's environment, so put these in your shell profile. `claude mcp add -e` would store the key in plain text in `~/.claude.json`.

```bash
export MODELMUX_API_BASE=http://localhost:11434/v1   # e.g. Ollama
export MODELMUX_API_MODEL=qwen3:32b
export MODELMUX_API_KEY=...                          # only if the endpoint needs one
```

The `api` worker has no tools, so modelmux pastes file text into the prompt. PDFs need `pdftotext` (poppler-utils).

## Usage

Work as usual. The head delegates when it pays off, and you can nudge it:

> Read the six papers in `./papers` and draft a related-work section. Delegate the reading.

> Have codex add tests for `src/dates.py` while you review the API design.

> Translate `locales/en.json` into Hindi, Spanish and German via the api worker, one task per language.

### The `delegate` tool

```json
{
  "tasks": [
    {"worker": "antigravity", "prompt": "Extract method, datasets and headline metric. Max 120 words.",
     "files": ["papers/attention.pdf"]},
    {"worker": "codex", "prompt": "Add unit tests for parse_date() in src/dates.py covering time zones. Run them.",
     "cwd": "/home/me/project", "write": true},
    {"worker": "api", "prompt": "Label each line bug / feature / question. Output CSV.",
     "files": ["issues.txt"]}
  ]
}
```

| field | required | meaning |
|---|---|---|
| `worker` | yes | one of the detected workers |
| `prompt` | yes | standalone brief: goal, context, constraints, exact output format |
| `files` | no | paths the worker should read: PDFs, code, earlier result files |
| `cwd` | no | working directory; defaults to where Claude Code was launched |
| `write` | no | allow edits inside `cwd` (default `false`) |
| `model` | no | override the worker's default model |

Each task comes back under a header naming its full-output file:

```text
## [1] antigravity ok 24s -> ~/.cache/modelmux/20261117-002241-eb1r2fv1/1-antigravity.md
...
## [2] codex ok 11s -> ~/.cache/modelmux/20261117-002241-eb1r2fv1/2-codex.md
...
## [3] api FAILED 0s -> ~/.cache/modelmux/20261117-002241-eb1r2fv1/3-api.md
FileNotFoundError: /home/me/project/issues.txt
```

## Safety

| worker | read-only (default) | `write: true` |
|---|---|---|
| `antigravity` | `agy -p … --mode plan` | `--mode accept-edits --sandbox` |
| `codex` | `codex exec -s read-only` | `-s workspace-write` |
| `claude` | `claude -p --tools Read,Grep,Glob,WebSearch` | `--permission-mode acceptEdits` |
| `api` | no tools | n/a |

- **Why plan mode for `agy`:** with `toolPermission: always-proceed` in agy's settings, `agy -p` runs shell commands without asking. In testing, plan mode still answered questions about files but executed nothing, even with that setting on.
- **Prompt injection is real.** Workers read whatever files you hand them, and a hostile document can try to instruct them. Read-only mode limits the damage but cannot make injection impossible; for example, a worker could put file contents into a web search. Don't delegate secrets.
- **Data leaves your head account.** Briefs and files go to each worker's provider. If your head runs on a work account, check your organization's policy before delegating its data to personal accounts.
- **Never give two parallel tasks write access to overlapping files.**

## Terms of service

modelmux runs each vendor's official CLI in its documented non-interactive mode, logged in as you. It does not extract tokens, scrape web apps or proxy APIs. You are still responsible for following each provider's terms. Don't use it to share accounts or resell access.

## Field notes

What we found building this, in case it saves you an afternoon:

- **Gemini CLI with a personal Google login now fails** with `IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products`. That's why the Google worker drives Antigravity's `agy`, whose print mode works with the same login. On our account, `agy models` also listed Claude and GPT-OSS models.
- **`agy -p` executes tools without asking under `toolPermission: always-proceed`.** Use `--mode plan` for anything meant to be read-only.
- **A nested `claude -p` inherits the parent session's variables** (`CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_EFFORT`, ...). modelmux strips them so every worker is a clean session on the other account.
- **`claude --bare` never reads OAuth credentials**, so it can't use a subscription login. Workers must not use it.
- **Claude Code may defer MCP tools**, which hides their descriptions until loaded. Routing policy therefore belongs in server instructions, not tool descriptions.

Tested with agy 1.4.6, codex-cli 0.163.0, Claude Code 2.1.323 and Python 3.14 on Arch Linux (November 2026). CLIs change often, so if a worker breaks, please open an issue with your CLI version.

## Configuration

| variable | default | effect |
|---|---|---|
| `MODELMUX_CLAUDE_DIR` | unset | config dir of a second Claude Code account; enables `claude` |
| `MODELMUX_API_BASE` | unset | OpenAI-compatible base URL ending in `/v1`; enables `api` together with `MODELMUX_API_MODEL` |
| `MODELMUX_API_MODEL` | unset | model name sent to that endpoint |
| `MODELMUX_API_KEY` | unset | bearer token for that endpoint |
| `MODELMUX_TIMEOUT` | `1200` | seconds per task |

Full outputs are written to `~/.cache/modelmux/` and are safe to delete at any time. The `api` worker honours `http_proxy`/`no_proxy`.

## Alternatives

Good tools exist in this space; choose by what you need:

| project | choose it when |
|---|---|
| [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server) (formerly Zen) | you want many workflow tools (consensus, code review, planning) and API-key models, plus its `clink` CLI bridge |
| [llm-cli-gateway](https://github.com/verivus-oss/llm-cli-gateway) | you need sessions, retries and async jobs across Claude, Codex, Gemini, Grok and Mistral CLIs |
| [ai-cli-mcp](https://github.com/mkXultra/ai-cli-mcp) | you want CLI agents running in the background from any MCP client |
| [claude-code-router](https://github.com/musistudio/claude-code-router) | you want to swap the model behind Claude Code rather than delegate to other CLIs |
| **modelmux** | you want something small enough to audit, read-only by default, with parallel fan-out in one call and Antigravity support |

## Roadmap

- `doctor` command: check that each worker is installed, logged in and headless-ready
- PyPI package for `uvx` installs
- Gemini CLI worker for API-key users
- Usage log and per-worker stats
- Rate-limit-aware fallback between workers
- Git-worktree isolation for parallel write tasks

## Contributing

Issues and PRs are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Tests: `python3 -m unittest discover -s tests -v`. Keep it one file and standard-library only. A new worker is one function plus one `WORKERS` entry. Any change to a worker should state the CLI version you tested and show that its read-only mode really can't write. Report security issues privately: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

If modelmux saves you quota, [star the repo](https://github.com/JayPokale/modelmux/stargazers): it costs none.
