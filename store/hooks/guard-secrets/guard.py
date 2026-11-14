"""farmout hook (TaskStart): block a task that would send a secrets file or a private key to a worker's provider.

Listed folders are searched too (up to 2,000 files). Exit 2 blocks the task, with the reason on stderr; if this hook
itself fails, it blocks too, so a broken guard never lets a task through. Extra name patterns:
GUARD_SECRETS_EXTRA="a*,b*".
"""
import fnmatch
import json
import os
import sys

PATTERNS = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
            "credentials*", "*.npmrc", ".pypirc", "*secret*", "*.kdbx", "service-account*.json"]
PATTERNS += [p for p in os.environ.get("GUARD_SECRETS_EXTRA", "").split(",") if p]


def secret(path):
    return any(fnmatch.fnmatch(os.path.basename(path).lower(), p) for p in PATTERNS)


def files(paths, limit=2000):
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
                for n in names:
                    limit -= 1
                    if limit < 0:
                        return
                    yield os.path.join(root, n)
        else:
            yield path


try:
    event = json.load(sys.stdin)
    hits = [f for f in files(event.get("files") or []) if secret(f)]
    leak = "PRIVATE KEY-----" in (event.get("prompt") or "")
except Exception as e:  # fail closed
    print(f"guard-secrets: couldn't check the task ({e}); blocking it to be safe", file=sys.stderr)
    sys.exit(2)
if hits:
    print(f"guard-secrets: {', '.join(hits[:5])} look like secrets; leave them out of the task", file=sys.stderr)
    sys.exit(2)
if leak:
    print("guard-secrets: the brief contains a private key", file=sys.stderr)
    sys.exit(2)
