# Security

farmout runs other agents on your accounts, so its safety defaults are the product.

Report vulnerabilities privately at https://github.com/JayPokale/farmout/security/advisories/new. Do not open a public issue.

In scope, for example:

- a task without write access that can modify files or run commands
- task input that changes which flags farmout passes to a worker CLI
- the head session's credentials or environment leaking to a worker
- a way around `.farmout/settings` (a worker or a repository widening which workers, writes or web access are allowed)
- an isolated session or pipeline stage writing to your own tree before you apply its patch
- a session, run or worker name that reaches outside farmout's directories, or anything that makes `farmout stop` signal another process
- a store install that accepts a file whose SHA-256 differs from the index, a project's hooks running without `farmout trust` (or after its settings file changed), or `farmout skill add` taking code from a remote source

Out of scope: prompt injection that stays within a worker's read-only permissions (see the README's Safety section), and bugs in the vendor CLIs themselves; report those to the vendor. A malicious store entry is in scope as a report about the entry: tell us privately and we'll pull it.

Expect a reply within a week. Fixes ship in a patch release, with credit unless you prefer otherwise.
