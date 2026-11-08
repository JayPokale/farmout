# Review benchmark: one model vs all of them

Does asking every subscription to review a change catch more real bugs than asking the best one? This benchmark plants known bugs in small code changes, sends the exact `/modelmux:review` brief to each worker, and counts what each one finds on its own and what all of them find together.

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

### What this does and doesn't show

- **No recall gain over the best single model, on these cases.** Antigravity and Claude (Sonnet) each found all 23 planted bugs, so running all four found nothing more. Planted, well-known bug classes are within every 2026 frontier model's reach. A harder benchmark (real bug-fix commits) is future work.
- **Strong agreement.** Every planted bug was flagged by at least three of the four models, and 18 of 23 by all four. When `/modelmux:review` reports "flagged by 4/4", that has meant a real bug every time here.
- **Misses move around.** Codex missed different bugs in two runs of the same case, and Copilot missed others. The fan-out means you don't have to know which model is reliable this week.
- **Cost in time, not in Claude's tokens.** A parallel call is as slow as its slowest worker: Antigravity averaged about 3.4 minutes per review, while Claude (Sonnet) took about 11 seconds. Running everything produced 112 findings for 23 planted bugs, so the head's merge-and-verify step matters. Some extras are real issues the cases didn't plant (missing authorization, quota races), and some are noise.

### End to end

`results/2026-11-17/demo-review.md` is a real `/modelmux:review` run on `files_api`, from the plugin loaded with `--plugin-dir`. It shows each worker's raw findings and Claude's merged, verified table: all five planted bugs confirmed (four at 4/4 agreement), plus four real issues nobody planted. The run took 220s; Claude's own cost was $0.115.

## Head tokens: reading papers alone vs delegating

`head_tokens.py` runs one task twice with `claude -p` (Sonnet head, separate account): summarize three arXiv papers (Transformer, BERT, ResNet; 43 pages), giving method, datasets and headline number. Once the head reads the PDFs itself; once it may only call `delegate`, and Antigravity reads them. Numbers are the head's own usage from `--output-format json` (2026-11-17, one run each):

| head | total tokens | cache write | cache read | output | cost | time |
|---|---|---|---|---|---|---|
| Claude reads the PDFs | 48,097 | 34,316 | 12,816 | 959 | $0.148 | 13s |
| Claude + modelmux | 19,747 | 7,082 | 11,683 | 976 | $0.039 | 62s |

**59% fewer head tokens and 74% less head cost**, with the same correct headline numbers in both answers (28.4/41.8 BLEU, GLUE 80.5, 3.57% top-5 error). The trade is wall time: Antigravity took a minute where the head took 13 seconds. Both runs pay roughly 12k cached tokens of fixed overhead (system prompt, tools); most of the rest of the first run is the PDFs, so the gap grows with every page the head no longer reads. Full answers: `results/2026-11-17/head_tokens.md`.

## Reproduce

```bash
python3 bench/review_recall.py              # every enabled worker; spends one review per worker per case
python3 bench/review_recall.py codex copilot   # only these
HEAD_CONFIG_DIR=~/.claude-2 python3 bench/head_tokens.py papers/*.pdf
```

Limits: 14 bugs is a small sample, the cases are short, and line-window matching can credit a finding that cites the right line for the wrong reason (or miss one that cites the wrong line). Treat the numbers as a sanity check, not a leaderboard.
