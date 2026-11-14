---
name: test-gaps
description: Lists the behaviours in your uncommitted change that no existing test covers, most important first, with the test to add for each.
workers: [codex, claude]
diff: true
---
The first listed file is a unified diff of an uncommitted change. Read it, then the repository's tests that
exercise the changed code. List the behaviours the change adds or alters that no test checks, most important
first: edge cases, error paths, boundary values, concurrency, and any bug fix without a regression test.

One per line, exactly:
path:line | behaviour not covered | the test to add (name and what it asserts)

At most 12. If the change is fully covered, reply exactly: COVERED
$ARGUMENTS
