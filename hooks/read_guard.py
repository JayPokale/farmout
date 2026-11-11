"""modelmux read guard: a Claude Code PreToolUse hook for the Read tool.

The first time Claude tries to read a big PDF or text file in a session, it is told how much that costs
and asked to delegate the reading to a modelmux worker instead. A second attempt at the same file goes
through, so "read it yourself" always works. Set MODELMUX_READ_GUARD=0 to turn it off.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PDF_PAGES = 10  # a "big" PDF; Claude Code reads PDFs at roughly 1.5-3k tokens a page
PEEK_PAGES = 5  # reading this many pages or fewer of a big PDF is a cheap look, never stopped
TEXT_BYTES = 200_000  # a "big" text file, about 50k tokens
PEEK_LINES = 500


def pdf_pages(path):
    with open(path, "rb") as f:
        return len(re.findall(rb"/Type\s*/Page[^s]", f.read()))


def span(pages):
    """Number of pages in a Read `pages` argument such as "1-5", "7" or "1-3,8"."""
    total = 0
    for part in str(pages).split(","):
        lo, _, hi = part.strip().partition("-")
        total += int(hi or lo) - int(lo) + 1 if lo.strip().isdigit() and (hi or lo).strip().isdigit() else 0
    return total


def main():
    if os.environ.get("MODELMUX_READ_GUARD") == "0":
        return
    event = json.load(sys.stdin)
    if event.get("tool_name") != "Read":
        return
    args = event.get("tool_input") or {}
    path = Path(args.get("file_path") or "")
    if not path.is_file():
        return
    if path.suffix.lower() == ".pdf":
        size = pdf_pages(path)
        want = span(args["pages"]) if args.get("pages") else size
        if size <= PDF_PAGES or want <= PEEK_PAGES:
            return
        what = f"a {size}-page PDF; reading {want} pages costs very roughly {want * 2}k tokens"
    else:
        size = path.stat().st_size
        if size <= TEXT_BYTES or (args.get("limit") or PEEK_LINES + 1) <= PEEK_LINES:
            return
        what = f"{size // 1000} KB of text (about {size // 4000}k tokens)"
    seen = Path(tempfile.gettempdir()) / f"modelmux-read-guard-{event.get('session_id', 'none')}"
    asked = seen.read_text().splitlines() if seen.exists() else []
    if str(path) in asked:  # second attempt: the user or Claude really wants it read here
        return
    with open(seen, "a") as f:
        f.write(f"{path}\n")
    reason = (f"modelmux read guard: {path} is {what}. Unless you need its exact text in this context, delegate "
              f"the reading: call the modelmux delegate tool with a task whose files list this path (antigravity "
              f"reads PDFs natively; its model gemini-3.8-flash-low is several times faster) and whose prompt says "
              f"exactly what to extract. If you do need the full text "
              f"yourself, read it again and the guard will let it through.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
