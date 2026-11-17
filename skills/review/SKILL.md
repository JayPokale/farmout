---
name: review
description: Review the current changes with every farmout worker in parallel (read-only), then merge their findings with agreement counts
argument-hint: "[--base <ref>] [focus]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/skills/review/save-diff.sh:*), mcp__plugin_farmout_farmout__delegate
---

# /farmout:review

Arguments: $ARGUMENTS

## 1. The change, already saved

```!
"${CLAUDE_PLUGIN_ROOT}/skills/review/save-diff.sh" $ARGUMENTS
```

The output above names the repository root, the diff file and any untracked files; don't read the diff yourself. With `--base <ref>` the target is the branch since `<ref>`, otherwise the uncommitted work. Argument text other than `--base <ref>` is the review focus. If the diff is 0 bytes and nothing is untracked, say there is nothing to review and stop.

## 2. Fan out

Call the farmout `delegate` tool once, with one task per worker in its `worker` enum, so every available model reviews in parallel. Never set `write`. Each task gets:

- `cwd`: the repository root shown above
- `files`: the diff file first, then any untracked files shown above
- `prompt`: exactly the brief below, plus `Focus: <focus>` if the user gave one
- for `antigravity` only: `"model": "gemini-3.8-flash-low"`. In our benchmark it found 22 of 23 planted bugs in about 20s, where the default model needed over 3 minutes. If that task fails on an unknown model, rerun it once without `model`.

```text
Review a code change for defects. The unified diff is in the first listed file; any other listed files are new, untracked files. Read any repository file you need for context; change nothing. Report only problems worth blocking a merge: bugs, crashes, security holes, data loss, races, broken edge cases, wrong behaviour. No style or naming nits, no praise, no summary. One finding per line, exactly, starting with the file's real path from the repository root:
path/to/file.py:42 | high/medium/low | what is wrong | input or state that triggers it | fix
The line is the line number in the file as it is now (open the file to check it), not a position in the diff.
At most 20 findings, most severe first. If there are none, reply exactly: NO FINDINGS
```

## 3. Merge and verify

- Group findings that describe the same defect (same place, same root cause) and count the workers behind each group.
- Open the cited code and check every group yourself. Drop wrong ones; mark ones you cannot confirm as unverified. Agreement raises priority but is not proof.
- Note every worker that FAILED, with its reason.

## 4. Report

A table ranked by severity, then agreement: `severity | location | defect | flagged by (names, n/N) | verdict`. Below it, one line on failed workers and the slowest worker's time from the result headers. Keep it short, and change no code unless the user asks.
