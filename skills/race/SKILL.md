---
name: race
description: Every write-capable farmout worker attempts the same change in its own git worktree, in parallel; Claude compares the attempts and applies the best one when you agree
argument-hint: "<task>"
disable-model-invocation: true
allowed-tools: mcp__plugin_farmout_farmout__delegate
---

# /farmout:race

Task: $ARGUMENTS

1. Write one standalone brief, because workers see nothing else: the goal, where the relevant code is (paths), the exact command that verifies success (tests, build), and constraints: smallest change that works, no unrelated edits, no new dependencies.
2. Call the farmout `delegate` tool once, with that brief for every worker in its `worker` enum except `api`, each with `"isolate": true` and `cwd` set to the repository root. Each worker edits its own git worktree (your HEAD plus uncommitted changes), so attempts can't collide and the user's working tree stays untouched.
3. Each result ends with an `[isolated]` footer naming its worktree, branch and patch. Run the verification command in every worktree that changed something, then read the patches.
4. Report a table: `worker | files changed | verification | notes`. Recommend one patch, or a combination, and say why: correctness first, then the smallest diff. Apply it with `git apply <patch>` only after the user agrees. Then offer cleanup: `git worktree remove --force <worktree>` and `git branch -D <branch>` for each attempt.
