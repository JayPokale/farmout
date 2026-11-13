---
name: ask
description: Ask every farmout worker the same question in parallel, then compare their answers
argument-hint: "<question>"
disable-model-invocation: true
allowed-tools: mcp__plugin_farmout_farmout__delegate
---

# /farmout:ask

Question: $ARGUMENTS

1. Write one standalone brief, because workers see nothing else: the question; the context from this conversation that a fresh model needs (goal, constraints, decisions made, what was tried); paths of relevant files go in `files`, never pasted. Ask for the answer first, then the reasoning in at most 10 lines, then a confidence (high/medium/low).
2. Call the farmout `delegate` tool once, with that same brief for every worker in its `worker` enum, read-only, `cwd` set to the project root.
3. Reply with the consensus, the disagreements and what drives them, points only one model raised, and your own verdict after weighing them: say which workers you side with and why. Name the workers. Be brief.
