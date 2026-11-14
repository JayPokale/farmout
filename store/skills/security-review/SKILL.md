---
name: security-review
description: Every worker reviews your change for security problems only (injection, auth, secrets, unsafe deserialization, path traversal, SSRF, XSS); findings are merged with how many models agree.
mode: review
---
Look only for security problems that an attacker or a careless caller could trigger:

- injection: SQL, shell, template, LDAP, header, log
- authentication and authorization: missing checks, checks on the wrong object, privilege escalation, IDOR
- secrets: keys, tokens or passwords in code, config, logs or error messages
- unsafe deserialization, path traversal, SSRF, XSS, open redirects, XXE
- weak randomness or crypto for security purposes; timing-unsafe comparisons of secrets
- missing validation where untrusted input crosses a trust boundary

For each finding, say who controls the input. Ignore everything that isn't security.
$ARGUMENTS
