"""Tab completion for bash, zsh and fish, made from farmout's own argument parser (so it always matches the
commands and options of this version): `farmout completion bash|zsh|fish`. Session names come from `farmout _names`
as you type."""
import argparse
import shlex

SESSIONS = {"session", "sessions"}  # positional arguments that name sessions
WORKER_OPTS = ("-w", "--worker", "--fallback")


def tree(parser):
    """{command: {"help", "opts": [(flags, takes_value, help)], "sessions": bool, "choices": [...], "subs": {...}}}"""
    out = {}
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    helps = {a.dest: a.help for a in sub._choices_actions} if sub else {}
    for name, sp in (sub.choices.items() if sub else ()):
        if name.startswith("_"):
            continue
        opts, sessions, choices = [], False, []
        for a in sp._actions:
            if isinstance(a, argparse._SubParsersAction) or isinstance(a, argparse._HelpAction):
                continue
            if a.option_strings:
                opts.append((a.option_strings, a.nargs != 0, a.help if isinstance(a.help, str) else ""))
            elif a.dest in SESSIONS:
                sessions = True
            elif a.choices:
                choices += list(a.choices)
        out[name] = {"help": helps.get(name) or "", "opts": opts, "sessions": sessions, "choices": choices,
                     "subs": tree(sp)}
    return out


def bash(t, workers):
    def words(xs):
        return shlex.quote(" ".join(xs))
    cases, subcases = [], []
    for cmd, c in t.items():
        flags = [f for flags, _, _ in c["opts"] for f in flags]
        cases.append(f"        {shlex.quote(cmd)}) opts={words(flags)}; args={words(c['choices'])}; "
                     f"names={int(c['sessions'])}; subs={words(c['subs'])} ;;")
        for s, sc in c["subs"].items():
            sflags = [f for flags, _, _ in sc["opts"] for f in flags]
            subcases.append(f"        {shlex.quote(cmd + ' ' + s)}) opts={words(sflags)} ;;")
    valued = sorted({f for c in t.values() for flags, v, _ in c["opts"] if v for f in flags}
                    | {f for c in t.values() for s in c["subs"].values() for flags, v, _ in s["opts"] if v for f in flags})
    return f"""# farmout completion for bash. Add to ~/.bashrc:  eval "$(farmout completion bash)"
_farmout() {{
    [ -n "${{ZSH_VERSION-}}" ] && setopt local_options ksh_arrays  # zsh: count COMP_WORDS from 0, as bash does
    local cur=${{COMP_WORDS[COMP_CWORD]}} prev=${{COMP_WORDS[COMP_CWORD-1]}} cmd=${{COMP_WORDS[1]}} sub=""
    local opts="" args="" names=0 subs=""
    COMPREPLY=()
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W {words(list(t))} -- "$cur"))
        return
    fi
    case "$cmd" in
{chr(10).join(cases)}
    esac
    if [ -n "$subs" ] && [ "$COMP_CWORD" -ge 3 ]; then
        sub=${{COMP_WORDS[2]}}
        case "$cmd $sub" in
{chr(10).join(subcases)}
        esac
    fi
    case "$prev" in
        {'|'.join(WORKER_OPTS)}) COMPREPLY=($(compgen -W {words(workers)} -- "$cur")); return ;;
        -C|-f|--file|-o|--out) COMPREPLY=($(compgen -f -- "$cur")); return ;;
        --attach) COMPREPLY=($(compgen -W "$(farmout _names 2>/dev/null)" -- "$cur")); return ;;
        {'|'.join(valued) or '--'}) return ;;
    esac
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$opts --help" -- "$cur"))
    elif [ -n "$subs" ] && [ "$COMP_CWORD" -eq 2 ]; then
        COMPREPLY=($(compgen -W "$subs" -- "$cur"))
    elif [ "$cmd" = run ] && [ "$COMP_CWORD" -eq 2 ]; then
        COMPREPLY=($(compgen -W {words(workers + ['auto'])} -- "$cur"))
    elif [ "$names" = 1 ]; then
        COMPREPLY=($(compgen -W "$(farmout _names 2>/dev/null) $args" -- "$cur"))
    elif [ -n "$args" ]; then
        COMPREPLY=($(compgen -W "$args" -- "$cur"))
    fi
}}
complete -o default -F _farmout farmout
"""


def zsh(t, workers):
    return ("# farmout completion for zsh. Add to ~/.zshrc, after compinit or your framework (oh-my-zsh, prezto):\n"
            "#   eval \"$(farmout completion zsh)\"\n"
            "(( $+functions[compdef] )) || { autoload -U +X compinit && compinit -C; }\n"
            "autoload -U +X bashcompinit && bashcompinit\n"
            + bash(t, workers).split("\n", 1)[1])


def fish(t, workers):
    def q(x):
        return "'" + str(x).replace("\\", "\\\\").replace("'", "\\'") + "'"
    out = ["# farmout completion for fish. Save as ~/.config/fish/completions/farmout.fish:",
           "#   farmout completion fish > ~/.config/fish/completions/farmout.fish",
           "complete -c farmout -f"]
    for cmd, c in t.items():
        out.append(f"complete -c farmout -n __fish_use_subcommand -a {q(cmd)} -d {q(c['help'][:80])}")
    seen = "__fish_seen_subcommand_from"
    for cmd, c in t.items():
        cond = f"{seen} {cmd}"
        for flags, takes, helptext in c["opts"]:
            out.append(opt_line(q, cond, flags, takes, helptext, workers))
        if c["sessions"]:
            out.append(f"complete -c farmout -n {q(cond)} -a '(farmout _names 2>/dev/null)'")
        if c["choices"]:
            out.append(f"complete -c farmout -n {q(cond)} -a {q(' '.join(c['choices']))}")
        if cmd == "run":
            out.append(f"complete -c farmout -n {q(cond)} -a {q(' '.join(workers + ['auto']))}")
        if c["subs"]:
            names = " ".join(c["subs"])
            for s, sc in c["subs"].items():
                out.append(f"complete -c farmout -n {q(f'{cond}; and not {seen} {names}')} -a {q(s)} -d {q((sc['help'] or '')[:80])}")
                for flags, takes, helptext in sc["opts"]:
                    out.append(opt_line(q, f"{cond}; and {seen} {s}", flags, takes, helptext, workers))
    return "\n".join(out) + "\n"


def opt_line(q, cond, flags, takes, helptext, workers):
    parts = [f"complete -c farmout -n {q(cond)}"]
    for f in flags:
        parts.append(f"-l {f[2:]}" if f.startswith("--") else f"-s {f[1:]}" if len(f) == 2 else f"-o {f[1:]}")
    if takes:
        parts.append("-r")
        if any(f in WORKER_OPTS for f in flags):
            parts.append(f"-a {q(' '.join(workers))}")
        elif any(f in ("-C", "-f", "--file", "-o", "--out") for f in flags):
            parts.append("-F")
    if helptext:
        parts.append(f"-d {q(helptext[:80])}")
    return " ".join(parts)


def script(shell, parser, workers):
    return {"bash": bash, "zsh": zsh, "fish": fish}[shell](tree(parser), list(workers))
