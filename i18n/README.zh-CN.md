# farmout

[English](../README.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

**让你付费的每个 AI 订阅都动起来：Codex、Gemini、GitHub Copilot、DeepSeek、第二个 Claude 账号、本地模型。会记住上下文的会话、并行代码审查、由你的测试裁定胜负的竞速，还有流水线，在终端里或在 Claude Code 内部都能用。默认只读，零依赖。**

[![CI](https://github.com/JayPokale/farmout/actions/workflows/ci.yml/badge.svg)](https://github.com/JayPokale/farmout/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio-6e56cf)
![license](https://img.shields.io/badge/license-MIT-blue)

> **公开预览版**：欢迎试用，遇到问题请到 [Discussions](https://github.com/JayPokale/farmout/discussions) 告诉我们；也可以给仓库点个 Star 或 Watch，0.1.0 发布时就能收到通知。

```bash
pipx install git+https://github.com/JayPokale/farmout
cd your-repo && farmout bench --share   # 哪个 AI 订阅最能在你自己的代码里找出真实的 bug？
```

<p align="center"><img src="../assets/review-demo.svg" width="900" alt="真实运行的终端动画回放：一次审查把同一处未提交的改动并行发给 antigravity、codex、claude 和 copilot，随后 Claude 输出一张合并并核实过的表格，列出 9 条发现以及每条被几个模型指出"></p>

- **`farmout bench`**：在你自己的代码上，哪个 AI 最强？farmout 会在临时 worktree 里撤销你 git 历史中最近的 bug 修复，让你的每个订阅去把 bug 找出来；加上 `--share`，还能把得分做成一篇帖子和一张图片卡片（只有分数，绝不包含你的代码）。
- **`farmout review`**：Codex、Gemini（通过 Antigravity）、GitHub Copilot 和第二个 Claude 账号同时以只读方式审查你的改动。各模型的发现会合并在一起，并标出有几个模型意见一致；凡是引用了不存在的文件或行号的发现，在你看到之前就会被剔除。在[我们的基准测试](../bench/README.md)中，32 个 bug（23 个人为植入，9 个是来自开源项目的真实回归）每一个都被四个模型中的至少三个指出。
- **`farmout race "<task>" --test "pytest -x"`**：每个模型都在各自的 git worktree 里尝试同一处改动，由你的测试选出胜者；在你执行 `farmout apply` 之前，你的工作区不会有任何改动。
- **`farmout run` / `send` / `fork`**：和任意 worker 开启命名会话。会话会记住对话内容，忙碌时把新消息排进队列，还能把一个会话的回复转交给另一个会话。
- **`farmout pipe run fix`**：先审查你的改动，再在 worktree 里修复两个及以上模型一致指出的问题，然后审查这次修复。你也可以自己编写流水线，支持门禁（gate）、审批和并行阶段。
- **技能、钩子和商店**：在任意 worker 上运行 `SKILL.md` 技能（Claude Code、Codex 和 Copilot 通用的格式），挂接 farmout 的各类事件，还能安装经过审核的钩子、技能和工具，每个文件都用 SHA-256 锁定。
- **在 Claude Code 里**：Claude 会把长篇阅读和样板代码交给最合适的订阅。用这种方式总结三篇论文，答案完全相同，Claude 却少用了 **59% 的 token**（成本降低 74%）。

你为 Claude 付了费，可能还订阅了 ChatGPT、Google AI、GitHub Copilot、第二个 Claude 席位，或者在 GPU 上跑着开源模型。它们各自待在自己的终端里，一天中的大部分时间都闲着。farmout 以你的身份驱动这些服务的官方 CLI，让它们并行干活、互相检查，在同一项工作上轮流接力。不需要 API Key，没有额外账单，也没有任何依赖：纯 Python 标准库实现，在把账号托付给它之前，你可以先把代码通读一遍。

## 安装

运行要求：Python 3.9+、Linux（已测试）或 macOS（未测试）、git，以及至少一个 worker：

| worker | 需要 | 适合 |
|---|---|---|
| `antigravity` | 已登录的 [Antigravity CLI](https://antigravity.google)（`agy`） | 长篇 PDF、论文、大量文件阅读、网络调研；Gemini，以及 `agy models` 列出的其他模型 |
| `codex` | 已登录的 [Codex CLI](https://github.com/openai/codex) | 需求明确的编码、测试、重构、审查 |
| `claude` | 第二个 Claude Code 账号 + `FARMOUT_CLAUDE_DIR` | 用另一份额度处理常规工作 |
| `copilot` | 已登录的 [GitHub Copilot CLI](https://github.com/github/copilot-cli) | 代码审查、有针对性的编码问题、第二意见；可用多家厂商的模型 |
| `deepseek` | [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`npm i -g @deepseek-ai/dsh`）和 `DEEPSEEK_API_KEY` | 用 DeepSeek V4 做批量阅读、摘要、常规编码，或多听一份意见；按 token 计价很便宜（实验性） |
| `api` | `FARMOUT_API_BASE` + `FARMOUT_API_MODEL` | 在任意兼容 OpenAI 的模型上处理批量或简单的工作（DeepSeek 的 API、Ollama、vLLM、OpenRouter） |

安装命令行工具（PyPI 版本即将发布，届时可直接用 `uvx farmout`）：

```bash
git clone https://github.com/JayPokale/farmout ~/.local/share/farmout
pipx install ~/.local/share/farmout      # or: pip install --user ~/.local/share/farmout
farmout doctor --live                    # which workers are ready; --live sends each one a tiny prompt
```

完全不安装也行：`python3 ~/.local/share/farmout/server.py <command>` 就是同一个 CLI。

作为 Claude Code 插件安装（会加入斜杠命令、MCP 工具和读取守卫；用 `claude plugin update farmout` 保持最新）：

```bash
claude plugin install farmout --marketplace JayPokale/farmout
```

**第二个 Claude 账号**：先用 `CLAUDE_CONFIG_DIR=~/.claude-2 claude` 登录一次，然后在 shell 配置文件里加上 `export FARMOUT_CLAUDE_DIR=~/.claude-2`。

**OpenAI 兼容接口**：`export FARMOUT_API_BASE=http://localhost:11434/v1 FARMOUT_API_MODEL=qwen3:32b`（需要的话再加上 `FARMOUT_API_KEY`）；如果直接用 DeepSeek 官方的 API，就设置 `FARMOUT_API_BASE=https://api.deepseek.com FARMOUT_API_MODEL=deepseek-flash` 并提供你的密钥。`api` worker 没有任何工具，所以文件内容会直接放进 prompt；处理 PDF 需要 `pdftotext`。

## 功能速览

```bash
# One worker, one conversation. The session remembers; send more later.
farmout run codex "Add unit tests for parse_date() in src/dates.py and run them" --isolate -n dates
farmout send dates "Also cover leap years"
farmout diff dates            # the patch, from its own git worktree
farmout apply dates           # into your tree (or --pr: branch, push, open a pull request)

# Every model at once
farmout review                                    # your uncommitted change
farmout review --base main --min-agree 2          # the branch since main, only what 2+ models agree on
farmout ask "Is this migration safe to run online?" -f migrations/0042.sql
git diff | farmout ask "Anything here that could lose data?"
farmout race "Make the flaky test in tests/test_sync.py deterministic" --test "pytest tests/test_sync.py -x"

# Sessions talk to each other
farmout send dates --attach review-1-claude "Fix what this review found"
farmout fork review-1-codex "Why do you think line 42 races?"     # a side question, same context

# Pipelines: each stage starts when the previous one ends
farmout pipe run fix --set test="pytest -x"      # review → fix what 2+ models agree on → check the fix
farmout pipe run spec "Add CSV export to the reports page"
farmout pipe ls --runs

# Models argue, your history judges
farmout ask --debate "Should this cache be per-request or global?"   # each reads the others, then answers again
farmout bench -n 3                                                    # replay 3 of your past bug fixes as a test
farmout fix-pr 128 --test "pytest -x"            # PR review comments and failing checks become a race

# Skills, hooks, the store
farmout store ls && farmout store add security-review && farmout security-review
farmout skill add https://github.com/you/skills --path skills/changelog
farmout store add notify      # a desktop or phone notification when a long turn or a pipeline ends

# Housekeeping
farmout ls                    # sessions: worker, state, turns
farmout attach dates          # watch a running session (Ctrl-C detaches; it keeps going)
farmout stats                 # what each subscription did for you this week
farmout status --line         # "farmout: 2 running · fix-3 waiting · codex limited till 15:00", for tmux or a prompt
farmout ls --watch            # a live dashboard
farmout export dates --html -o dates.html   # share a conversation
farmout hook install          # review every commit; block it when 2+ models flag a high-severity bug
```

## 命令

| 命令 | 作用 |
|---|---|
| `run <worker> "<prompt>"` | 启动一个会话：`-n name`、`--write`（直接修改当前目录）、`--isolate`（在私有 worktree 中修改并生成补丁）、`--model`、`--effort`、`--fallback w1,w2`、`--bg`、`-f file`。worker 填 `auto` 时，会选第一个允许使用且未触及用量上限的 worker |
| `send <session> "<msg>"` | 发送下一条消息；会话忙碌时消息会排队。`--attach <session>` 把那个会话的最后一条回复一并交过去，`-f` 附加文件，`--bg` 立即返回 |
| `fork <session> "<msg>"` | 以该会话的对话为起点开一个新会话；`--worker` 换一个模型接着聊 |
| `ask "<question>"` | 把同一个问题发给每个 worker（用 `-w` 挑选），答案并排展示；`--debate` 让每个 worker 读完其他人的回答后再答一次 |
| `review [focus]` | 每个 worker 审查 `--staged`、`--base <ref>` 或你未提交的改动；`--min-agree N`，以及决定退出码的 `--fail-on high\|medium\|low` |
| `race "<task>"` | 每个 worker 在各自的 worktree 中尝试这个任务；`--test <cmd>` 选出胜者，`--apply` 直接应用 |
| `fix-pr <number>` | 把一个 pull request 的审查评论和失败的检查（通过 `gh` 获取）变成一场竞速；请先把该 PR 检出到本地 |
| `bench [-n N]` | 重放你历史中最近 N 次小型 bug 修复，按能否找出 bug 给每个 worker 打分 |
| `ls [--watch]`, `log`, `attach`, `wait`, `stop`, `rm` | 列出（或以仪表盘形式实时查看）、阅读、观看、等待、停止和删除会话（`rm` 还会删除会话的 worktree 和分支） |
| `diff`, `apply [--pr]`, `undo` | 处理隔离会话的补丁：查看、应用（可按顺序应用多个，遇到第一个应用不上的就停下）、用 `gh` 开 pull request，或把已应用的补丁撤回 |
| `export <session> [--html]` | 把一段对话导出为 Markdown 或独立的 HTML 页面 |
| `status [--line]` | 查看哪些任务在运行、在排队、在等待审批或已触及用量上限 |
| `skill ls\|show\|run\|add\|rm`, `<skill>` | 管理技能；`farmout <skill> [args]` 运行其中一个 |
| `store ls\|show\|add\|rm\|update`, `tool ls\|run` | 商店，以及你从商店安装的工具 |
| `trust [--revoke\|--list]`, `hooks` | 允许运行项目自带的钩子；列出当前项目中生效的钩子 |
| `pipe run\|ls\|status\|resume\|approve\|stop` | 流水线及其运行记录 |
| `stats` | 每个订阅的运行次数、失败次数、用量上限、耗时和 token 数 |
| `hook install\|uninstall [--pre-push]` | 安装（或卸载）一个审查每次提交或推送的 git 钩子 |
| `doctor [--live]`, `mcp` | 检查各个 worker；通过 stdio 提供 MCP 工具 |

所有命令都支持 `--json`（方便脚本使用）和 `-C <dir>`。prompt 也可以从 stdin 传入；如果命令里已经给了 prompt，通过管道传入的 stdin 会作为文件一起附上。退出码：0 表示成功，1 表示失败（或触发了审查门禁），130 表示被停止。

## 会话

会话（session）就是与某个 worker 的一段命名对话，保存在 `~/.local/state/farmout/sessions/<name>/`。farmout 借助 worker CLI 自己的会话 ID 来恢复会话（`claude --resume`、`codex exec resume`、`copilot --session-id`、`agy --conversation`、`dsh --session-id`；`api` 则由 farmout 自己保存历史），所以模型是真的记得之前的轮次，而不是重读一份摘要。

消息会先进入收件箱。会话的 runner 是一个持有该会话锁的后台进程，按顺序处理这些消息。正因如此，向忙碌的会话 `send` 消息会排队而不是失败，`farmout attach` 在任何终端里都能用，`farmout stop` 也只会停掉这一个会话的 worker。fork Claude 会话时用的是 Claude 原生的 fork；其他 worker 则会以文件形式拿到对话记录。review、ask、race 和流水线的各个阶段也都会创建会话，所以你可以继续追问任何一位审查者：`farmout send review-3-codex "Show me the input that breaks line 42"`。

## 流水线

流水线（pipeline）就是一组阶段，写在 `<repo>/.farmout/pipelines/` 或 `~/.config/farmout/pipelines/` 下的 `.toml`（需要 Python 3.11+）或 `.json` 文件里。每个阶段要么运行一个技能（`review`、`ask`、`race`），要么把自己的 prompt 发给一个或多个 worker；上一个阶段结束后，下一个阶段才开始。

```toml
description = "Plan with Claude, build with Codex in a worktree, ship after I approve"

[[stage]]
name = "plan"
worker = "claude"
prompt = "Write a short plan for: {args}"

[[stage]]
name = "build"
worker = "codex"
isolate = true
prompt = "Implement the plan in {plan}. Run the tests."
test = "pytest -x"
gate = "tests"            # passes only if the test command passes in the worktree

[[stage]]
name = "review"
skill = "review"
in = "build"              # review the build's worktree
gate = "high == 0"

[[stage]]
name = "notes"
worker = "copilot"
approve = true            # waits for `farmout pipe approve <run>`
prompt = "Write release notes for the patch in {build.patch}"
```

- **占位符**：`{stage}` 是该阶段的输出文件，`{stage.patch}` 是它的补丁，`{args}` 是 `farmout pipe run <name>` 后面的自由文本，`{key}` 是一个参数（`params` 表或 `--set key=value`），`{item}` 是 `foreach` 中的一项。阶段的输出文件会交给 worker，所以 worker 能读取它们。
- **门禁**：`ok`、`tests`、`agree >= N`、`findings == 0`、`high == 0`（`<`、`<=`、`==`、`!=`、`>=`、`>` 均可），或 `contains "TEXT"`。门禁不通过时，它之后的阶段都不会再运行。
- **`on`**：`success`（默认）、`failure`（只在前面有步骤失败后才运行，比如清理或发通知），或 `always`。
- **`foreach = "<stage>"`**：为该阶段输出中的每一行 `- item` 各开一个会话，分配到 `workers` 上并行运行（`max_items`，默认 8）。
- **`approve = true`**：运行到该阶段之前会暂停，直到执行 `farmout pipe approve <run>`（或 `--reject`）。
- **运行记录**保存在 `~/.local/state/farmout/runs/<run>/`。失败、被门禁拦下、被停止或被中断的运行，都可以用 `farmout pipe resume <run>` 继续；`--bg` 让它在后台运行。

内置流水线：**`fix`**（审查 → 修复 2 个及以上模型一致指出的问题，`--set worker=codex test="pytest -x"` → 审查这次修复）和 **`spec`**（需求 → 设计 → 拆成相互独立的任务 → 每个任务在各自的 worktree 中并行实现 → 把所有补丁放在一起检查）。流水线里指定的 worker 如果你的机器上没有，会自动换成一个可用的。

## 技能

技能（skill）就是一个包含 `SKILL.md` 的文件夹：YAML frontmatter（`name`、`description`）加上指令，这是 Claude Code、Codex 和 Copilot 通用的格式。farmout 会在你的 worker 上运行它：`farmout <skill> [args]`（或 `farmout skill run <skill>`）。指令中的 `$ARGUMENTS` 会被替换为传入的参数，技能所在的文件夹也会一并交给 worker，方便它们读取其中引用的资料。

```markdown
---
name: security-review
description: Every worker reviews your change for security problems only.
mode: review          # ask (default) | review (farmout review, with these instructions as the focus) | race
workers: [codex, claude, copilot]   # default: one worker for ask, all of them for review and race
diff: true            # hand the workers your uncommitted change as a diff file
---
Look only for security problems that an attacker could trigger: ...
$ARGUMENTS
```

farmout 还会读取 `write`、`isolate`、`test`（race 模式）、`model` 和 `effort`；其他 agent 会忽略这些字段。技能放在 `<repo>/.farmout/skills/<name>/`（项目级，同名时以它为准）和 `~/.config/farmout/skills/<name>/`（你自己的）。流水线阶段也可以运行技能：`skill = "security-review"`，该阶段的 `prompt` 就是它的参数。

`farmout skill add <source>` 可以从任何地方安装技能：本地文件夹、git 仓库（仓库里有多个技能时用 `--path` 指定其中一个，用 `--ref` 指定分支或标签；对应的 commit 会被记录下来），或者指向原始 `SKILL.md` 文件的 URL。来自 git 仓库或 URL 的技能，只要包含代码（`scripts/` 文件夹、可执行文件、脚本）或符号链接，就会被拒绝：代码只能来自[商店](#商店)，或者你自己复制进来的文件夹。而且，只有你担保的技能（你自己的或商店里的）才能让 farmout **真正执行操作**（运行它的 `test` 命令、给 worker 开 `write`）。项目自带的技能和从外部拉取的技能都只当作纯文本；需要的话，请自己传入 `--test` 或 `--write`。

## 钩子

钩子（hook）是在 farmout 事件发生时运行的命令，写在你的设置里：

```toml
[[hooks]]
event = "TaskEnd"                       # SessionStart, MessageReceived, TaskStart, TaskEnd, StageEnd, PipelineEnd
command = "notify-send farmout done"
matcher = "codex|claude"                # optional: a regex on the worker, session or pipeline name
timeout = 30
```

钩子从 stdin 读取一行 JSON 格式的事件（视事件而定，包含 worker、prompt、files、session、result、run、stage 等）。以 0 退出表示继续；`TaskStart` 钩子可以输出 `{"prompt": "..."}` 来改写任务说明（brief），或以 2 退出（或输出 `{"decision": "block", "reason": "..."}`），在任务到达 worker 之前把它拦下。其他任何失败都只会显示出来，然后被忽略。`farmout hooks` 会列出当前生效的钩子。

钩子就是代码，所以 farmout 只运行这几类：你自己的（`~/.config/farmout/settings`）、从商店安装的，以及项目自带的 `.farmout/settings` 里的钩子，后者要等你对该文件执行 `farmout trust` 之后才会运行。信任与文件的 SHA-256 绑定：文件一旦改动，其中的钩子就会停用，直到你再次信任它。

在设置中加上 **`redact = true`**，farmout 就会在每份任务说明和管道传入的 stdin 离开你的机器之前，把其中的凭据（API 密钥和令牌、私钥、JWT、`password=…` 的值）替换掉。worker 自己读取的文件不受影响；不要把机密放进 `files`，或者安装 `guard-secrets` 钩子，它会拒绝列出了这类文件的任务。

## 商店

`farmout store ls` 列出经过审核的技能、钩子和工具；`farmout store show <name>` 打印某个条目的文件，方便你先读一遍；`farmout store add <name>` 安装它。每个文件都会与商店索引中的 SHA-256 进行比对，只要有一个对不上，就什么都不会安装。`farmout store update` 会重新安装有新版本的条目；工具通过 `farmout tool run <name> [args]` 运行。

| 条目 | 类型 | 作用 |
|---|---|---|
| `security-review` | 技能 | 每个 worker 只针对安全问题审查你的改动，合并结果并标出有几个模型一致 |
| `test-gaps` | 技能 | 找出你的改动中没有任何测试覆盖的行为，并为每一项给出应补的测试 |
| `notify` | 钩子 | 耗时较长的一轮对话或一条流水线结束时发送桌面通知；如果设置了 `NTFY_TOPIC`，还会通过 [ntfy](https://ntfy.sh) 推送到手机 |
| `guard-secrets` | 钩子 | 拒绝会把 `.env`、私钥或凭据交给 worker 的任务 |
| `prune` | 工具 | 删除闲置超过 N 天的会话，连同它们的 worktree |

商店就是本仓库中的 [`store/`](../store/)（`index.json` 加上各条目的文件）；每个条目在合并之前都会被逐行审读。想自建商店，就把你自己设置里的 `store`（或 `FARMOUT_STORE`）指向另一个索引，本地路径也可以。只有你自己的设置才能指定商店，项目的设置永远不行。

## 哪个模型最懂你的代码

`farmout bench -n 3` 会在你的提交历史中找出最近的小型 bug 修复提交（标题像是修复，最多涉及 4 个文件、80 行改动的代码），在临时 worktree 里撤销每次修复，让 bug 以未提交改动的形式回到它真实所在的代码库，再让每个 worker 审查；只要某个 worker 有一条发现落在改动处的 3 行以内，就给它计分。代价是每个提交、每个 worker 各一次审查；报告保存在 `~/.local/state/farmout/bench/` 下。在 tqdm 的两次修复上，Claude、Codex 和 Copilot 两次都找到了，Antigravity 的快速模型找到了一次。

## 设置

`~/.config/farmout/settings.toml`（你自己的）和 `<repo>/.farmout/settings.toml`（项目的；也可以用 `.json`，在 Python 3.9–3.10 上只能用 `.json`）：

```toml
workers = ["codex", "claude"]   # which subscriptions may see this repo, e.g. keep work code off personal accounts
write = false                   # forbid write and isolate here
web = false                     # forbid web access (agy can't turn its search off, so it's refused)
fallback = ["codex", "copilot", "claude"]   # who takes over when one hits its usage limit
timeout = 900                   # seconds per task
redact = true                   # strip credentials from briefs and piped input
# [[hooks]] tables (see Hooks); in your own settings only: store = "<index URL or path>"
```

两个文件都不能放宽对方的限制：worker 列表取交集，`write` 和 `web` 只要任一方关闭就是关闭，超时以较短的为准。设置总是从主工作区读取，所以 worker 修改它 worktree 里的那份副本不会改变任何东西。

仓库根目录下的 **`.farmoutinclude`** 列出隔离 worktree 需要、但被 git 忽略的文件（glob 模式，每行一个，比如 `.env` 或 `config/*.local`）。这些文件会被复制进去，而且绝不会出现在补丁里。

## 用量上限与统计

当某个 worker 因订阅触及用量上限而失败时，farmout 会记下该让它歇多久（报错里如果有 “try again in 20 minutes” 这样的提示就照办，否则按 15 分钟算）；如果你指定了 `--fallback`（或阶段的 `fallback`，或 `fallback` 设置），就改由下一个 worker 执行该任务。在限制解除之前，发给这个 worker 的任务会直接转给下一个，`farmout run auto` 也会跳过它。`farmout stats` 根据用量日志（`~/.local/state/farmout/usage.jsonl`，从不记录 prompt 或输出内容）显示每个订阅的运行次数、失败次数、限额情况、耗时和 token 数。

## git 钩子

`farmout hook install` 会添加一个 pre-commit 钩子（加 `--pre-push` 则是 pre-push 钩子），运行 `farmout review --staged --min-agree 2 --fail-on high`：只要有两个或以上的模型指出高严重度问题，提交就会被拦下。`FARMOUT_SKIP=1`（或 `git commit --no-verify`）可以跳过一次；已有的钩子可以用 `--force` 挪到一边，执行 `farmout hook uninstall` 时会还原。

## 在 Claude Code 和其他 MCP 客户端中使用

```mermaid
flowchart LR
    you([You]) --> head["Claude Code (head)<br/>plans · routes · integrates"]
    you --> cli["farmout CLI"]
    head -- "MCP: delegate · send · pipeline" --> fo{{farmout}}
    cli --> fo
    fo --> ag["antigravity<br/>agy · Google account"]
    fo --> cx["codex<br/>Codex CLI · ChatGPT account"]
    fo --> cl["claude<br/>second Claude account"]
    fo --> cp["copilot<br/>Copilot CLI · GitHub account"]
    fo --> oa["api<br/>any OpenAI-compatible model"]
```

farmout 同时也是一个 MCP 服务器（`farmout mcp`，或 `python3 server.py`），所以 Claude Code、Codex CLI、Copilot CLI 或任何 MCP 客户端都可以充当**主控**（head）：由它负责规划，把一项工作拆成相互独立的子任务，把每个子任务交给足以胜任的模型，并把自己的额度留给真正困难的部分。farmout 把路由策略作为 MCP 服务器的 instructions 一起提供，所以不需要额外的路由模型。

| 工具 | 作用 |
|---|---|
| `delegate` | 并行执行一次性子任务（最多 8 个）：`worker`、`prompt`、`files`、`cwd`、`write`、`isolate`、`model`、`effort`；`background: true` 会立即返回。完整输出写入 `~/.cache/farmout/`，主控拿到的是截断后的副本和文件路径 |
| `send` | 向命名会话发送消息（给出 `worker` 则新开一个会话）；worker 会记住对话内容 |
| `sessions` | 列出所有会话，或某个会话的各轮对话和输出文件 |
| `pipeline` | 列出、运行、查看状态、批准、拒绝、恢复或停止流水线运行（它们在后台运行） |

插件的斜杠命令：`/farmout:review [--base <ref>] [focus]`（每个 worker 都参与审查，Claude 负责合并，还会对照代码逐条核实每项发现）、`/farmout:race <task>`、`/farmout:ask <question>` 和 `/farmout:setup`。插件的**读取守卫**（read guard）遇到大块读取（长 PDF 超过 5 页，或 200 KB 以上文件中的 500 行以上）时，会请 Claude 把这次读取委托出去，每个文件只提醒一次；在一次 16 页的深度阅读中，它[让 Claude 的成本减半，但耗时从几十秒变成了几分钟](../bench/README.md#read-guard-a-deep-read-with-and-without-it)。设置 `FARMOUT_READ_GUARD=0` 即可关闭。

其他主控：`codex mcp add farmout -- farmout mcp`、`copilot mcp add farmout -- farmout mcp`（Codex 每次调用 MCP 前都会先询问；使用 `codex exec` 时，请在 `[mcp_servers.farmout]` 下设置 `default_tools_approval_mode = "approve"`）。不装插件的话，Claude Code 用 `claude mcp add -s user farmout -- farmout mcp` 添加。

**在 DeepSeek Harness 中使用**：dsh 从它的 patch 文件中加载 MCP 服务器。把下面的内容加到 `$DSH_HOME/cordis.patch.yml`（对所有 profile 生效）或某个 profile 的 `cordis.patch.yml` 中，farmout 的工具就会以 `mcp__farmout__delegate` 等名称出现。dsh 会对 MCP 服务器剔除疑似凭据的变量和 `DSH_*` 变量，所以 farmout 需要的变量请通过 `env` 传入：

```yaml
- insert:
    - id: farmout
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: farmout
        transport: stdio
        command: farmout
        args: [mcp]
        env: { FARMOUT_CLAUDE_DIR: /home/you/.claude-2 }
        cwd: !!js process.cwd()
```

## 安全

| worker | 只读（默认） | 写入（`--write`、`--isolate`、`write: true`） |
|---|---|---|
| `antigravity` | `agy -p … --mode plan` | `--mode accept-edits --sandbox` |
| `codex` | `codex exec -s read-only` | `-s workspace-write` |
| `claude` | `claude -p --tools Read,Grep,Glob,WebSearch` | `--permission-mode acceptEdits` |
| `copilot` | `copilot -p … --allow-all-tools --available-tools view rg glob web_fetch` | 增加 `apply_patch edit create`；绝不开放 shell |
| `deepseek` | `dsh headless --permission-mode read-only --approval-policy never` | `--permission-mode workspace-write` |
| `api` | 无工具 | 不适用 |

- **为什么 `agy` 要用 plan 模式**：如果 agy 的设置里有 `toolPermission: always-proceed`，`agy -p` 会不经询问直接执行 shell 命令。plan 模式仍然能回答关于文件的问题，但不会执行任何东西，即使开着这个设置也一样。
- **为什么 `copilot` 要用白名单**：无头（headless）模式下的 Copilot 必须加 `--allow-all-tools`，而这也会自动批准你 Copilot 配置中的所有 MCP 服务器（我们自己的配置里就有一个能删除云数据库）。`--available-tools` 会对模型隐藏其他一切，MCP 服务器也包括在内。
- **隔离**：隔离的 worker 修改的是它自己位于 `<repo>/.farmout/worktrees/` 中的 worktree，交给它的文件和任务说明中的仓库内路径都会被改写为指向那里。只有在你应用补丁时，你的工作区才会改变。但这不是文件系统沙箱：被 git 跟踪、指向仓库外部的符号链接仍然可以被写穿，`codex` 和 `copilot` 还可能写入系统临时目录。
- **提示词注入是真实存在的风险**。worker 会读取你交给它们的任何内容，恶意文档可能会试图对它们下指令。只读模式能限制损害，但无法让注入变得不可能。不要把机密委托出去；在设置中写 `web = false` 可以关闭联网工具。
- **数据会离开你的机器**。任务说明和文件会发送给各个 worker 的服务商。可以用 `workers` 设置让工作仓库远离个人账号。

## 服务条款

farmout 以你的登录身份，按各厂商文档中说明的非交互模式运行其官方 CLI。它不会提取令牌，不会抓取网页应用，也不会代理 API。遵守各服务商的条款仍然是你自己的责任。不要用它来共享账号或转售访问权限。

## 踩坑笔记

以下是我们开发过程中踩过的坑，也许能帮你省下一个下午：

- **用个人 Google 账号登录的 Gemini CLI 现在会失败**，报错 `IneligibleTierError`；所以 Google 这边的 worker 驱动的是 Antigravity 的 `agy`，它的 print 模式用同一个登录就能正常工作。
- **在 `toolPermission: always-proceed` 下，`agy -p` 会不经询问直接执行工具**。凡是只读的任务都要用 `--mode plan`。plan 模式下它有时只返回一个计划空壳，所以 farmout 会要求只读任务把完整答案直接写在回复里。
- **无头模式的 Claude Code 会拒绝任何需要申请权限的工具**。只写 `--tools ...,WebSearch` 的话，模型拿到的是一个永远用不了的搜索工具；`--allowedTools WebSearch` 才会预先批准它。
- **`codex exec resume` 不接受 `-s` 或 `-C`**。沙箱要通过 `-c sandbox_mode="read-only"` 传入，并在会话所在目录中运行。
- **每个 worker CLI 都能按 ID 恢复无头会话**，但只有 Claude 和 Copilot 允许你事先指定 ID；Codex 和 Antigravity 会把各自的 ID 报告出来（`--json` 中的 `thread.started`，agy JSON 中的 `conversation_id`）。
- **嵌套调用的 `claude -p` 会继承父会话的环境变量**（`CLAUDECODE`、`CLAUDE_CODE_*` 等）。farmout 会把它们清除，确保每个 worker 都是另一个账号上的干净会话。`claude --bare` 从不读取 OAuth 凭据，所以没法使用订阅登录。
- **Copilot CLI 的 `--allow-all-tools` 连你的 MCP 服务器也会一并批准**，而且 `--deny-tool` 拦不住它们。请改用 `--available-tools` 设置白名单。
- **Python 的 `text=True` 会把 CRLF 转成 LF**。这样读进来的 diff 无法再应用到 CRLF 文件上，所以 farmout 以字节形式传递补丁。
- **git 钩子在内部的提交和检出时也会触发**。放在 pre-commit 里的审查门禁会把 farmout 创建的每个 worktree 都审查一遍，而 `--no-verify` 仍然会运行 post-commit 和 post-checkout 钩子；因此 farmout 自己调用 git 时都会带上 `-c core.hooksPath=/dev/null`。
- **Claude Code 拒绝修改 `.git` 下的内容**，即使 worktree 就放在那里面也不行，所以 worktree 都放在 `<repo>/.farmout/worktrees/`。
- **模型引用路径时，是相对于它自认为所在的目录**（把 `pkg/cli.py` 写成 `cli.py`），而且除非另有说明，给出的行号是 `.diff` 文件内的行号。farmout 的审查任务说明会要求给出当前文件中的行号，它的引用检查也接受唯一的后缀匹配。

测试环境：Arch Linux 上的 agy 1.4.6、codex-cli 0.163.0、Claude Code 2.1.323、GitHub Copilot CLI 1.0.112 和 Python 3.14（2026 年 11 月）；离线测试套件在 Python 3.9 和 3.12 上也能通过。这些 CLI 变化很快，如果某个 worker 出了问题，请提交 issue 并附上你的 CLI 版本。`deepseek` worker 尚处于实验阶段：它的参数依据 DeepSeek Harness 0.2.2 的文档编写，还没经过真正的冒烟测试。

## 配置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `FARMOUT_CLAUDE_DIR` | 未设置 | 第二个 Claude Code 账号的配置目录；设置后启用 `claude` |
| `FARMOUT_API_BASE` | 未设置 | 以 `/v1` 结尾的 OpenAI 兼容 base URL；与 `FARMOUT_API_MODEL` 一起设置后启用 `api` |
| `FARMOUT_API_MODEL` | 未设置 | 发送给该接口的模型名称 |
| `FARMOUT_API_KEY` | 未设置 | 该接口的 Bearer 令牌 |
| `FARMOUT_TIMEOUT` | `1200` | 每个任务的超时秒数（`timeout` 设置可以把它调低） |
| `FARMOUT_STATE` | `~/.local/state/farmout` | 会话、流水线运行记录、用量日志、基准测试报告的存放位置 |
| `FARMOUT_STORE` | GitHub 上 farmout 的 `store/index.json` | 商店索引（URL 或本地路径） |
| `FARMOUT_READ_GUARD` | 开启 | 设为 `0` 关闭插件的读取守卫 |
| `FARMOUT_SKIP` | 未设置 | 设为 `1` 跳过一次 git 钩子 |

一次性 `delegate` 的输出存放在 `~/.cache/farmout/`，可以放心删除。`farmout rm <session>` 会删除一个会话，连同它的 worktree 和分支。

## 常见问题

**它和 OpenAI 为 Claude Code 推出的 Codex 插件有什么不同？** 那个插件只能在 Claude Code 里驱动 Codex。farmout 能驱动你拥有的任何订阅（Codex、通过 Antigravity 使用的 Gemini、Copilot、第二个 Claude 账号、本地模型），可以同时用好几个，在终端或任何 MCP 客户端里都能用，还支持会话和流水线。

**会不会把我其他订阅的额度烧光？** 每个任务消耗的是执行它的那个 worker 的额度。`review`、`ask` 和 `race` 会给每个 worker 各发一个任务；`farmout stats` 会显示每个 worker 做了什么。用 `-w` 挑选要用的 worker。

**审查能省下 Claude 的额度吗？** 命令行里的 `farmout review` 完全不消耗 Claude 的额度（除非你把第二个 Claude 账号当作 worker）。在 Claude Code 里，`/farmout:review` 反而比让 Claude 自己审查花得更多，因为 Claude 要合并并核实四份报告：在我们那个小型基准改动上，前者用了 59 秒、$0.122，后者是 13 秒、$0.033。用它是为了看多个模型的共识；想省额度，就把阅读和体力活委托出去。

**会更慢吗？** 并行调用的速度取决于最慢的那个 worker：四个模型的审查大约要一分钟，竞速要几分钟。可以用 `--bg`、`farmout attach` 和 `farmout wait`，在等待的同时继续工作。

**一定要用 Claude Code 吗？** 不用。命令行只需要你已有的 worker CLI；任何 MCP 客户端都可以充当主控。

## 同类工具

这个领域已经有不少好工具，按需选择即可：

| 项目 | 适合以下情况 |
|---|---|
| [Claude Code 的 Codex 插件](https://github.com/openai/codex-plugin-cc) | 你只需要 Codex，并且想要 OpenAI 官方的 rescue（救援）、会话转移和后台任务命令 |
| [Claude Octopus](https://github.com/nyldn/claude-octopus) | 你想要结构化的多模型议事（council）、众多服务商和工作流门禁，并且不介意插件体量较大 |
| [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server)（前身为 Zen） | 你想要大量工作流工具（共识、代码审查、规划）和基于 API Key 的模型，外加它的 `clink` CLI 桥接 |
| [claude-squad](https://github.com/smtg-ai/claude-squad)、[Conductor](https://conductor.build)、[vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 你想用 TUI、桌面应用或看板并排运行多个交互式 agent |
| [claude-code-router](https://github.com/musistudio/claude-code-router) | 你想替换 Claude Code 背后的模型，而不是使用其他 CLI |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | 你想迁移到一个围绕 DeepSeek 模型打造、开放且基于插件的 harness（farmout 既能把它当作 worker 驱动，也能在它内部运行） |
| **farmout** | 你想让手上已有的订阅协同工作：默认只读，在终端或 MCP 客户端中使用，具备会话、跨模型审查、竞速、流水线、技能、钩子和按哈希锁定的商店，而且是可以审计的纯 Python 标准库实现 |

## 路线图

- 更多 worker，且只读参数都经过验证：OpenCode、Qwen Code、Cursor CLI，以及面向 API Key 用户的 Gemini CLI
- 发布到 PyPI，支持 `uvx farmout`
- CI 模式：只把多个模型一致认可的发现作为评论发到 pull request 上
- 让编辑器通过 Agent Client Protocol 驱动 farmout；在容器中运行 worker

## 参与贡献

欢迎提交 issue 和 PR，详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。测试：`python3 -m unittest discover -s tests -v`（离线运行，使用模拟的 worker CLI）。整个包只依赖标准库。新增一个 worker 只需在 `farmout/workers.py` 里加一个函数，再加一条 `WORKERS` 条目；请注明你测试所用的 CLI 版本，并证明它的只读模式确实无法写入。安全问题请私下报告，见 [SECURITY.md](../SECURITY.md)。

## 许可证

[MIT](../LICENSE)

如果 farmout 帮你省下了额度，欢迎[给仓库点个 Star](https://github.com/JayPokale/farmout/stargazers)：这个不花额度。
