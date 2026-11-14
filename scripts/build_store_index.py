"""Build store/index.json from the store's folders: store/<kind>s/<name>/entry.json plus the entry's files.

    python3 scripts/build_store_index.py           write store/index.json
    python3 scripts/build_store_index.py --check   exit 1 if store/index.json is out of date
"""
import hashlib
import json
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "store"


def build():
    entries = []
    for kind in ("skill", "hook", "tool"):
        for d in sorted((STORE / f"{kind}s").iterdir()) if (STORE / f"{kind}s").is_dir() else []:
            spec = json.loads((d / "entry.json").read_text())
            if spec.get("kind") != kind:
                raise SystemExit(f"{d}/entry.json: kind must be {kind!r}")
            files = [{"path": str(f.relative_to(d)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                     for f in sorted(d.rglob("*")) if f.is_file() and f.name != "entry.json" and "__pycache__" not in f.parts]
            entries.append({"name": d.name, **spec, "dir": f"{kind}s/{d.name}", "files": files})
    return json.dumps({"version": 1, "entries": entries}, indent=1) + "\n"


if __name__ == "__main__":
    text, index = build(), STORE / "index.json"
    if "--check" in sys.argv:
        sys.exit(0 if index.exists() and index.read_text() == text else "store/index.json is out of date: "
                 "run python3 scripts/build_store_index.py")
    index.write_text(text)
    print(f"wrote {index} ({len(json.loads(text)['entries'])} entries)")
