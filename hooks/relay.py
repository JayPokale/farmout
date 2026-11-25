"""farmout's limit relay as a Claude Code UserPromptSubmit hook: at Claude's usage limit, what you type goes to
another subscription (once `farmout relay on`), and Claude catches up when it's back. See farmout/relay.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from farmout.relay import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
