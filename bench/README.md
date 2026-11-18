# Review benchmark: one model vs all of them

Does asking every subscription to review a change catch more real bugs than asking the best one? This benchmark plants known bugs in small code changes, sends the exact `/farmout:review` brief to each worker, and counts what each one finds on its own and what all of them find together.

The runs below were recorded before the project was renamed from modelmux to farmout, so their transcripts and paths show the old name.

## At a glance (2026-11-17)

| question | answer |
|---|---|
| Does delegating save Claude's own quota? | Summarizing three papers: **−59% head tokens, −74% head cost**, same answers, but 62s instead of 13s. |
| Do the reviewers find real bugs? | 23 planted bugs and 9 real regressions (BugsInPy): **every one flagged by at least 3 of 4 models**; 27 of 32 by all four. |
| Does running four reviewers beat the best one? | **Not when the best one has room.** Claude (Sonnet) alone found everything when allowed 30 findings. Four reviewers covered more only under a short-list cap (17 vs 10 of 23 on one big change). The value is agreement and not having to pick this week's best model. |
| Does `/farmout:review` save Claude's quota? | **No.** On the `files_api` change Claude reviewing alone cost $0.033 in 13s and found the same planted bugs; `/farmout:review` cost Claude $0.122 in 59s (it merges and verifies four reports), plus one review on each worker. |
| How long does `/farmout:review` take? | About a minute end to end with Antigravity on Flash low (220s on its default model). |
| Does `/farmout:race` keep your tree safe? | Yes, after a fix: in five runs, a brief naming the repo path once let temp-writable workers edit it. Briefs are now rewritten to the worktree, and the last run left the tree clean while Claude's test re-runs caught a wrong patch. |
| Does the read guard pay off? | On a 16-page deep read: **Claude's cost halved**, but 240s instead of 20s. On skims it never fires. |

## Method

- **Cases** (`cases/<name>/`): new files that make up a realistic change, plus `bugs.json` listing the planted bugs with the line each lives on and why it's wrong. Five cases, 23 bugs, in two sets. Set 1 holds common classes: SQL injection, path traversal, prototype pollution, a 1-based page offset, a missing commit, an unchecked `None` row, wall-clock rate limiting, a check-then-set race, a retry count off by one, a swallowed exception, TTL units (ms vs s), FIFO masquerading as LRU, an un-awaited write, and a numeric sort that sorts strings. Set 2 holds subtler ones: float cents truncated by `int()`, a timing-unsafe HMAC compare, a ReDoS pattern, a dict mutated while iterated, `indexOf(...) > 0`, a single-occurrence `replace`, 64-bit ids losing precision, 1-based months passed to `Date.UTC`, and `forEach(async …)`. Every planted bug was reproduced in code before the runs.
- **Run** (`review_recall.py`): for each case, a throwaway git repo holds the files as an uncommitted change. One `delegate` call sends every enabled worker the brief from `skills/review/SKILL.md`, read-only, so the workers review in parallel.
- **Scoring** (`--score <dir>` re-scores saved outputs without calling any model): a finding credits at most one planted bug in the same file. That's the nearest bug the finding names (one of its `keywords` in `bugs.json`) within 10 lines, or else the nearest bug within 3 lines. Planted bugs sit at least 7 lines apart. Findings that match no planted bug are counted, not judged: some are real issues the cases didn't plant.
- **Brief v1 vs v2:** the first run showed the `claude` worker citing line numbers inside the `.diff` file (6 too high), so the brief now asks for line numbers in the current file. The v1 outputs are kept in `results/2026-11-17-brief-v1/`.
- **Raw data:** every worker's full output is committed under `results/<date>/`, misses included.

## Results

### Set 1: common bug classes (3 cases, 14 bugs), 2026-11-17, brief v2

| reviewer | files_api (5) | lru_config (5) | ratelimit (4) | total (14) | findings | review time |
|---|---|---|---|---|---|---|
| antigravity | 5 | 5 | 4 | **14** | 17 | 656s |
| codex | 4 | 5 | 3 | 12 | 14 | 105s |
| claude (Sonnet) | 5 | 5 | 4 | **14** | 23 | 34s |
| copilot | 5 | 5 | 3 | 13 | 14 | 85s |
| **all four, in parallel** | 5 | 5 | 4 | **14** | 68 | 656s |

### Set 2: subtler bugs (2 cases, 9 bugs), 2026-11-17

| reviewer | billing (4) | events (5) | total (9) | findings | review time |
|---|---|---|---|---|---|
| antigravity | 4 | 5 | **9** | 9 | 360s |
| codex | 3 | 5 | 8 | 9 | 78s |
| claude (Sonnet) | 4 | 5 | **9** | 17 | 23s |
| copilot | 3 | 5 | 8 | 9 | 49s |
| **all four, in parallel** | 4 | 5 | **9** | 44 | 360s |

