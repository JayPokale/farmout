<!-- Small, focused PRs beat big rewrites. -->

## What and why

<!-- One or two lines: what changes, and why it needs to. -->

## Checklist

- [ ] `python3 -m unittest discover -s tests -v` passes
- [ ] `server.py` is still one file, standard library only, under 400 lines
- [ ] Read-only is still the default; new or changed worker flags are in the README Safety table
- [ ] Worker changes: OS and CLI versions tested, plus `tests/smoke.py` output below
- [ ] CHANGELOG entry under Unreleased

## Notes

<!-- Anything a reviewer should know: tradeoffs, deliberate simplifications, follow-ups. -->
