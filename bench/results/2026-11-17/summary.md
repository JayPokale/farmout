| reviewer | billing (4) | events (5) | files_api (5) | lru_config (5) | ratelimit (4) | total (23) | findings | time |
|---|---|---|---|---|---|---|---|---|
| antigravity | 4 | 5 | 5 | 5 | 4 | 23 | 26 | 1016s |
| codex | 3 | 5 | 4 | 5 | 3 | 20 | 23 | 183s |
| claude | 4 | 5 | 5 | 5 | 4 | 23 | 40 | 57s |
| copilot | 3 | 5 | 5 | 5 | 3 | 21 | 23 | 134s |
| **all, in parallel** | 4 | 5 | 5 | 5 | 4 | **23** | 112 | 1016s |

Per bug (✓ = found):

| bug | antigravity | codex | claude | copilot |
|---|---|---|---|---|
| billing/float-cents | ✓ | ✓ | ✓ | ✓ |
| billing/timing-compare | ✓ | · | ✓ | ✓ |
| billing/redos | ✓ | ✓ | ✓ | · |
| billing/dict-mutation | ✓ | ✓ | ✓ | ✓ |
| events/index-zero | ✓ | ✓ | ✓ | ✓ |
| events/replace-once | ✓ | ✓ | ✓ | ✓ |
| events/int64-precision | ✓ | ✓ | ✓ | ✓ |
| events/month-offset | ✓ | ✓ | ✓ | ✓ |
| events/foreach-async | ✓ | ✓ | ✓ | ✓ |
| files_api/sql-injection | ✓ | ✓ | ✓ | ✓ |
| files_api/page-offset | ✓ | ✓ | ✓ | ✓ |
| files_api/path-traversal | ✓ | ✓ | ✓ | ✓ |
| files_api/missing-row | ✓ | ✓ | ✓ | ✓ |
| files_api/no-commit | ✓ | · | ✓ | ✓ |
| lru_config/ttl-units | ✓ | ✓ | ✓ | ✓ |
| lru_config/lru-recency | ✓ | ✓ | ✓ | ✓ |
| lru_config/unawaited-write | ✓ | ✓ | ✓ | ✓ |
| lru_config/prototype-pollution | ✓ | ✓ | ✓ | ✓ |
| lru_config/lexicographic-sort | ✓ | ✓ | ✓ | ✓ |
| ratelimit/wall-clock | ✓ | ✓ | ✓ | · |
| ratelimit/bucket-race | ✓ | ✓ | ✓ | ✓ |
| ratelimit/retry-count | ✓ | ✓ | ✓ | ✓ |
| ratelimit/swallowed-error | ✓ | · | ✓ | ✓ |
