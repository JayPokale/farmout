files_api.py:18 | high | SQL injection exposes other owners’ file metadata | owner=`' OR 1=1 -- ` | use parameterized query  
files_api.py:36 | high | path traversal reads arbitrary readable files | owner or name contains `../` | resolve path and enforce it remains under owner storage directory  
config.js:8 | high | prototype pollution from JSON overrides | `{"__proto__":{"isAdmin":true}}` | reject dangerous keys and use own-key iteration  
billing.py:7 | high | known hard-coded webhook secret permits forged webhooks | deployment retains default secret | require secret from secure configuration; fail startup when absent  
ratelimit.py:38 | high | concurrent first requests create separate buckets, bypassing per-key limit | simultaneous `allow` calls for new key | lock bucket lookup/creation  
events.js:52 | high | returns success before saves/publishes complete; failures become unhandled | `store.save` rejects or caller proceeds after await | await all save/publish operations  
events.js:44 | high | every generated month is one month late | `monthStart(2026, 1)` returns February | pass `month - 1` to `Date.UTC`  
events.js:36 | high | 64-bit IDs lose precision and can target wrong user | ID exceeds `Number.MAX_SAFE_INTEGER` | preserve IDs as strings or BigInt  
cache.js:30 | medium | TTL documented as seconds is applied as milliseconds | default 60-second entry expires after 60 ms | compare against `this.ttl * 1000`  
billing.py:47 | medium | deleting multiple expired sessions crashes during dictionary iteration | two or more sessions expired | iterate over a snapshot of items/keys