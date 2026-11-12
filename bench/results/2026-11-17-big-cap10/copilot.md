files_api.py:17 | high | SQL injection in the owner filter | attacker-controlled owner contains SQL syntax | use a parameterized query
files_api.py:36 | high | path traversal allows reading arbitrary files | owner or name contains `../` or an absolute path | validate components and enforce the resolved path stays under STORAGE
billing.py:23 | high | all webhook signatures use a hardcoded public secret | production deployment leaves SECRET unchanged | load a strong secret from protected configuration and compare with constant-time verification
events.js:45 | high | delivery returns before saves and publishes finish, while save failures become unhandled rejections | any asynchronous store.save operation | await Promise.all or sequential saves and propagate failures
billing.py:46 | high | deleting expired sessions mutates the dictionary during iteration and raises RuntimeError | purge_expired finds any expired session | iterate over a snapshot or collect keys before deleting
billing.py:10 | medium | decimal amounts can be truncated by binary floating-point representation | values such as 19.99 produce 1998.999... | use Decimal or integer/string monetary inputs with explicit rounding
events.js:33 | medium | monthStart returns the following month | valid month values such as month=1 | pass month - 1 to Date.UTC
cache.js:38 | medium | TTL is interpreted as milliseconds instead of seconds | default ttlSeconds=60 expires entries after about 60 ms | convert ttlSeconds to milliseconds before comparing
config.js:19 | medium | numeric tiers are sorted lexicographically | tiers such as [20, 100] | sort with `(a, b) => a - b`
files_api.py:26 | medium | page 1 skips the first page of files | any request with page=1 | calculate the offset as `(page - 1) * PAGE_SIZE`