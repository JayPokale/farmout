# The farmout store

Reviewed skills, hooks and tools for [farmout](../README.md). `farmout store ls` lists them, `farmout store show <name>`
prints an entry's files so you can read them, and `farmout store add <name>` installs it: every file is checked against
the SHA-256 in [index.json](index.json), and if any differs, nothing is installed.

| kind | installed to | what it is |
|---|---|---|
| skill | `~/.config/farmout/skills/<name>/` | a `SKILL.md` folder; `farmout <name>` or `farmout skill run <name>` |
| hook | `~/.config/farmout/hooks/<name>/` | a command farmout runs on its events (see the README's Hooks section) |
| tool | `~/.config/farmout/tools/<name>/` | a command: `farmout tool run <name>` |

Hooks and tools run code on your machine, so farmout takes them only from this store (or from files you put in
`~/.config/farmout/` yourself). Skills without code can come from anywhere: `farmout skill add <git-url|path|url>`.

## Adding an entry

1. Make `store/<kind>s/<name>/` with an `entry.json` (`kind`, `version`, `description`, plus `hook` with `events` and
   `command`, or `tool` with `command`; `{python}` and `{dir}` are filled in at install time) and the entry's files.
   Standard library only, no network calls unless that is the entry's purpose, and nothing that sends prompts or
   outputs anywhere.
2. Run `python3 scripts/build_store_index.py` to update `index.json`; the tests check the two agree.
3. Open a pull request. Every entry is read line by line before it is merged; bump `version` on every change.
