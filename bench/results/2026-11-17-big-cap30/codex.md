files_api.py:36 | high | path traversal permits arbitrary file reads | `name="/etc/passwd"` | reject absolute/traversal paths and enforce resolved path under owner directory
config.js:8 | high | user JSON can prototype-pollute configuration/global objects | `{"__proto__":{"isAdmin":true}}` | reject prototype keys and iterate own safe keys
files_api.py:18 | high | SQL injection exposes other owners’ file metadata | `owner="' OR 1=1 --"` | use a parameterized query
billing.py:7 | high | public hard-coded webhook secret allows forged accepted webhooks | deployment retains default SECRET | require secret from secure configuration and fail startup when absent
ratelimit.py:38 | high | concurrent first requests for one key create separate buckets and bypass rate limit | parallel requests for a new key | lock bucket lookup/creation
events.js:36 | high | 64-bit user IDs lose precision and can identify wrong users | ID above `9007199254740991` | retain IDs as strings/BigInt and parse safely
events.js:52 | high | delivery returns before saves complete and drops save failures as unhandled rejections | slow or failing `store.save` | await all saves/publishes and propagate failures
billing.py:12 | high | binary float conversion undercharges/overcharges invoice cents | prices such as `1.15` whose `amount * 100` is below integer | accept decimal strings and use `Decimal`
ratelimit.py:60 | high | retry silently converts persistent failures into success-like `None` | `fn` fails every attempt | re-raise the final exception
cache.js:30 | medium | TTL documented in seconds is compared against milliseconds | default 60-second TTL expires after ~60 ms | multiply TTL by 1000 or store milliseconds
files_api.py:27 | medium | first page skips its first 20 files | `page=1` with any files | use `(page - 1) * PAGE_SIZE`
events.js:44 | medium | month values are shifted one month forward | `monthStart(2026, 1)` returns February | pass `month - 1` to `Date.UTC`
billing.py:49 | medium | purging multiple expired sessions raises during dictionary iteration | two or more sessions expired | iterate over a copy or collect IDs first
cache.js:18 | medium | cache does not update recency on reads, so recently used entries are evicted | access oldest entry, then insert past capacity | move accessed entry to Map end
cache.js:43 | medium | `save` resolves before writing and does not propagate write errors | caller awaits `save` then process exits or write fails | return/await `fs.promises.writeFile`
events.js:17 | medium | first subscriber can never unsubscribe | unsubscribe callback for first subscription | splice when `i >= 0`
config.js:18 | medium | lexicographic sorting selects wrong numeric upload tier | tiers `[2, 10]`, bytes `1` | numeric-sort with `(a, b) => a - b`