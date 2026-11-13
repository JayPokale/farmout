---
name: setup
description: Check which farmout workers are ready, and how to enable the rest
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/server.py doctor:*)
---

# /farmout:setup

```!
"${CLAUDE_PLUGIN_ROOT}/server.py" doctor
```

Summarize the report above: which workers are ready, and for each missing one the exact step to enable it. Workers are detected when Claude Code starts, so after enabling one the user must restart Claude Code.

Then offer the live login check, which sends each ready worker one tiny prompt (a little quota each): `"${CLAUDE_PLUGIN_ROOT}/server.py" doctor --live`. Run it only if the user agrees, and explain any BROKEN line (usually a login to redo). Don't run anything else.