Codex missed the timing-unsafe HMAC compare; Copilot missed the ReDoS. Per-bug detail: `results/2026-11-17/summary.md`.

### Set 3: real regressions (BugsInPy, 9 bugs), 2026-11-17

`real_bugs.py` takes real bug fixes from [BugsInPy](https://github.com/soarsmu/BugsInPy) (cookiecutter 1; httpie 3, 4; PySnooper 3; tqdm 1, 4, 6, 7, 8), fetches each project at its fixed commit, and reverse-applies the fix as an uncommitted change. Reviewers therefore see a real one- or two-line regression inside its real codebase. Antigravity ran on Flash low.

| reviewer | found (of 9) | findings | review time |
|---|---|---|---|
| antigravity | **9** | 9 | 325s |
| codex | **9** | 9 | 295s |
| claude (Sonnet) | **9** | 11 | 94s |
| copilot | **9** | 9 | 222s |
| **all four, in parallel** | **9** | 38 | 389s |

Scoring is by location, so we read the four subtlest outputs by hand (`tqdm/6`, `tqdm/7`, `tqdm/8`, `httpie/4`): every model named the actual root cause, e.g. the dropped `(?<!\S)` lookbehind, `l_bar` formatted instead of `l_bar_user`, `self.total` unset on the `disable=True` early return, and a case-sensitive `Host` check on a plain dict. Caveat: a reversed fix is a small, focused diff that points straight at the bug. Bugs buried in large changes are harder, and these runs don't measure that.

### Set 4: one big change (all 23 planted bugs in one diff), 2026-11-17

`big_change.py` puts every planted case into a single change and asks each worker once. Two runs: the brief's default cap of 10 findings, then the same brief allowing 30.

| reviewer | found, top 10 | found, up to 30 |
|---|---|---|
| antigravity (Flash low) | 10 | 17 |
| codex | 9 | 16 |
| claude (Sonnet) | 10 | **23** |
| copilot | 10 | 19 |
| **all four, in parallel** | **17** | **23** |

With the cap at 10, every reviewer filled its list, and together they covered 17 of 23: each model listed at least one bug no other did. Allowed 30, Claude (Sonnet) alone found all 23. So on a big change the cap, not the models, limited coverage. Breadth from parallel reviewers helps when each review must stay short, but the best single model given room did as well. `/farmout:review` now allows 20 findings per reviewer. Raw outputs: `results/2026-11-17-big-cap10/`, `results/2026-11-17-big-cap30/`.

### What this does and doesn't show

- **No recall gain over the best single model, given room.** Antigravity and Claude (Sonnet) each found all 23 planted bugs, all four models found all 9 real regressions, and on one big change Claude found all 23 when allowed 30 findings. Parallel reviewers covered more only when each was held to a short list (17 vs 10 of 23). Planted, well-known bug classes are within every 2026 frontier model's reach. A harder benchmark (real bug-fix commits) is future work.
- **Strong agreement.** Across all 32 bugs (23 planted, 9 real), every one was flagged by at least three of the four models, and 27 by all four. When `/farmout:review` reports "flagged by 4/4", that has meant a real bug every time here.
- **Misses move around.** Codex missed different bugs in two runs of the same case, and Copilot missed others. The fan-out means you don't have to know which model is reliable this week.
- **Cost in time, not in Claude's tokens.** A parallel call is as slow as its slowest worker: Antigravity averaged about 3.4 minutes per review, while Claude (Sonnet) took about 11 seconds. Running everything produced 112 findings for 23 planted bugs, so the head's merge-and-verify step matters. Some extras are real issues the cases didn't plant (missing authorization, quota races), and some are noise.

### Antigravity: default vs Flash low

Antigravity set every review's wall time (about 3.5 minutes per review on its default model). Same brief, same five cases, model `gemini-3.8-flash-low` (2026-11-17):

| case | billing | events | files_api | lru_config | ratelimit | total |
|---|---|---|---|---|---|---|
| found | 4/4 | 5/5 | 5/5 | 5/5 | 3/4 | **22/23** |
| time | 43s | 19s | 20s | 23s | 20s | |

It missed only `retry-count`, which the other three workers all caught, so a parallel review loses nothing on these cases. Its wall time falls to the next-slowest worker (about 45s for Codex). `/farmout:review` now asks for this model. (`gemini-3.8-flash-medium` took 209s on `billing`, so its speed comes from the low reasoning effort.)

### End to end

`results/2026-11-17/demo-review.md` is a real `/farmout:review` run on `files_api`, from the plugin loaded with `--plugin-dir`. It shows each worker's raw findings and Claude's merged, verified table: all five planted bugs confirmed (four at 4/4 agreement), plus four real issues nobody planted. The run took 59s with Antigravity on Flash low (an earlier run on its default model took 220s); Claude's own cost was $0.122. `results/2026-11-17/demo-race.md` is a real `/farmout:race` run: four fixes in parallel in 66s, the user's tree untouched. Claude re-ran the tests on every patch: three identical fixes passed, and Copilot's failed one test, which Claude flagged instead of applying.

## Head tokens: reading papers alone vs delegating

`head_tokens.py` runs one task twice with `claude -p` (Sonnet head, separate account): summarize three arXiv papers (Transformer, BERT, ResNet; 43 pages), giving method, datasets and headline number. Once the head reads the PDFs itself; once it may only call `delegate`, and Antigravity reads them. Numbers are the head's own usage from `--output-format json` (2026-11-17, one run each):

| head | total tokens | cache write | cache read | output | cost | time |
|---|---|---|---|---|---|---|
| Claude reads the PDFs | 48,097 | 34,316 | 12,816 | 959 | $0.148 | 13s |
| Claude + farmout | 19,747 | 7,082 | 11,683 | 976 | $0.039 | 62s |

**59% fewer head tokens and 74% less head cost**, with the same correct headline numbers in both answers (28.4/41.8 BLEU, GLUE 80.5, 3.57% top-5 error). The trade is wall time: Antigravity took a minute where the head took 13 seconds. Both runs pay roughly 12k cached tokens of fixed overhead (system prompt, tools); most of the rest of the first run is the PDFs, so the gap grows with every page the head no longer reads. Full answers: `results/2026-11-17/head_tokens.md`.

## Read guard: a deep read with and without it

Task (`claude -p`, Sonnet head, plugin loaded): "Summarize every numbered section of bert.pdf in one sentence each, including the appendix sections" (16 pages, 2026-11-17, one run each):

| run | what happened | head tokens | head cost | time |
|---|---|---|---|---|
| guard off (`FARMOUT_READ_GUARD=0`) | Claude read pages 1-10 and 11-16 itself | 50,457 | $0.139 | 20s |
| guard on | the 16-page read was stopped once; Claude delegated it to Antigravity and answered from the result | 43,600 | $0.069 | 240s |

Claude's cost halved (writing a PDF into its context is the expensive part) for the same section-by-section answer, but it took four minutes instead of twenty seconds. An earlier guard-on run exposed an Antigravity plan-mode stub ("approve to proceed"); Claude then read the PDF anyway (85,871 tokens, $0.155). That is fixed. On skim tasks (abstracts, first pages) the guard never fires: Claude reads one page per paper and stays cheap.

Disclosure for both head benchmarks: the head account had the [Chisle](https://github.com/JayPokale/Chisle) plugin on (it makes Claude's replies terser; same author as farmout). Each comparison has it on both sides, so the differences are like for like, but the absolute numbers are not those of a stock Claude Code.

## Bug-Finding Leaderboard (tooling ready, not run yet)

`leaderboard.py` scales the real-regression idea up: recent bug fixes (since August 2026) from ten well-known projects in Python, TypeScript, Go and Rust (41 fixes in `leaderboard/suite.json`: every subject screened by hand, doubtful diffs read, then a second opinion from Claude Opus and Codex on each, in `leaderboard/screening.md`; 13 picks rejected), each undone in a fresh repository and reviewed by every lane: a worker on its default model or a named one, such as `claude:opus` or a model your Copilot plan offers. Only a fix's source files are undone, so no deleted regression test or changelog line names the bug, and the copy's only commit is "snapshot", so `git log` can't either. Each (fix, lane) is one review, results are kept per file so a run resumes, and a new model is one more lane:

```bash
python3 bench/leaderboard.py run -l claude:opus,codex,antigravity:gemini-3.8-flash-high,copilot
python3 bench/leaderboard.py run -l copilot:<new-model>        # a model that shipped today: one more lane
python3 bench/leaderboard.py render                              # leaderboard/README.md, leaderboard.json, card.svg/.png
```

## Reproduce

```bash
python3 bench/review_recall.py              # every enabled worker; spends one review per worker per case
python3 bench/review_recall.py codex copilot   # only these
HEAD_CONFIG_DIR=~/.claude-2 python3 bench/head_tokens.py papers/*.pdf
# a coding task, alone vs with farmout, each in a fresh clone; the check runs after each (not run yet)
HEAD_CONFIG_DIR=~/.claude-2 python3 bench/head_tokens.py --code ~/some-repo --task "Add tests for parse_date()" --check "python3 -m pytest -q"
```

Limits: 14 bugs is a small sample, the cases are short, and line-window matching can credit a finding that cites the right line for the wrong reason (or miss one that cites the wrong line). Treat the numbers as a sanity check, not a leaderboard.
