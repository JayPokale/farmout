---
name: review
description: Review the current changes with every modelmux worker in parallel (read-only), then merge their findings with agreement counts
argument-hint: "[--base <ref>] [focus]"
disable-model-invocation: true
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git rev-parse:*), Bash(mkdir:*), Bash(wc:*), mcp__plugin_modelmux_modelmux__delegate
---

# /modelmux:review

Arguments: $ARGUMENTS

## 1. Save the change without reading it

- With `--base <ref>`, review the branch: `git diff <ref>...HEAD`. Otherwise review uncommitted work: `git diff HEAD`, plus untracked files from `git status --porcelain`.
- Any other argument text is the review focus.
- Write the diff to a file without printing it, e.g. `mkdir -p "$(git rev-parse --absolute-git-dir)/modelmux" && git diff HEAD > "$(git rev-parse --absolute-git-dir)/modelmux/review.diff"`, then check its size with `wc -c`.
- If the diff is empty and nothing is untracked, say there is nothing to review and stop.

## 2. Fan out

Call the modelmux `delegate` tool once, with one task per worker in its `worker` enum, so every available model reviews in parallel. Never set `write`. Each task gets:

- `cwd`: the repository root (`git rev-parse --show-toplevel`)
- `files`: the saved diff first, then any untracked files
- `prompt`: exactly the brief below, plus `Focus: <focus>` if the user gave one

```text
Review a code change for defects. The unified diff is in the first listed file; any other listed files are new, untracked files. Read any repository file you need for context; change nothing. Report only problems worth blocking a merge: bugs, crashes, security holes, data loss, races, broken edge cases, wrong behaviour. No style or naming nits, no praise, no summary. One finding per line, exactly:
path:line | high/medium/low | defect | input or state that triggers it | fix
The line is the line number in the file as it is now (open the file to check it), not a position in the diff.
At most 10 findings, most severe first. If there are none, reply exactly: NO FINDINGS
```

## 3. Merge and verify

- Group findings that describe the same defect (same place, same root cause) and count the workers behind each group.
- Open the cited code and check every group yourself. Drop wrong ones; mark ones you cannot confirm as unverified. Agreement raises priority but is not proof.
- Note every worker that FAILED, with its reason.

## 4. Report

A table ranked by severity, then agreement: `severity | location | defect | flagged by (names, n/N) | verdict`. Below it, one line on failed workers and the slowest worker's time from the result headers. Keep it short, and change no code unless the user asks.
