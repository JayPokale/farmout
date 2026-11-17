# farmout

[English](../README.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

**让你付费的每个 AI 订阅都动起来：Codex、Gemini、GitHub Copilot、第二个 Claude 账号和本地模型。会记住上下文的会话、并行代码审查、由你的测试裁决的竞赛和流水线，在终端里或在 Claude Code 内部都能用。默认只读，零依赖。**

[![CI](https://github.com/JayPokale/farmout/actions/workflows/ci.yml/badge.svg)](https://github.com/JayPokale/farmout/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio-6e56cf)
![license](https://img.shields.io/badge/license-MIT-blue)

> **说明：** 本译文对应较早的版本。最新功能（`farmout bench --share`、实时进度显示、DeepSeek 支持等）请以[英文 README](../README.md) 为准。

```bash
pipx install git+https://github.com/JayPokale/farmout
cd your-repo && farmout bench --share   # 哪个 AI 订阅最能在你自己的代码里找出真实的 bug？
```

<p align="center"><img src="../assets/review-demo.svg" width="900" alt="真实运行的终端动画回放：/farmout:review 将一处未提交修改并行发送给 antigravity、codex、claude 和 copilot，随后 Claude 输出合并校验后的 9 条发现结果表格，并标明各问题被多少个模型指出"></p>

- **`/farmout:review`**：Codex、Gemini（通过 Antigravity）、GitHub Copilot 以及第二个 Claude 账号同时以只读方式审查你的 diff。Claude 会合并审查结果，统计指出每个问题的模型数量，并对照代码逐一核对每项发现。在[我们的基准测试](../bench/README.md)中，32 个 Bug（23 个预设的、9 个来自开源项目的真实回归）全部被 4 个模型中的至少 3 个指出。
- **`/farmout:race <task>`**：每个模型各自在独立的 git worktree 中尝试相同的修复；由你的测试选出胜者，且在得到你的许可前绝不修改你的代码树。
- **日常委托**：Claude 将长文阅读和样板代码交给最合适的订阅处理。以此方式总结 3 篇论文，Claude 在获得相同答案的前提下**节省了 59% 的 Token**（成本降低 74%）。

你付费订阅了 Claude，可能还订了 ChatGPT、Google AI、第二个 Claude 席位，或者在本地 GPU 上运行开源模型。然而，你最强模型的额度却仍然被用来阅读 PDF 和编写样板代码，与此同时其他模型却在闲置。

farmout 让 Claude Code 担任**主控**。它负责规划、将任务拆解为独立子任务，并将各任务分发给足以胜任的模型，把自身的额度留给核心难点：

- 60 页的论文交给 **Antigravity** (Gemini) 阅读，提炼返回 150 字总结
- “为 `parse_date()` 编写测试”交给 **Codex**，由其编写并运行测试
- 中等复杂度的任务交给你的**第二个 Claude 账号**
- 针对棘手修改的复核建议交给 **GitHub Copilot**，它提供来自多家供应商的模型
- 2,000 行代码分类交给**任意兼容 OpenAI 的模型**（Ollama、vLLM、OpenRouter、Qwen 等）

这些任务均可通过单次工具调用并行运行。每个 worker 均为各厂商官方的 CLI 工具，使用你自己的订阅直接登录，因此 CLI worker 无需任何 API Key。

## 命令行

farmout 也是一个命令行工具。克隆仓库后用 `pipx install` 安装（即将发布到 PyPI），再用 `farmout doctor --live` 检查各个 worker：

```bash
# 所有模型审查你的改动，合并发现并标明有几个模型一致
farmout review
# 每个模型在各自的 worktree 中尝试，由你的测试选出胜者
farmout race "让 tests/test_sync.py 里不稳定的测试变得确定" --test "pytest tests/test_sync.py -x"
# 一个会记住对话的会话
farmout run codex "为 src/dates.py 中的 parse_date() 添加单元测试" --isolate -n dates
farmout send dates "再覆盖闰年的情况"
# 把补丁应用到你的工作区（或 --pr）
farmout apply dates
# 审查 → 修复 2 个以上模型一致的问题 → 审查修复
farmout pipe run fix
# 每个订阅为你做了什么
farmout stats
```

会话、流水线（带阶段、关卡和审批的 `.toml` 或 `.json` 文件）、按仓库的设置（`.farmout/settings.toml`：哪些服务商可以看到代码、禁止写入、禁止联网）、某个订阅用尽额度时自动换用其他 worker，以及审查每次提交的 git hook：详见[英文 README](../README.md)。

## 快速开始

运行环境要求：Python 3.9+、Linux（已测试）或 macOS（未测试）、[Claude Code](https://github.com/anthropics/claude-code)，以及至少一个 worker：

| worker | 所需条件 | 适用场景 |
|---|---|---|
| `antigravity` | 已登录的 [Antigravity CLI](https://antigravity.google) (`agy`) | 长篇 PDF、论文、多文件阅读、网络调研；Gemini 以及 `agy models` 列出的其他模型 |
| `codex` | 已登录的 [Codex CLI](https://github.com/openai/codex) | 需求明确的编码、测试编写、重构、审查 |
| `claude` | 第二个 Claude Code 账号 + `FARMOUT_CLAUDE_DIR` | 使用独立额度处理常规任务 |
| `copilot` | 已登录的 [GitHub Copilot CLI](https://github.com/github/copilot-cli) | 代码审查、针对性编码问题、复核建议；支持多家供应商的模型 |
| `api` | `FARMOUT_API_BASE` + `FARMOUT_API_MODEL` | 在任意兼容 OpenAI 的模型上执行批量或简单任务 |

```bash
git clone https://github.com/JayPokale/farmout ~/.local/share/farmout
claude mcp add -s user farmout -- python3 ~/.local/share/farmout/server.py
claude mcp list        # farmout: ... ✔ Connected
```

或者将其安装为 Claude Code 插件，通过 `claude plugin update farmout` 即可保持最新版本：

```bash
claude plugin install farmout --marketplace JayPokale/farmout
```

插件的服务端从环境变量读取配置，因此使用插件安装时，请在 Shell 配置文件中 `export` 下方列出的 `FARMOUT_*` 变量，而无需通过 `-e` 传递。两种安装方式二选一，切勿同时使用。

**其他主控。** Codex CLI 和 Copilot CLI 也可以驱动 farmout，从而把工作交给 Claude、Gemini 或彼此（两者均已通过与 `claude` worker 的往返调用测试）：

```bash
codex mcp add farmout -- python3 ~/.local/share/farmout/server.py
copilot mcp add farmout -- python3 ~/.local/share/farmout/server.py
```

Codex 在每次调用 MCP 工具前都会请求确认。使用 `codex exec` 时，请在 `~/.codex/config.toml` 的 `[mcp_servers.farmout]` 下添加 `default_tools_approval_mode = "approve"`。斜杠命令仅适用于 Claude Code。

重启 Claude Code。所有 worker 会在启动时自动检测。要查看哪些 worker 已就绪以及如何启用其余 worker，请运行 `python3 server.py doctor`（若使用插件则运行 `/farmout:setup`）；`doctor --live` 还会向每个 worker 发送一条简短 prompt，以验证其登录是否有效。

## 使用方法

### 斜杠命令（插件安装）

| 命令 | 功能说明 |
|---|---|
| `/farmout:review [--base <ref>] [focus]` | 所有 worker 以只读方式并行审查你未提交的修改（或自 `<ref>` 以来的分支修改）；Claude 会合并发现的问题，统计标记各问题的模型数量，并对照代码逐一核对 |
| `/farmout:ask <question>` | 向所有 worker 提出相同问题；汇总各方共识、分歧点以及 Claude 的最终裁决 |
| `/farmout:race <task>` | 所有具备写入能力的 worker 各自在独立的 git worktree 中并行尝试相同的修改；Claude 会针对每次尝试运行测试，对比补丁，并在你确认后应用最优方案 |
| `/farmout:setup` | 查看哪些 worker 已就绪，以及启用各个未就绪 worker 的具体步骤 |

## 安全性

| worker | 只读（默认） | `write: true` |
|---|---|---|
| `antigravity` | `agy -p … --mode plan` | `--mode accept-edits --sandbox` |
| `codex` | `codex exec -s read-only` | `-s workspace-write` |
| `claude` | `claude -p --tools Read,Grep,Glob,WebSearch` | `--permission-mode acceptEdits` |
| `copilot` | `copilot -p … --allow-all-tools --available-tools view rg glob web_fetch` | 增加 `apply_patch edit create`；绝不调用 Shell |
| `api` | 无工具支持 | 不适用 |

---

本篇为精简译文。完整配置说明、`delegate` 工具详解、实战笔记、常见问题（FAQ）以及基准测试，请参阅英文原版 [README](../README.md)。
