"""farmout tool: delete sessions with no turn in more than N days, with their worktrees and branches.

    farmout tool run prune [--days 14] [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
import time

p = argparse.ArgumentParser(prog="farmout tool run prune")
p.add_argument("--days", type=float, default=14)
p.add_argument("--dry-run", action="store_true")
a = p.parse_args()
farmout = [os.environ.get("FARMOUT_PYTHON", sys.executable), "-m", "farmout"]
rows = json.loads(subprocess.run(farmout + ["ls", "--all", "--json"], capture_output=True, text=True, check=True).stdout)
cutoff = time.time() - a.days * 86400
old = [r["name"] for r in rows if r["state"] not in ("running", "queued") and (r.get("updated") or r.get("created") or 0) < cutoff]
if not old:
    print(f"No sessions idle for more than {a.days:g} days.")
    sys.exit(0)
print(("Would delete: " if a.dry_run else "Deleting: ") + ", ".join(old))
if not a.dry_run:
    sys.exit(subprocess.run(farmout + ["rm", *old]).returncode)
