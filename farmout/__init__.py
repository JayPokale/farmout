"""farmout: hand work to the AI subscriptions you already pay for, from a terminal or from any MCP client."""
import os as _os
import sys as _sys

VERSION = "0.1.0"

if _os.name == "nt":  # worktree locks, process groups and signals are POSIX; say so instead of an ImportError
    _sys.exit("farmout runs on Linux and macOS. On Windows, run it inside WSL.")
