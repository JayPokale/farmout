# Security

farmout runs other agents on your accounts, so its safety defaults are the product.

Report vulnerabilities privately at https://github.com/JayPokale/farmout/security/advisories/new. Do not open a public issue.

In scope, for example:

- a task without `write: true` that can modify files or run commands
- task input that changes which flags farmout passes to a worker CLI
- the head session's credentials or environment leaking to a worker

Out of scope: prompt injection that stays within a worker's read-only permissions (see the README's Safety section), and bugs in the vendor CLIs themselves; report those to the vendor.

Expect a reply within a week. Fixes ship in a patch release, with credit unless you prefer otherwise.
