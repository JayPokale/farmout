#!/bin/sh
# Release check for the plugin's slash commands: each must run headless without a permission error.
# Loads the plugin from this checkout (--plugin-dir), spends a few Haiku tokens, changes nothing.
# Run from a normal terminal (not inside a Claude Code session):  sh tests/plugin_smoke.sh
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
repo=$(mktemp -d)
cd "$repo"
git init -q && echo x > f && git add f && git -c user.name=t -c user.email=t@localhost commit -qm init
for cmd in /modelmux:setup /modelmux:review; do
  out=$(claude -p "$cmd" --plugin-dir "$root" --model haiku --no-session-persistence < /dev/null 2>&1) || true
  case "$out" in
    *"requires approval"*|*"permission check failed"*|*"Unknown skill"*|*"Unknown command"*)
      echo "FAIL $cmd"; echo "$out"; exit 1 ;;
  esac
  echo "ok   $cmd: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-110)"
done
rm -rf "$repo"
