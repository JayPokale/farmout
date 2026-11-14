#!/usr/bin/env python3
"""farmout's entry point for the Claude Code plugin and MCP clients; the code lives in the farmout/ package.

    python3 server.py              serve the MCP tools over stdio
    python3 server.py <command>    the farmout CLI, e.g. `python3 server.py doctor --live`

Register:  claude mcp add -s user farmout -- python3 /path/to/farmout/server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from farmout.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["mcp"]))
