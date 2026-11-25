---
name: setup
description: Check which farmout workers are ready, and how to enable the rest
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/server.py doctor:*), Bash(${CLAUDE_PLUGIN_ROOT}/server.py relay on:*)
---

# /farmout:setup

```!
"${CLAUDE_PLUGIN_ROOT}/server.py" doctor
```

Summarize the report above: which workers are ready, and for each missing one the exact step to enable it. Workers are detected when Claude Code starts, so after enabling one the user must restart Claude Code.

Then offer the live login check, which sends each ready worker one tiny prompt (a little quota each): `"${CLAUDE_PLUGIN_ROOT}/server.py" doctor --live`. Run it only if the user agrees, and explain any BROKEN line (usually a login to redo).

If the report says the relay is off, say in one line what it does (once Claude Code hits its usage limit, what the user types next goes to another of their subscriptions, with this conversation, and the answer shows here; Claude catches up after the reset) and offer to turn it on: `"${CLAUDE_PLUGIN_ROOT}/server.py" relay on --write` (the worker may edit files; without `--write` it only reads). Run it only if the user agrees. Don't run anything else.
