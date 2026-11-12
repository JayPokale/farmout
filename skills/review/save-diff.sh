#!/bin/sh
# Save the change under review for /modelmux:review, so Claude never needs a shell write.
# Usage: save-diff.sh [--base <ref>] [focus words...]   (focus words are ignored here)
set -e
base=""
if [ "$1" = "--base" ] && [ -n "$2" ]; then base="$2"; fi
case "$base" in -*) echo "--base needs a git ref, not an option: $base" >&2; exit 1 ;; esac
dir="$(git rev-parse --absolute-git-dir)/modelmux"
mkdir -p "$dir"
if [ -n "$base" ]; then
  git diff "$base...HEAD" > "$dir/review.diff"
else
  git diff HEAD > "$dir/review.diff"
fi
if [ -n "$base" ]; then target="$base...HEAD"; else target="uncommitted changes"; fi
echo "repository root: $(git rev-parse --show-toplevel)"
echo "diff file: $dir/review.diff ($(wc -c < "$dir/review.diff" | tr -d ' ') bytes; $target)"
if [ -z "$base" ]; then
  git ls-files --others --exclude-standard | sed 's/^/untracked file: /'
fi
