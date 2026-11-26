#!/usr/bin/env python3
"""Builds the animated README art in assets/readme/: a -dark and a -light SVG per panel (the README picks one with
<picture>). Plain SVG + SMIL, so it animates inside GitHub's <img> sandbox with no scripts and no web fonts. Every
static attribute holds the final frame, so a renderer without SMIL (PyPI's page, an image viewer) shows the result.

Colours follow the site and the logo: a colour always means a worker (codex orange, antigravity teal, copilot violet,
claude coral, deepseek blue); farmout itself is bone, or all four lanes at once. Terminals stay dark in both themes.

The figures mirror the README and bench/README.md; change them together. Review panel: the real run in
scripts/demo.json. Relay panel: the messages of a real relay run (Claude Code 2.1.323 and Codex; the limit was staged
for the recording). Race panel: an illustration in the real output format, and it says so.

Run: python3 scripts/build_readme_art.py
"""
import math
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "readme"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
ADV = 0.6  # monospace advance per px of font size; the mono layouts assume it

THEMES = {
    "dark": {
        "bg": "#0b0c11", "card": "#13151d", "line": "#242734", "text": "#ece8df", "soft": "#c9c5bc", "dim": "#9a9eaf",
        "faint": "#5f6375", "dot": "#1b1e28", "track": "#1e2029", "glow": 0.16, "pass": "#7bd88f", "fail": "#f47c7c",
        "codex": "#f2a65a", "agy": "#5ec4b6", "copilot": "#a78bfa", "claude": "#f47c7c", "deepseek": "#6f8bff",
        "api": "#c9c5bc",
    },
    "light": {
        "bg": "#f7f5f0", "card": "#fdfcf9", "line": "#dbd6ca", "text": "#15171f", "soft": "#2b2e3b", "dim": "#575b69",
        "faint": "#8c8f9a", "dot": "#e5e0d5", "track": "#ebe7de", "glow": 0.10, "pass": "#1d8a45", "fail": "#c4433e",
        "codex": "#b4621b", "agy": "#0f8478", "copilot": "#6a4fd0", "claude": "#c4433e", "deepseek": "#3550d8",
        "api": "#5b5e6b",
    },
}
TERM = {  # the terminal, the same in both themes
    "bg": "#08090d", "line": "#22252f", "fg": "#ece8df", "soft": "#c9c5bc", "dim": "#8b8fa3", "faint": "#5f6375",
    "band": "#191b23", "pass": "#7bd88f", "fail": "#f47c7c", "warn": "#e8c36a", "code": "#9aa7ff",
    "codex": "#f2a65a", "agy": "#5ec4b6", "copilot": "#a78bfa", "claude": "#f47c7c", "deepseek": "#6f8bff",
    "api": "#c9c5bc",
}
KEY = {"codex": "codex", "antigravity": "agy", "copilot": "copilot", "claude": "claude", "deepseek": "deepseek",
       "api": "api"}

# Text inside the art, per README language. Commands, CLI output and code stay English everywhere; only the prose
# around them is translated.
I18N = {
    "en": {
        "pill": "CLI · MCP server · Claude Code plugin · 0 deps",
        "tag1": "Hit your limit less.", "tag2": "Keep working when you do.",
        "accounts": {"codex": "ChatGPT plan", "agy": "Google account", "copilot": "Copilot plan",
                     "claude": "a second login"},
        "statsTitle": "Measured in farmout's own benchmarks · raw results committed",
        "tiles": [("Claude's own tokens", "3 papers read by another plan; cost −74%"),
                  ("bugs caught by 3+ of 4", "23 planted, 9 real; 27 by all four"),
                  ("kinds of worker", "Codex, Gemini, Copilot, Claude, DeepSeek, local"),
                  ("dependencies", "and no API keys: your CLIs, your logins")],
        "relayTitle": "At Claude Code's limit, keep typing.",
        "relayFoot": "Real Claude Code and Codex output; the limit was staged for the recording.",
        "reset": "04:39 · the limit resets",
        "reviewTitle": "Four subscriptions review one change.", "reviewNote": "a real run, replayed",
        "raceTitle": "Same task, every model. Your tests pick the winner.",
        "raceFoot": "Illustration in farmout's real output format: the winner passes the tests with the smallest diff.",
        "benchTitle": "Which subscription finds the bugs?",
        "benchSub": "32 bugs: 23 planted, 9 real regressions from open-source projects",
        "benchFoot": "On your own code: farmout bench replays your past bug fixes; --apply makes the winners the default.",
        "benchTime": "time per review",
        "treeTitle": "Every conversation is a tree.", "treeNote": "branch any turn, onto any model",
        "keys": [("c", "continue"), ("b", "branch off this turn"), ("e", "ask it again, differently"),
                 ("/", "find in every answer"), ("Y", "copy a command, whole"), ("C", "carry on your own CLI session")],
        "keysTitle": "farmout ui",
        "pipeTitle": "Pipelines: each stage starts when the last one ends.",
        "stages": [("review", "4 models review your change"), ("fix", "codex fixes what 2+ agree on, in a worktree"),
                   ("check", "4 models review the fix")],
        "ready": "patch ready", "pipeFoot": "Write your own: stages, gates, approvals, parallel foreach.",
        "hubTitle": "Drive it from wherever you work.", "hubNote": "CLI · MCP · ACP",
        "heads": [("terminal", "farmout <command>"), ("Claude Code", "plugin: MCP, slash commands"),
                  ("Codex · Copilot CLI", "MCP"), ("Zed · JetBrains · Neovim", "ACP"), ("any MCP client", "stdio")],
        "workers": [("codex", "ChatGPT plan"), ("antigravity", "Google account"), ("copilot", "Copilot plan"),
                    ("claude", "a second login"), ("deepseek", "API key"), ("api", "Ollama · vLLM · OpenRouter")],
        "headsLabel": "where you type", "workersLabel": "what you already pay for",
        "sheet": "cheat sheet",
        "notes": ["every model reviews your change", "your tests pick the winner", "a conversation that survives limits",
                  "carry it on with another model", "keep typing at Claude Code's limit",
                  "carry on your own Claude Code session", "the best model on your code becomes the default",
                  "every conversation as a graph", "review → fix → check the fix",
                  "inside Claude Code: merged and verified"],
        "stripTitle": "farmout is free. Testing it on every subscription isn't.",
        "stripSub": "Saved you a limit? A coffee pays for the next test run.",
        "free": "farmout is free.", "notFree": "Testing it on every subscription isn't.",
        "body1": "It's tested on real, paid subscriptions.",
        "body2": "Saved you a limit? A coffee pays for the next run.", "button": "☕ Buy me a coffee",
    },
}
L = I18N["en"]  # the language being drawn; the main loop switches it

WIDE = re.compile("[ᄀ-ᇿ⺀-鿿가-힯豈-﫿＀-￯]")


def text_w(s, fs, mono=False):
    """Rough rendered width: CJK and Hangul take about 1em, Latin about 0.55em in sans and 0.6em in mono."""
    return sum(fs * (1 if WIDE.match(c) else ADV if mono else 0.55) for c in str(s))


def fit(s, fs, maxw, mono=False):
    """The font size, at most fs, at which s fits in maxw."""
    return min(fs, math.floor(fs * maxw / max(text_w(s, fs, mono), 1) * 10) / 10)


def wrap(s, fs, maxw, mono=False):
    """s broken into lines that fit maxw at size fs."""
    words = re.findall(r"[\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]|[^\s\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+\s*|\s+", str(s))
    lines, cur = [], ""
    for w in words:
        if cur and text_w((cur + w).rstrip(), fs, mono) > maxw:
            lines.append(cur.rstrip())
            cur = w.lstrip()
        else:
            cur += w
    return lines + [cur.rstrip()] if cur.strip() else lines


def num(v):
    if isinstance(v, str):
        return v
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def mono(size):
    return f' font-family="{MONO}" font-size="{num(size)}"'


def anim(attr, pts, dur, ease=False, discrete=False, kind=None):
    """One looping timeline. pts: [(seconds, value)] in time order; the first and last values hold to the ends."""
    p = list(pts)
    if p[0][0] > 0:
        p.insert(0, (0, p[0][1]))
    if p[-1][0] < dur:
        p.append((dur, p[-1][1]))
    tag = "animateTransform" if kind else "animate"
    a = (f'<{tag} attributeName="{attr}"' + (f' type="{kind}"' if kind else "")
         + f' values="{";".join(num(v) for _, v in p)}" keyTimes="{";".join(f"{x / dur:.4f}" for x, _ in p)}"'
         + f' dur="{num(dur)}s" repeatCount="indefinite"')
    if discrete:
        a += ' calcMode="discrete"'
    elif ease:
        a += ' calcMode="spline" keySplines="' + ";".join(["0.4 0 0.2 1"] * (len(p) - 1)) + '"'
    return a + "/>"


def shown(t0, t1, dur, f=0.25):
    """Visible from t0 to t1 of each loop."""
    return anim("opacity", [(t0, 0), (t0 + f, 1), (t1 - f, 1), (t1, 0)], dur)


def until(t1, dur, f=0.3):
    """Visible from the start of the loop to t1 (an element revealed some other way, faded out with the loop)."""
    return anim("opacity", [(0, 1), (t1, 1), (t1 + f, 0)], dur)


def enter(delay):
    """Fade and rise in once, `delay` seconds after load."""
    k = f"{delay / (delay + 0.6):.3f}"
    return (f'<animate attributeName="opacity" values="0;0;1" keyTimes="0;{k};1" dur="{num(delay + 0.6)}s" '
            f'fill="freeze"/><animateTransform attributeName="transform" type="translate" values="0 10;0 10;0 0" '
            f'keyTimes="0;{k};1" dur="{num(delay + 0.6)}s" fill="freeze" calcMode="spline" '
            f'keySplines="0 0 1 1;0.2 0.7 0.2 1"/>')


def svg(t, w, h, label, body, defs=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{SANS}" xml:space="preserve" role="img" aria-label="{esc(label)}">\n'
            + (f"<defs>{defs}</defs>\n" if defs else "")
            + f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="14" fill="{t["bg"]}" stroke="{t["line"]}"/>\n'
            + "\n".join(body) + "\n</svg>\n")


def lanes_grad(t, gid="lanes"):
    return (f'<linearGradient id="{gid}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{t["codex"]}"/>'
            f'<stop offset="0.36" stop-color="{t["claude"]}"/><stop offset="0.68" stop-color="{t["copilot"]}"/>'
            f'<stop offset="1" stop-color="{t["agy"]}"/></linearGradient>')


def blur(w, h, sd=3, fid="blur"):
    # user-space region: a straight lane has a zero-height box, and a box-relative filter would hide it
    return (f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">'
            f'<feGaussianBlur stdDeviation="{sd}"/></filter>')


def pulse(d, colour, t0, t1, dur, width=2.5):
    """A spark of light running along path d from t0 to t1; off the path (unseen) otherwise and when static."""
    off = anim("stroke-dashoffset", [(t0, 20), (t1, -110)], dur)
    base = (f'd="{d}" pathLength="100" fill="none" stroke="{colour}" stroke-linecap="round" '
            f'stroke-dasharray="12 300" stroke-dashoffset="20"')
    return (f'<path {base} stroke-width="{num(width * 3.2)}" stroke-opacity="0.45" filter="url(#blur)">{off}</path>'
            f'<path {base} stroke-width="{num(width)}">{off}</path>')


def win(x, y, w, h, title):
    """A terminal window: card, title bar, traffic lights, centred title."""
    T = TERM
    return (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="10" fill="{T["bg"]}" '
            f'stroke="{T["line"]}"/><path d="M{num(x)} {num(y + 32)}H{num(x + w)}" stroke="{T["line"]}"/>'
            + "".join(f'<circle cx="{num(x + 18 + i * 16)}" cy="{num(y + 16)}" r="5" fill="{c}"/>'
                      for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")))
            + f'<text x="{num(x + w / 2)}" y="{num(y + 20.5)}"{mono(12)} fill="{T["dim"]}" '
              f'text-anchor="middle">{esc(title)}</text>')


def spans(segs):
    return "".join(f'<tspan fill="{s[1]}"' + (' font-weight="700"' if len(s) > 2 and s[2] else "")
                   + f">{esc(s[0])}</tspan>" for s in segs)


def typed(uid, x, y, segs, fs, t0, t1, dur):
    """segs [(text, colour)] typed at (x, y) from t0 to t1 behind a caret; the static frame holds the whole line."""
    w = sum(len(s[0]) for s in segs) * fs * ADV
    return (f'<clipPath id="{uid}"><rect x="{num(x)}" y="{num(y - fs)}" width="{num(w + 2)}" height="{num(fs * 1.5)}">'
            + anim("width", [(t0, 0), (t1, w + 2)], dur) + "</rect></clipPath>"
            + f'<text x="{num(x)}" y="{num(y)}"{mono(fs)} clip-path="url(#{uid})">{spans(segs)}</text>'
            + f'<rect x="{num(x + w)}" y="{num(y - fs + 1)}" width="{num(fs * ADV)}" height="{num(fs + 3)}" '
              f'fill="{TERM["soft"]}" opacity="0">'
            + anim("x", [(t0, x), (t1, x + w)], dur)
            + anim("opacity", [(t0 - 0.01, 0), (t0, 0.75), (t1 + 0.3, 0.75), (t1 + 0.31, 0)], dur) + "</rect>")


def printed(x, y, segs, fs, at, dur, end=None):
    """A line that appears at `at` (and, given `end`, goes at `end`; it is then hidden in the static frame)."""
    a = anim("opacity", [(at, 0), (at + 0.15, 1)], dur) if end is None else shown(at, end, dur, 0.15)
    return (f'<text x="{num(x)}" y="{num(y)}"{mono(fs)}' + (' opacity="0"' if end is not None else "")
            + f">{spans(segs)}{a}</text>")


def heading(t, title, note="", w=880, y=40):
    out = f'<text x="28" y="{y}" font-size="{fit(title, 19, 560 if note else 820)}" font-weight="700" fill="{t["text"]}">{esc(title)}</text>'
    if note:
        out += (f'<text x="{w - 28}" y="{y}" font-size="{fit(note, 13, 260)}" fill="{t["dim"]}" '
                f'text-anchor="end">{esc(note)}</text>')
    return out


# ── The logo, as the site draws it: a bone line fanning out into four coloured lanes ─────────────────────────────
LOGO = {"codex": 140, "agy": 218, "copilot": 294, "claude": 372}


def logo_path(k):
    y, c = LOGO[k], 256 if k in ("codex", "claude") else 250
    return f"M104 256H196C{c} 256 262 {y} 326 {y}H384"


def mark(t, x, y, s, sparks=(), dur=None):
    """The logo without its tile, its root dot at (x, y), scaled by s (it is 512 units square). `sparks`: the
    times a spark of light runs all four lanes."""
    o = [f'<g transform="translate({num(x - 104 * s)} {num(y - 256 * s)}) scale({s})" fill="none" '
         f'stroke-linecap="round">']
    o += [f'<path d="{logo_path(k)}" stroke="{t[k]}" stroke-width="26"/>' for k in LOGO]
    o.append(f'<path d="M104 256H204" stroke="{t["text"]}" stroke-width="26"/>'
             f'<circle cx="104" cy="256" r="40" fill="{t["text"]}" stroke="none"/>')
    for i, (k, ly) in enumerate(LOGO.items()):
        grow = "".join(anim("r", [(s0 + 0.5 + i * 0.06, 30), (s0 + 0.7 + i * 0.06, 40), (s0 + 1.0 + i * 0.06, 30)], dur)
                       for s0 in sparks[:1]) if sparks else ""
        o.append(f'<circle cx="404" cy="{ly}" r="30" fill="{t[k]}" stroke="none">{grow}</circle>')
    for s0 in sparks:
        for i, k in enumerate(LOGO):
            off = anim("stroke-dashoffset", [(s0 + i * 0.05, 20), (s0 + 0.6 + i * 0.05, -110)], dur)
            o.append(f'<path d="{logo_path(k)}" pathLength="100" stroke="#ffffff" stroke-opacity="0.6" '
                     f'stroke-width="14" stroke-dasharray="14 300" stroke-dashoffset="20">{off}</path>')
    return "".join(o) + "</g>"


# ── Banner: one change reviewed by four subscriptions; then a turn at Claude's limit goes on on Codex ─────────────
def banner(t):
    W, H, D = 1200, 420, 15
    defs = (f'<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.3" '
            f'fill="{t["dot"]}"/></pattern>'
            f'<radialGradient id="glowA" cx="0.8" cy="0.15" r="0.6"><stop offset="0" stop-color="{t["copilot"]}" '
            f'stop-opacity="{t["glow"]}"/><stop offset="1" stop-color="{t["copilot"]}" stop-opacity="0"/></radialGradient>'
            f'<radialGradient id="glowB" cx="0.98" cy="1" r="0.55"><stop offset="0" stop-color="{t["codex"]}" '
            f'stop-opacity="{t["glow"]}"/><stop offset="1" stop-color="{t["codex"]}" stop-opacity="0"/></radialGradient>'
            + lanes_grad(t) + blur(W, H))
    o = [f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="14" fill="url(#{g})"/>' for g in ("dots", "glowA", "glowB")]

    # Left: the pitch.
    pill = L["pill"]
    o += [f'<rect x="64" y="50" width="{num(text_w(pill, 13, True) + 44)}" height="30" rx="15" fill="{t["card"]}" '
          f'stroke="{t["line"]}"/>',
          f'<circle cx="81" cy="65" r="4" fill="{t["pass"]}">{anim("opacity", [(0, 1), (1, 0.3), (2, 1)], 2)}</circle>',
          f'<text x="93" y="69.5"{mono(13)} fill="{t["dim"]}">{esc(pill)}</text>',
          mark(t, 74, 160, 0.19, sparks=(1.3, 8.3), dur=D),
          f'<text x="150" y="194" font-size="96" font-weight="800" letter-spacing="-3" fill="{t["text"]}">farmout</text>',
          f'<text x="64" y="260" font-size="{fit(L["tag1"], 31, 500)}" font-weight="700" fill="{t["text"]}">'
          f'{esc(L["tag1"])}</text>',
          f'<text x="64" y="302" font-size="{fit(L["tag2"], 31, 500)}" font-weight="800" fill="url(#lanes)">'
          f'{esc(L["tag2"])}</text>']
    cmd = "pipx install git+https://github.com/JayPokale/farmout"
    o += [f'<rect x="64" y="330" width="{num(text_w("$ " + cmd, 13.5, True) + 36)}" height="44" rx="10" '
          f'fill="{t["card"]}" stroke="{t["line"]}"/>',
          f'<text x="82" y="357"{mono(13.5)}><tspan fill="{t["faint"]}">$</tspan><tspan fill="{t["text"]}"> '
          f'{esc(cmd)}</tspan></text>']

    # Right: the command, and the lanes it fans out to.
    X0, T = 590, TERM
    o.append(win(X0, 32, 580, 112, "~/app"))
    o.append(f'<g opacity="0">{until(6.6, D)}'
             + typed("c1", X0 + 20, 90, [("$ ", T["faint"]), ("farmout review", T["fg"])], 14, 0.4, 1.1, D) + "</g>")
    o.append(printed(X0 + 20, 118, [("✓ ", T["pass"]), ("9 findings, merged", T["fg"]),
                                    (" · 4 flagged by all four models", T["dim"])], 12.5, 5.0, D, end=6.9))
    o.append(f'<g>{anim("opacity", [(0, 1), (14.2, 1), (14.6, 0)], D)}'
             + typed("c2", X0 + 20, 90, [("$ ", T["faint"]), ('farmout send cache "now fix it"', T["fg"])], 14, 7.1,
                     7.9, D) + "</g>")
    o.append(f'<text x="{X0 + 20}" y="118"{mono(12)}>{spans([("claude", T["claude"]), (" is at its limit till 15:00 → ", T["dim"]), ("codex", T["codex"]), (" answered, conversation and all", T["fg"])])}'
             + shown(12.0, 14.5, D) + "</text>")

    ys = {"codex": 186, "agy": 244, "copilot": 302, "claude": 360}
    RX, RY, FX, CX, CW = 626, 273, 706, 900, 262
    root = f"M{RX} 146V{RY - 12}Q{RX} {RY} {RX + 12} {RY}H{FX}"
    o += [f'<path d="{root}" fill="none" stroke="{t["text"]}" stroke-opacity="0.5" stroke-width="2"/>',
          f'<circle cx="{RX}" cy="{RY}" r="6" fill="{t["text"]}"/>',
          f'<text x="{RX}" y="{RY + 26}"{mono(11.5)} fill="{t["faint"]}" text-anchor="middle">farmout</text>',
          pulse(root, t["text"], 1.25, 1.9, D), pulse(root, t["text"], 8.25, 8.9, D)]
    names = {"codex": "codex", "agy": "antigravity", "copilot": "copilot", "claude": "claude"}
    done = {"claude": (3.3, "9 findings", "15s"), "agy": (3.8, "8 findings", "25s"),
            "copilot": (4.1, "5 findings", "29s"), "codex": (4.5, "5 findings", "36s")}
    for i, (k, y) in enumerate(ys.items()):
        d = f"M{FX} {RY}C{FX + 70} {RY} {FX + 80} {y} {FX + 150} {y}H{CX}"
        dim = anim("opacity", [(9.6, 1), (9.9, 0.3), (14.2, 0.3), (14.5, 1)], D) if k == "claude" else ""
        o.append(f'<path d="{d}" fill="none" stroke="{t[k]}" stroke-width="2.5" stroke-opacity="0.65"'
                 + (' opacity="0.3"' if k == "claude" else "") + f">{dim}</path>")
        o.append(pulse(d, t[k], 1.9 + i * 0.05, 2.5 + i * 0.05, D))
        stroke = anim("stroke", [(0, t["line"]), (9.5, t["fail"]), (14.4, t["line"])], D, discrete=True) if k == "claude" else ""
        o += [f'<rect x="{CX}" y="{y - 24}" width="{CW}" height="48" rx="10" fill="{t["card"]}" '
              f'stroke="{t["fail"] if k == "claude" else t["line"]}">{stroke}</rect>',
              f'<circle cx="{CX + 18}" cy="{y}" r="5" fill="{t[k]}"/>',
              f'<text x="{CX + 32}" y="{y - 3}"{mono(14)} font-weight="700" fill="{t[k]}">{names[k]}</text>',
              f'<text x="{CX + 32}" y="{y + 14}" font-size="{fit(L["accounts"][k], 11.5, 84)}" fill="{t["faint"]}">'
              f'{esc(L["accounts"][k])}</text>']
        at, what, secs = done[k]
        sx = CX + CW - 14
        o.append(f'<text x="{sx}" y="{y + 5}"{mono(12.5)} fill="{t["dim"]}" text-anchor="end" opacity="0">reviewing…'
                 + shown(2.45 + i * 0.05, at, D, 0.15) + "</text>")
        o.append(f'<text x="{sx}" y="{y + 5}"{mono(12.5)} text-anchor="end" opacity="0">'
                 + spans([("✓ ", t["pass"]), (what, t["text"]), (" " + secs, t["faint"])]) + shown(at, 6.9, D, 0.15) + "</text>")
    o.append(pulse(f"M{FX} {RY}C{FX + 70} {RY} {FX + 80} 360 {FX + 150} 360H{CX}", t["claude"], 8.9, 9.5, D))
    o.append(pulse(f"M{FX} {RY}C{FX + 70} {RY} {FX + 80} 186 {FX + 150} 186H{CX}", t["codex"], 10.0, 10.6, D))
    sx = CX + CW - 14
    o.append(f'<text x="{sx}" y="365"{mono(12.5)} fill="{t["fail"]}" text-anchor="end">limit · till 15:00'
             + anim("opacity", [(9.5, 0), (9.7, 1), (14.2, 1), (14.5, 0)], D) + "</text>")
    o.append(f'<text x="{sx}" y="191"{mono(12.5)} fill="{t["dim"]}" text-anchor="end" opacity="0">answering…'
             + shown(10.5, 11.7, D, 0.15) + "</text>")
    o.append(f'<text x="{sx}" y="191"{mono(12.5)} text-anchor="end">' + spans([("✓ ", t["pass"]), ("answered", t["text"])])
             + anim("opacity", [(11.6, 0), (11.8, 1), (14.2, 1), (14.5, 0)], D) + "</text>")
    hop = f"M{CX + CW + 4} 360C{CX + CW + 30} 360 {CX + CW + 30} 186 {CX + CW + 4} 186"
    o.append(f'<path d="{hop}" pathLength="100" fill="none" stroke="{t["dim"]}" stroke-width="1.6" '
             f'stroke-dasharray="100 100" stroke-dashoffset="0">'
             + anim("stroke-dashoffset", [(9.9, 100), (10.5, 0), (14.2, 0), (14.5, 100)], D) + "</path>")
    o.append(f'<path d="M{CX + CW + 11} 181L{CX + CW + 4} 186L{CX + CW + 11} 191" fill="none" stroke="{t["dim"]}" '
             f'stroke-width="1.6" stroke-linecap="round">' + anim("opacity", [(10.4, 0), (10.5, 1), (14.2, 1), (14.5, 0)], D)
             + "</path>")
    return svg(t, W, H, "farmout: hit your limit less, keep working when you do. Animated: farmout review sends one "
               "change to codex, antigravity, copilot and claude at once and merges their findings; then a turn "
               "sent to a conversation on claude, which is at its usage limit, is answered by codex with the "
               "conversation so far.", o, defs)


# ── Stats: four headline numbers ─────────────────────────────────────────────────────────────────────────────────
def stats(t):
    W, H, D = 880, 280, 6
    tw, gap, y0, th, bw = 196, 16, 56, 200, 160
    x0 = (W - 4 * tw - 3 * gap) / 2
    defs = lanes_grad(t)
    o = [f'<text x="{W / 2}" y="34" font-size="{fit(L["statsTitle"], 14, 820)}" fill="{t["dim"]}" '
         f'text-anchor="middle">{esc(L["statsTitle"])}</text>']
    values = ["−59%", "32/32", "6", "0"]
    for i, (v, (label, sub)) in enumerate(zip(values, L["tiles"])):
        x = x0 + i * (tw + gap)
        vx, vy = x + 18, y0 + 158
        g = [f'<clipPath id="tile{i}"><rect x="{num(x)}" y="{y0}" width="{tw}" height="{th}" rx="10"/></clipPath>',
             f'<rect x="{num(x)}" y="{y0}" width="{tw}" height="{th}" rx="10" fill="{t["card"]}" stroke="{t["line"]}"/>',
             f'<rect x="{num(x)}" y="{y0}" width="{tw}" height="3" fill="url(#lanes)" clip-path="url(#tile{i})"/>',
             f'<text x="{num(x + 18)}" y="{y0 + 62}" font-size="42" font-weight="800" letter-spacing="-1" '
             f'fill="{t["text"]}">{v}</text>',
             f'<text x="{num(x + 18)}" y="{y0 + 94}" font-size="{fit(label, 15, 162)}" font-weight="600" '
             f'fill="{t["text"]}">{esc(label)}</text>',
             ''.join(f'<text x="{num(x + 18)}" y="{y0 + 116 + j * 16}" font-size="12" fill="{t["dim"]}">{esc(ln)}</text>'
                     for j, ln in enumerate(wrap(sub, 12, 162)[:2]))]
        if i == 0:  # what's left of Claude's tokens: 41%
            fw = bw * 0.41
            g += [f'<rect x="{num(vx)}" y="{vy}" width="{bw}" height="8" rx="4" fill="{t["track"]}"/>',
                  f'<rect x="{num(vx)}" y="{vy}" width="{num(fw)}" height="8" rx="4" fill="{t["claude"]}">'
                  + anim("width", [(0, bw), (1, bw), (2.5, fw)], D, ease=True) + "</rect>",
                  f'<text x="{num(vx)}" y="{vy + 26}"{mono(11)} fill="{t["faint"]}">100% → 41%</text>']
        elif i == 1:  # one dot per bug; the 5 that three models (not four) flagged are paler
            for j in range(32):
                cx, cy = vx + 4 + (j % 16) * 10.2, vy + 2 + (j // 16) * 11
                g.append(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="3.6" fill="{t["pass"]}" '
                         f'fill-opacity="{1 if j < 27 else 0.45}">' + anim("opacity", [(0.4 + j * 0.05, 0), (0.55 + j * 0.05, 1)], D) + "</circle>")
            g.append(f'<text x="{num(vx)}" y="{vy + 36}"{mono(11)} fill="{t["faint"]}">● 4/4   ◐ 3/4</text>')
        elif i == 2:
            for j, k in enumerate(("codex", "agy", "copilot", "claude", "deepseek", "api")):
                g.append(f'<circle cx="{num(vx + 6 + j * 26)}" cy="{vy + 4}" r="6" fill="{t[k]}">'
                         + anim("r", [(0.5 + j * 0.25, 6), (0.65 + j * 0.25, 8), (0.9 + j * 0.25, 6)], D) + "</circle>")
        else:
            g.append(f'<text x="{num(vx)}" y="{vy + 9}"{mono(11.5)} fill="{t["faint"]}">import json, subprocess</text>')
        o.append(f"<g>{enter(0.15 * i)}" + "".join(g) + "</g>")
    return svg(t, W, H, "From farmout's own benchmarks: handing three papers to another subscription cost Claude 59% "
               "fewer tokens (74% less cost) for the same answers; each of 32 bugs (23 planted, 9 real regressions) "
               "was flagged by at least three of four models, 27 by all four; six kinds of worker (Codex, Gemini, "
               "Copilot, Claude, DeepSeek, any OpenAI-compatible or local model); zero dependencies and no API keys.",
               o, defs)


# ── Relay: Claude Code at its limit; what you type goes to Codex, and Claude catches up later ────────────────────
def relay(t):
    W, H, D = 880, 512, 16
    FS, LH, X, Y0, T = 13, 19.5, 40, 120, TERM
    o = [heading(t, L["relayTitle"], "farmout relay on --write"), win(16, 58, 848, 418, "claude · ~/shop"),
         f'<g>{anim("opacity", [(0, 1), (15.0, 1), (15.4, 0)], D)}']
    rows = [  # (row, time, segs, band)
        (0, 0.2, [("❯ ", T["dim"]), ("Why do stale products never expire from my cache?", T["fg"])], True),
        (1, 0.7, [("● ", T["fg"]), ("The check is inverted: ", T["fg"]), ("expires > time.time() + self.ttl", T["code"]),
                  (". Use ", T["fg"]), ("expires <= time.time()", T["code"]), (".", T["fg"])], False),
        (2, 1.3, [("  ⎿  ", T["dim"]), ("You've hit your 5-hour limit · resets 4:39am", T["fail"])], False),
        (4, 3.5, [("❯ ", T["dim"]), ("Fix it, and add a test that fails on the old code.", T["fg"])], True),
        (5, 5.2, [("● ", T["warn"]), ("Operation stopped by hook: Claude is at its usage limit until 04:39,", T["warn"])], False),
        (6, 5.2, [("  so codex answered (farmout session codex-1):", T["warn"])], False),
        (7, 5.45, [("  Fixed expiry predicate in `cache.py`. Added stale-entry regression test", T["warn"])], False),
        (8, 5.45, [("  in `test_cache.py`; fails on old code.", T["warn"])], False),
        (11, 9.6, [("❯ ", T["dim"]), ("In one line: who fixed get() while you were out, and how?", T["fg"])], True),
        (12, 9.9, [("  ⎿  ", T["dim"]), ("farmout: Claude is back and knows codex answered meanwhile (farmout session codex-1).", T["dim"])], False),
        (13, 10.7, [("● ", T["fg"]), ("Codex (farmout session codex-1) fixed get() in cache.py by correcting the", T["fg"])], False),
        (14, 10.7, [("  expiry predicate, and added a stale-entry regression test.", T["fg"])], False),
    ]
    for row, at, segs, band in rows:
        y = Y0 + row * LH
        if band:
            o.append(f'<rect x="26" y="{num(y - 14)}" width="828" height="19" fill="{T["band"]}">'
                     + anim("opacity", [(at, 0), (at + 0.15, 1)], D) + "</rect>")
        o.append(printed(X, y, segs, FS, at, D))
    y = Y0 + 9.5 * LH
    chip = L["reset"]
    cw = text_w(chip, 12, True) + 28
    o.append(f'<g>{anim("opacity", [(7.4, 0), (7.6, 1)], D)}<path d="M40 {num(y - 4)}H{num(440 - cw / 2 - 10)}M{num(440 + cw / 2 + 10)} '
             f'{num(y - 4)}H840" stroke="{T["line"]}"/><rect x="{num(440 - cw / 2)}" y="{num(y - 15)}" width="{num(cw)}" '
             f'height="22" rx="11" fill="{T["bg"]}" stroke="{T["pass"]}" stroke-opacity="0.5"/><text x="440" '
             f'y="{num(y)}"{mono(12)} fill="{T["pass"]}" text-anchor="middle">{esc(chip)}</text></g>')
    # the prompt box, the two prompts typed into it, and farmout's status line under it
    by = 418
    o.append(f'<path d="M26 {by - 22}H854M26 {by + 14}H854" stroke="{T["line"]}"/>'
             f'<text x="{X}" y="{by}"{mono(FS)} fill="{T["dim"]}">❯</text>')
    for uid, (t0, t1), text in (("p1", (2.0, 3.3), "Fix it, and add a test that fails on the old code."),
                                ("p2", (8.1, 9.4), "In one line: who fixed get() while you were out, and how?")):
        o.append(f'<g opacity="0">{anim("opacity", [(t0 - 0.05, 0), (t0, 1), (t1 + 0.15, 1), (t1 + 0.2, 0)], D)}'
                 + typed(uid, X + 16, by, [(text, T["fg"])], FS, t0, t1, D) + "</g>")
    o.append(f'<rect x="{X + 16}" y="{by - 12}" width="8" height="16" fill="{T["soft"]}" opacity="0.75">'
             + anim("opacity", [(0, 0.75), (2.0, 0.75), (2.01, 0), (3.5, 0), (3.51, 0.75), (8.1, 0.75), (8.11, 0),
                                (9.6, 0), (9.61, 0.75)], D, discrete=True) + "</rect>")
    o.append(f'<text x="{X + 8}" y="{by + 38}"{mono(12.5)} fill="{T["dim"]}" opacity="0">farmout: '
             f'<tspan fill="{T["claude"]}">claude</tspan> out till 04:39 → <tspan fill="{T["codex"]}">codex</tspan> '
             f'(codex-1)' + shown(3.6, 7.5, D, 0.2) + "</text>")
    o.append("</g>")
    o.append(f'<text x="{W / 2}" y="{H - 14}" font-size="{fit(L["relayFoot"], 12.5, 820)}" fill="{t["faint"]}" '
             f'text-anchor="middle">{esc(L["relayFoot"])}</text>')
    return svg(t, W, H, "Animated replay of farmout relay in Claude Code: Claude Code says you've hit your 5-hour limit; "
               "the next prompt, 'Fix it, and add a test that fails on the old code', is answered by Codex with the "
               "whole conversation ('Operation stopped by hook: Claude is at its usage limit until 04:39, so codex "
               "answered'); once the limit resets, Claude knows what Codex did and explains the fix.", o, blur(W, H))


# ── Review: a real /farmout:review run (scripts/demo.json) ──────────────────────────────────────────────────────
REVIEWERS = [("antigravity", 3.4, 8, 25), ("codex", 4.0, 5, 36), ("claude", 2.9, 9, 15), ("copilot", 3.6, 5, 29)]
FINDINGS = [
    ("high", "files_api.py:18", "SQL injection: owner interpolated into the query", 4),
    ("high", "files_api.py:36", "path traversal: absolute or ../ name, no owner check", 4),
    ("high", "files_api.py:27", "1-based pages: page 1 skips the first 20 files", 4),
    ("medium", "files_api.py:43", "unknown file_id: row is None, TypeError instead of 404", 4),
    ("medium", "files_api.py:48", "quota read-then-write races; missing quota row crashes", 3),
    ("medium", "files_api.py:49", "nothing commits: quota, row and disk drift apart", 3),
    ("low", "files_api.py:25", "page < 1 is not rejected", 3),
    ("low", "files_api.py:47", "delete trusts stored names: traversal via a bad row", 1),
    ("low", "files_api.py:23", "file URL built from raw names, not quoted", 1),
]


def review(t):
    W, H, D = 880, 530, 15
    FS, LH, X, Y0, T = 12.5, 18.5, 38, 120, TERM
    sev = {"high": T["fail"], "medium": T["warn"], "low": T["dim"]}
    o = [heading(t, L["reviewTitle"], L["reviewNote"]), win(16, 58, 848, 456, "claude · ~/project"),
         f'<g>{anim("opacity", [(0, 1), (14.2, 1), (14.5, 0)], D)}']
    o.append(printed(X, Y0, [("❯ ", T["dim"]), ("/farmout:review", T["fg"], True)], FS, 0.2, D))
    o.append(printed(X, Y0 + LH, [("⏺ ", T["pass"]), ("Saved the diff of your uncommitted change ", T["fg"]),
                                  ("(files_api.py, +61 lines)", T["dim"])], FS, 1.0, D))
    o.append(printed(X, Y0 + 2 * LH, [("⏺ ", T["pass"]), ("farmout · delegate", T["fg"], True),
                                      ("  4 reviewers in parallel, read-only", T["dim"])], FS, 1.7, D))
    for i, (name, at, n, secs) in enumerate(REVIEWERS):
        y, c = Y0 + (3 + i) * LH, T[KEY[name]]
        bx = X + 15 * FS * ADV
        o.append(printed(X + 3 * FS * ADV, y, [(name, c, True)], FS, 2.2, D))
        o.append(f'<rect x="{num(bx)}" y="{num(y - 8)}" width="120" height="6" rx="3" fill="{T["line"]}">'
                 + anim("opacity", [(2.2, 0), (2.35, 1)], D) + "</rect>")
        o.append(f'<rect x="{num(bx)}" y="{num(y - 8)}" width="120" height="6" rx="3" fill="{c}">'
                 + anim("width", [(2.2, 0), (at, 120)], D) + anim("opacity", [(2.2, 0), (2.35, 1)], D) + "</rect>")
        o.append(printed(bx + 134, y, [("✓ ", T["pass"]), (f"{n} findings", T["fg"]), (f"   {secs}s", T["dim"])], FS, at, D))
    o.append(printed(X, Y0 + 8 * LH, [("⏺ ", T["pass"]), ("Merged 4 reviews and checked every finding against the code", T["fg"])], FS, 4.6, D))
    o.append(printed(X, Y0 + 9 * LH, [(f"  {'severity':<10}{'location':<18}{'defect':<58}models", T["dim"])], FS, 5.3, D))
    for i, (s, loc, what, agree) in enumerate(FINDINGS):
        at = 5.6 + i * 0.22
        o.append(printed(X, Y0 + (10 + i) * LH, [(f"  {s:<10}", sev[s]), (f"{loc:<18}", T["fg"]), (f"{what:<58}", T["fg"]),
                                                 (f"{agree}/4  ", T["fg"], True), ("✓", T["pass"])], FS, at, D))
    o.append(printed(X, Y0 + 20 * LH, [("  All 5 planted bugs confirmed, plus 4 real issues nobody planted. "
                                         "Nothing changed in your tree.", T["fg"])], FS, 8.2, D))
    o.append("</g>")
    return svg(t, W, H, "Animated replay of a real /farmout:review run: one uncommitted change goes to antigravity, "
               "codex, claude and copilot in parallel (8, 5, 9 and 5 findings), then Claude prints a merged table of "
               "nine findings, each checked against the code, with how many of the four models flagged it: all five "
               "planted bugs confirmed, plus four real issues nobody planted.", o)


# ── Race: every model attempts the task in its own worktree; the tests pick the winner ──────────────────────────
def race(t):
    W, H, D = 880, 430, 13
    FS, LH, X, Y0, T = 12.5, 19, 38, 120, TERM
    o = [heading(t, L["raceTitle"], "farmout race"), win(16, 58, 848, 330, "~/app"),
         f'<g>{anim("opacity", [(0, 1), (12.2, 1), (12.5, 0)], D)}',
         typed("rc", X, Y0, [("$ ", T["faint"]), ('farmout race "make test_sync deterministic" --test "pytest -x"', T["fg"])],
               FS, 0.4, 1.7, D),
         printed(X, Y0 + LH, [("farmout race · 4 workers · one worktree each", T["dim"])], FS, 2.0, D)]
    ends = {"antigravity": 5.6, "codex": 4.3, "claude": 3.9, "copilot": 3.6}
    for i, (name, end) in enumerate(ends.items()):
        y, c = Y0 + (2 + i) * LH, T[KEY[name]]
        o.append(printed(X, y, [("  ", T["fg"]), (f"{name:<13}", c, True)], FS, 2.2, D))
        o.append(printed(X + 15 * FS * ADV, y, [("working…", T["dim"])], FS, 2.2, D, end=end))
        o.append(printed(X + 15 * FS * ADV, y, [("✓ ", T["pass"]), ("done", T["fg"])], FS, end, D))
    head = ("worker", "result", "tests", "files", "+/-", "time", "session")
    body = [("antigravity", "ok", "pass", "1", "+9/-4", "95s", "race-1-antigravity"),
            ("codex", "ok", "pass", "1", "+6/-3", "48s", "race-1-codex"),
            ("claude", "ok", "pass", "2", "+19/-7", "41s", "race-1-claude"),
            ("copilot", "ok", "FAIL", "1", "+4/-2", "37s", "race-1-copilot")]
    widths = [max(len(r[i]) for r in [head] + body) for i in range(len(head))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    y = Y0 + 7 * LH
    o.append(f'<rect x="26" y="{num(y + 2 * LH - 14)}" width="828" height="19" fill="{T["codex"]}" fill-opacity="0.12">'
             + anim("opacity", [(7.6, 0), (7.9, 1)], D) + "</rect>")
    o.append(printed(X, y, [(fmt(head), T["dim"])], FS, 6.2, D))
    for i, r in enumerate(body):
        line = fmt(r)
        segs = [(line[:widths[0]], T[KEY[r[0]]], True)]
        rest = line[widths[0]:]
        k = rest.find(r[2])
        segs += [(rest[:k], T["fg"]), (r[2], T["pass"] if r[2] == "pass" else T["fail"], True), (rest[k + len(r[2]):], T["fg"])]
        o.append(printed(X, y + (1 + i) * LH, segs, FS, 6.4 + i * 0.15, D))
    o.append(printed(X, y + 6 * LH, [("Winner: ", T["fg"], True), ("race-1-codex", T["codex"], True), (" (codex). ", T["fg"]),
                                     ("See it: farmout diff race-1-codex   Apply it: farmout apply race-1-codex", T["dim"])],
                     FS, 8.1, D))
    o.append("</g>")
    o.append(f'<text x="{W / 2}" y="{H - 16}" font-size="{fit(L["raceFoot"], 12.5, 820)}" fill="{t["faint"]}" '
             f'text-anchor="middle">{esc(L["raceFoot"])}</text>')
    return svg(t, W, H, "Animated illustration of farmout race: four workers each attempt the task in their own git "
               "worktree, the test command runs in each, and the attempt that passes with the smallest diff wins; "
               "nothing touches your tree until you farmout apply it.", o)


# ── Bench: which subscription finds the bugs (bench/README.md, sets 1 to 3) ─────────────────────────────────────
def bench(t):
    W, H, D, X, MAXW, T0, RUN = 880, 330, 10, 196, 440, 0.6, 3.2
    rows = [("antigravity", 32, "5–11 min"), ("claude", 32, "23–94 s"), ("copilot", 30, "49 s–4 min"),
            ("codex", 29, "78 s–5 min")]
    o = [heading(t, L["benchTitle"], "farmout bench"),
         f'<text x="28" y="66" font-size="{fit(L["benchSub"], 13.5, 820)}" fill="{t["dim"]}">{esc(L["benchSub"])}</text>']
    te = T0 + RUN
    for i, (name, n, secs) in enumerate(rows):
        y, k = 96 + i * 46, KEY[name]
        w = n / 32 * MAXW
        label = name + (" (Sonnet)" if name == "claude" else "")
        o += [f'<text x="{X - 16}" y="{y + 15}"{mono(fit(label, 14, 150, True))} font-weight="700" fill="{t[k]}" '
              f'text-anchor="end">{esc(label)}</text>',
              f'<rect x="{X}" y="{y}" width="{MAXW}" height="20" rx="5" fill="{t["track"]}"/>',
              f'<rect x="{X}" y="{y}" width="{num(w)}" height="20" rx="5" fill="{t[k]}">'
              + anim("width", [(T0, 0), (te, w), (D - 0.5, w), (D - 0.15, 0)], D) + "</rect>",
              f'<text x="{X + MAXW + 14}" y="{y + 15}" font-size="15">{shown(te, D - 0.3, D, 0.2)}'
              f'<tspan font-weight="700" fill="{t["text"]}">{n}</tspan><tspan fill="{t["dim"]}"> / 32</tspan></text>',
              f'<text x="{W - 28}" y="{y + 15}"{mono(12)} fill="{t["faint"]}" text-anchor="end">{esc(secs)}</text>']
    o.append(f'<text x="{W - 28}" y="86"{mono(11)} fill="{t["faint"]}" text-anchor="end">{esc(L["benchTime"])}</text>')
    o.append(f'<text x="28" y="{H - 22}" font-size="{fit(L["benchFoot"], 12.5, 824)}" fill="{t["faint"]}">'
             f'{esc(L["benchFoot"])}</text>')
    return svg(t, W, H, "Bugs found out of 32 (23 planted, 9 real regressions from open-source projects) in farmout's "
               "benchmark: antigravity 32, claude (Sonnet) 32, copilot 30, codex 29. Typical review time: claude 23 to "
               "94 seconds, copilot 49 seconds to 4 minutes, codex 78 seconds to 5 minutes, antigravity 5 to 11 "
               "minutes.", o)


# ── Tree: a conversation as a graph, branches on other models ───────────────────────────────────────────────────
def tree(t):
    W, H, D = 880, 346, 12
    FS, LH, X, Y0, T = 13.5, 25, 40, 124, TERM
    cl, cx, dim, fg = T["claude"], T["codex"], T["dim"], T["fg"]
    rows = [
        (0.3, [("dates", fg, True), (" · claude · 2m ago", dim)]),
        (0.7, [("● ", cl), ("1  ", dim), ("Why does parse_date fail on 2024-02-30?", fg)]),
        (1.4, [("├─╮ ", dim), ("⑂ dates-1 · ", dim), ("claude:opus", cl)]),
        (1.8, [("│ ", dim), ("● ", cl), ("2  ", dim), ("Try it with a lookup table instead", fg)]),
        (2.4, [("● ", cl), ("2  ", dim), ("Fix it and add a test for leap years", fg)]),
        (3.1, [("├─╮ ", dim), ("⑂ dates-2 · ", dim), ("codex", cx)]),
        (3.5, [("│ ", dim), ("● ", cx), ("3  ", dim), ("Same fix, without the new dependency", fg)]),
        (4.1, [("● ", cl), ("3  ", dim), ("Now do the same for parse_time", fg)]),
    ]
    o = [heading(t, L["treeTitle"], L["treeNote"]), win(16, 58, 848, 272, "~/app — farmout tree dates"),
         f'<g>{anim("opacity", [(0, 1), (11.2, 1), (11.5, 0)], D)}']
    for i, (at, segs) in enumerate(rows):
        o.append(printed(X, Y0 + i * LH, segs, FS, at, D))
    kx, ky = 560, 112
    o.append(f'<rect x="{kx}" y="{ky - 14}" width="282" height="{36 + 26 * len(L["keys"])}" rx="8" fill="none" '
             f'stroke="{T["line"]}"/>' + f'<text x="{kx + 16}" y="{ky + 8}"{mono(12)} fill="{dim}">{esc(L["keysTitle"])}</text>')
    for i, (key, what) in enumerate(L["keys"]):
        y = ky + 36 + i * 26
        o.append(f'<g>{anim("opacity", [(4.6 + i * 0.25, 0), (4.8 + i * 0.25, 1)], D)}'
                 f'<rect x="{kx + 16}" y="{y - 14}" width="22" height="20" rx="4" fill="{T["band"]}" stroke="{T["line"]}"/>'
                 f'<text x="{kx + 27}" y="{y + 1}"{mono(12.5)} fill="{fg}" text-anchor="middle">{esc(key)}</text>'
                 f'<text x="{kx + 48}" y="{y + 1}" font-size="{fit(what, 13, 220)}" fill="{T["soft"]}">{esc(what)}</text></g>')
    o.append("</g>")
    return svg(t, W, H, "Animated: farmout tree draws a conversation as a graph. Turn 1 on claude branches onto "
               "claude:opus, turn 2 branches onto codex, and the main line goes on; farmout ui shows it full-screen, "
               "where c continues, b branches off any turn, e asks a turn again differently, / finds words in every "
               "answer, Y copies a command whole and C carries on your own CLI session.", o)


# ── Pipe: the built-in fix pipeline ─────────────────────────────────────────────────────────────────────────────
def pipe(t):
    W, H, D = 880, 282, 10
    bw, bh, y0 = 150, 104, 80
    xs = [24, 262, 500]
    gates = ["agree ≥ 2", "tests", "high == 0"]
    o = [heading(t, L["pipeTitle"], "farmout pipe run fix")]
    t_on = [0.5, 3.0, 5.5]
    for i, x in enumerate(xs):
        name, what = L["stages"][i]
        on = t_on[i]
        hi = anim("stroke", [(0, t["line"]), (on, t["text"]), (on + 2.0, t["line"])], D, discrete=True)
        o.append(f'<rect x="{x}" y="{y0}" width="{bw}" height="{bh}" rx="12" fill="{t["card"]}" stroke="{t["line"]}">{hi}</rect>')
        o.append(f'<text x="{x + 16}" y="{y0 + 28}"{mono(15)} font-weight="700" fill="{t["text"]}">{esc(name)}</text>')
        for j, ln in enumerate(wrap(what, 12, bw - 30)[:3]):
            o.append(f'<text x="{x + 16}" y="{y0 + 50 + j * 17}" font-size="12" fill="{t["dim"]}">{esc(ln)}</text>')
        lanes = ["codex"] if i == 1 else ["codex", "agy", "copilot", "claude"]
        for j, k in enumerate(lanes):
            cx = x + bw - 18 - j * 13
            o.append(f'<circle cx="{cx}" cy="{y0 + 23}" r="5" fill="{t[k]}">'
                     + anim("r", [(on + 0.2 + j * 0.1, 5), (on + 0.4 + j * 0.1, 7), (on + 0.7 + j * 0.1, 5)], D) + "</circle>")
        # the gate after this stage
        gx = x + bw + 44
        o.append(f'<path d="M{x + bw} {y0 + bh / 2}H{xs[i + 1] if i < 2 else 738}" stroke="{t["line"]}" stroke-width="2"/>')
        o.append(pulse(f"M{x + bw} {y0 + bh / 2}H{xs[i + 1] if i < 2 else 738}", t["text"], on + 1.8, on + 2.4, D))
        gw = text_w(gates[i], 11, True) + 18
        o.append(f'<rect x="{num(gx - gw / 2)}" y="{y0 + bh / 2 - 10}" width="{num(gw)}" height="20" rx="10" '
                 f'fill="{t["bg"]}" stroke="{t["line"]}">'
                 + anim("stroke", [(0, t["line"]), (on + 1.9, t["pass"]), (D - 0.4, t["line"])], D, discrete=True) + "</rect>")
        o.append(f'<text x="{num(gx)}" y="{y0 + bh / 2 + 4}"{mono(11)} fill="{t["dim"]}" text-anchor="middle">{esc(gates[i])}</text>')
    ex = 738
    o.append(f'<rect x="{ex}" y="{y0 + bh / 2 - 24}" width="118" height="48" rx="12" fill="{t["card"]}" stroke="{t["pass"]}">'
             + anim("stroke", [(0, t["line"]), (8.0, t["pass"]), (D - 0.4, t["line"])], D, discrete=True) + "</rect>")
    o.append(f'<text x="{ex + 59}" y="{y0 + bh / 2 - 3}" font-size="{fit("✓ " + L["ready"], 13.5, 100)}" font-weight="700" '
             f'fill="{t["pass"]}" text-anchor="middle">✓ {esc(L["ready"])}</text>')
    o.append(f'<text x="{ex + 59}" y="{y0 + bh / 2 + 15}"{mono(10.5)} fill="{t["faint"]}" text-anchor="middle">farmout apply</text>')
    o.append(f'<text x="28" y="{H - 52}"{mono(13)}><tspan fill="{t["faint"]}">$ </tspan><tspan fill="{t["text"]}">'
             f'farmout pipe run fix --set test="pytest -x"</tspan></text>')
    o.append(f'<text x="28" y="{H - 24}" font-size="{fit(L["pipeFoot"], 13, 820)}" fill="{t["dim"]}">{esc(L["pipeFoot"])}</text>')
    return svg(t, W, H, "Animated: the built-in fix pipeline. Four models review your change; the gate passes what 2 "
               "or more agree on; codex fixes it in a git worktree and the tests must pass; four models review the "
               "fix and the gate wants no high-severity finding; then the patch is ready for farmout apply.",
               o, blur(W, H))


# ── Hub: where you type, farmout, and the subscriptions behind it ───────────────────────────────────────────────
def hub(t):
    W, H, D = 880, 392, 8
    heads, workers = L["heads"], L["workers"]
    o = [heading(t, L["hubTitle"], L["hubNote"]),
         f'<text x="28" y="78" font-size="12" fill="{t["faint"]}">{esc(L["headsLabel"])}</text>',
         f'<text x="{W - 28}" y="78" font-size="12" fill="{t["faint"]}" text-anchor="end">{esc(L["workersLabel"])}</text>']
    MX, MY = 440, 226
    hw, ww = 236, 262
    for i, (name, how) in enumerate(heads):
        y = 104 + i * 56
        d = f"M{28 + hw} {y + 20}C{28 + hw + 60} {y + 20} {MX - 90} {MY} {MX - 34} {MY}"
        o += [f'<path d="{d}" fill="none" stroke="{t["text"]}" stroke-opacity="0.25" stroke-width="1.6"/>',
              pulse(d, t["text"], 0.3 + i * 0.35, 1.0 + i * 0.35, D, 2),
              f'<rect x="28" y="{y}" width="{hw}" height="40" rx="9" fill="{t["card"]}" stroke="{t["line"]}"/>',
              f'<text x="44" y="{y + 17}" font-size="{fit(name, 13.5, hw - 32)}" font-weight="600" fill="{t["text"]}">{esc(name)}</text>',
              f'<text x="44" y="{y + 32}"{mono(fit(how, 11, hw - 32, True))} fill="{t["faint"]}">{esc(how)}</text>']
    for i, (name, how) in enumerate(workers):
        y = 92 + i * 48
        k = KEY[name]
        d = f"M{MX + 34} {MY}C{MX + 100} {MY} {W - 28 - ww - 60} {y + 18} {W - 28 - ww} {y + 18}"
        o += [f'<path d="{d}" fill="none" stroke="{t[k]}" stroke-opacity="0.45" stroke-width="1.8"/>',
              pulse(d, t[k], 2.2 + i * 0.12, 2.9 + i * 0.12, D, 2.2),
              f'<rect x="{W - 28 - ww}" y="{y}" width="{ww}" height="36" rx="9" fill="{t["card"]}" stroke="{t["line"]}"/>',
              f'<circle cx="{W - 28 - ww + 16}" cy="{y + 18}" r="5" fill="{t[k]}"/>',
              f'<text x="{W - 28 - ww + 30}" y="{y + 23}"{mono(13)} font-weight="700" fill="{t[k]}">{esc(name)}</text>',
              f'<text x="{W - 40}" y="{y + 23}" font-size="{fit(how, 11.5, ww - 120)}" fill="{t["faint"]}" '
              f'text-anchor="end">{esc(how)}</text>']
    o.append(f'<circle cx="{MX}" cy="{MY}" r="46" fill="{t["card"]}" stroke="{t["line"]}"/>')
    o.append(mark(t, MX - 22, MY, 0.105, sparks=(1.9,), dur=D))
    o.append(f'<text x="{MX}" y="{MY + 72}" font-size="15" font-weight="800" fill="{t["text"]}" text-anchor="middle">farmout</text>')
    return svg(t, W, H, "Where you type: a terminal (farmout commands), Claude Code (the plugin: MCP tools and slash "
               "commands), Codex and Copilot CLI (MCP), Zed, JetBrains and Neovim (ACP), any MCP client. What runs "
               "the work: codex on a ChatGPT plan, antigravity on a Google account, copilot on a Copilot plan, claude "
               "on a second login, deepseek with an API key, and api for Ollama, vLLM or OpenRouter.", o, blur(W, H))


# ── Install: what pipx and `farmout doctor --live` print ─────────────────────────────────────────────────────────
def install(t):
    W, H, D = 880, 446, 15
    FS, LH, X, Y0, T = 12.5, 18.5, 36, 84, TERM
    lines = [
        (2.4, [("  installed package farmout 0.1.0, installed using Python 3.14.8", T["soft"])]),
        (2.55, [("done! ✨ 🌟 ✨", T["soft"])]),
        None,
        (5.1, [("farmout 0.1.0 on Python 3.14.8; sessions and runs in ~/.local/state/farmout, results in ~/.cache/farmout", T["dim"])]),
        (5.3, [("  ready    ", T["pass"]), ("antigravity  ", T["agy"], True), ("/usr/bin/agy 1.4.6", T["soft"])]),
        (5.35, [("  note                  ", T["dim"]), ("Google's terms (section 6) may forbid using Antigravity with other tools", T["dim"])]),
        (5.5, [("  ready    ", T["pass"]), ("codex        ", T["codex"], True), ("/usr/bin/codex codex-cli 0.163.0", T["soft"])]),
        (5.7, [("  ready    ", T["pass"]), ("claude       ", T["claude"], True), ("~/.local/bin/claude 2.1.323 (Claude Code)", T["soft"])]),
        (5.9, [("  ready    ", T["pass"]), ("copilot      ", T["copilot"], True), ("/usr/local/bin/copilot GitHub Copilot CLI 1.0.116.", T["soft"])]),
        (6.1, [("  missing  ", T["fail"]), ("deepseek     ", T["deepseek"], True), ("install DeepSeek Harness (`npm i -g @deepseek-ai/dsh`) and set DEEPSEEK_API_KEY", T["dim"])]),
        (6.3, [("  missing  ", T["fail"]), ("api          ", T["api"], True), ("set FARMOUT_API_BASE and FARMOUT_API_MODEL (and FARMOUT_API_KEY if needed)", T["dim"])]),
        (6.6, [("4 subscriptions ready. `farmout review` has them all review your change, and a task at its limit", T["fg"])]),
        (6.6, [("moves on in this order here: antigravity, codex, claude, copilot. The `fallback` setting changes it.", T["fg"])]),
        (8.4, [("  works    ", T["pass"]), ("antigravity  ", T["agy"], True), ("11s", T["dim"])]),
        (7.6, [("  works    ", T["pass"]), ("codex        ", T["codex"], True), ("4s", T["dim"])]),
        (7.9, [("  works    ", T["pass"]), ("claude       ", T["claude"], True), ("6s", T["dim"])]),
        (7.7, [("  works    ", T["pass"]), ("copilot      ", T["copilot"], True), ("5s", T["dim"])]),
    ]
    o = [win(16, 16, 848, 414, "~ — zsh"), f'<g>{anim("opacity", [(0, 1), (14.2, 1), (14.5, 0)], D)}',
         typed("i1", X, Y0, [("$ ", T["faint"]), ("pipx install git+https://github.com/JayPokale/farmout", T["fg"])], FS, 0.5, 1.9, D)]
    row = 1
    for item in lines:
        if item is None:
            o.append(typed("i2", X, Y0 + (row + 0.3) * LH, [("$ ", T["faint"]), ("farmout doctor --live", T["fg"])], FS, 3.6, 4.5, D))
            row += 1.6
            continue
        at, segs = item
        o.append(printed(X, Y0 + row * LH, segs, FS, at, D))
        row += 1
    o.append("</g>")
    return svg(t, W, H, "Animated terminal: pipx installs farmout from GitHub, then farmout doctor --live lists the "
               "workers it found (antigravity, codex, claude and copilot ready; deepseek and api not set up), the "
               "order a task at its limit moves on in, and a live check that each ready worker answers.", o)


# ── Commands: a typed cheat sheet ───────────────────────────────────────────────────────────────────────────────
def commands(t):
    W, H, D = 880, 384, 13
    FS, X, Y0, LH, PAD = 13.5, 38, 82, 27.5, 44
    cmds = [("$", "farmout review"), ("$", 'farmout race "<task>" --test "pytest -x"'),
            ("$", 'farmout run codex "<task>" -n fix'), ("$", "farmout send fix -w claude:opus"),
            ("$", "farmout relay on --write"), ("$", "farmout continue --write"), ("$", "farmout bench --apply"),
            ("$", "farmout ui"), ("$", "farmout pipe run fix"), (">", "/farmout:review")]
    T = TERM
    o = [win(16, 16, 848, 352, L["sheet"]), f'<g>{anim("opacity", [(0, 1), (D - 0.6, 1), (D - 0.3, 0)], D)}']
    for i, (p, c) in enumerate(cmds):
        y, t0 = Y0 + i * LH, 0.4 + i * 0.95
        t1 = t0 + 0.4
        o.append(typed(f"k{i}", X, y, [(f"{p} ", T["faint"] if p == "$" else T["claude"]), (c, T["fg"])], FS, t0, t1, D))
        o.append(printed(X + PAD * FS * ADV, y, [(f"# {L['notes'][i]}", T["dim"])], FS, t1 + 0.15, D))
    o.append("</g>")
    return svg(t, W, H, "Animated cheat sheet: " + "; ".join(f"{c}: {n}" for (_, c), n in zip(cmds, L["notes"])) + ".", o)


# ── Support: the coffee ask, as a slim strip (top) and a full card (bottom) ─────────────────────────────────────
BMC = "#FFDD00"


def cup(t, x, y, s):
    """A mug with rising steam; (x, y) is the top-left of an 80×100 box at scale s."""
    steam = ""
    for i, sx in enumerate((20, 34, 48)):
        p = i * 0.7
        steam += (f'<path d="M{sx} 28c-6-7 6-11 0-19" fill="none" stroke="{t["dim"]}" stroke-width="3" '
                  f'stroke-linecap="round" opacity="0">' + anim("opacity", [(p, 0), (p + 0.8, 0.85), (p + 1.6, 0)], 3)
                  + anim("transform", [(p, "0 6"), (p + 1.6, "0 -6")], 3, kind="translate") + "</path>")
    return (f'<g transform="translate({num(x)} {num(y)}) scale({s})">{steam}'
            f'<ellipse cx="38" cy="92" rx="38" ry="6" fill="{t["line"]}"/>'
            f'<path d="M62 42h6a10 10 0 0 1 0 20h-6" fill="none" stroke="{t["text"]}" stroke-width="3"/>'
            f'<path d="M6 34h56v30a20 20 0 0 1-20 20H26A20 20 0 0 1 6 64z" fill="{BMC}" stroke="{t["text"]}" '
            f'stroke-width="3" stroke-linejoin="round"/></g>')


def coffee_button(x, y, w, h, fs):
    """The yellow button, with a light sweep across it every few seconds."""
    return (f'<clipPath id="btn"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{num(h / 4)}"/></clipPath>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{num(h / 4)}" fill="{BMC}" stroke="#e5c700"/>'
            f'<g clip-path="url(#btn)"><rect x="{x - 60}" y="{y}" width="34" height="{h}" fill="#ffffff" opacity="0.5" '
            f'transform="skewX(-18) translate({num(0.325 * y)} 0)">' + anim("x", [(0, x - 60), (0.9, x + w + 40)], 3.2, ease=True)
            + "</rect></g>"
            f'<text x="{num(x + w / 2)}" y="{num(y + h / 2 + fs * 0.36)}" font-size="{fit(L["button"], fs, w - 24)}" '
            f'font-weight="700" fill="#0d1117" text-anchor="middle">{esc(L["button"])}</text>')


def strip(t):
    o = [cup(t, 26, 8, 0.6),
         f'<text x="96" y="36" font-size="{fit(L["stripTitle"], 16, 570)}" font-weight="700" fill="{t["text"]}">{esc(L["stripTitle"])}</text>',
         f'<text x="96" y="58" font-size="{fit(L["stripSub"], 13.5, 570)}" fill="{t["dim"]}">{esc(L["stripSub"])}</text>',
         coffee_button(690, 20, 166, 40, 15)]
    return svg(t, 880, 80, "farmout is free. Testing it on every subscription isn't. Saved you a limit? Buy me a coffee.", o)


def support(t):
    defs = (f'<radialGradient id="warm" cx="0.08" cy="0.5" r="0.5"><stop offset="0" stop-color="{BMC}" '
            f'stop-opacity="{t["glow"]}"/><stop offset="1" stop-color="{BMC}" stop-opacity="0"/></radialGradient>')
    o = ['<rect x="1" y="1" width="878" height="208" rx="14" fill="url(#warm)"/>', cup(t, 50, 46, 1.15),
         f'<text x="190" y="84" font-size="{fit(L["free"], 26, 440)}" font-weight="800" fill="{t["text"]}">{esc(L["free"])}</text>',
         f'<text x="190" y="118" font-size="{fit(L["notFree"], 26, 440)}" font-weight="800" fill="url(#lanes)">{esc(L["notFree"])}</text>',
         f'<text x="190" y="152" font-size="{fit(L["body1"], 14.5, 430)}" fill="{t["dim"]}">{esc(L["body1"])}</text>',
         f'<text x="190" y="174" font-size="{fit(L["body2"], 14.5, 430)}" fill="{t["dim"]}">{esc(L["body2"])}</text>',
         coffee_button(650, 76, 200, 54, 17),
         f'<text x="750" y="158"{mono(12)} fill="{t["faint"]}" text-anchor="middle">buymeacoffee.com/jaypokale3b</text>']
    return svg(t, 880, 210, "farmout is free. Testing it on every subscription isn't. Saved you a limit? Buy me a coffee "
               "at buymeacoffee.com/jaypokale3b.", o, defs + lanes_grad(t))


# ── Line: the travelling divider, in all four lanes (one file; it reads on both themes) ─────────────────────────
LINE = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="4" viewBox="0 0 1200 4" preserveAspectRatio="none" role="img" aria-label="divider">
<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#f2a65a" stop-opacity="0"/><stop offset="0.35" stop-color="#f2a65a"/><stop offset="0.6" stop-color="#f47c7c"/><stop offset="0.8" stop-color="#a78bfa"/><stop offset="1" stop-color="#5ec4b6"/></linearGradient></defs>
<rect width="1200" height="4" rx="2" fill="#9a9eaf" opacity="0.16"/>
<rect width="300" height="4" rx="2" fill="url(#g)"><animate attributeName="x" from="-300" to="1200" dur="3.4s" repeatCount="indefinite"/></rect>
</svg>
"""

PANELS = {"banner": banner, "stats": stats, "relay": relay, "review": review, "race": race, "bench": bench,
          "tree": tree, "pipe": pipe, "hub": hub, "install": install, "commands": commands, "strip": strip,
          "support": support}
ENGLISH_ONLY = {"install"}  # nothing in it to translate


def main():
    global L
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "line.svg").write_text(LINE)
    for lang, strings in I18N.items():
        L = strings
        folder = OUT if lang == "en" else OUT / lang
        folder.mkdir(parents=True, exist_ok=True)
        for name, build in PANELS.items():
            if lang != "en" and name in ENGLISH_ONLY:
                continue
            for theme, t in THEMES.items():
                (folder / f"{name}-{theme}.svg").write_text(build(t))
    print(f"wrote {OUT}: line, and {', '.join(PANELS)} for {', '.join(I18N)} (dark, light)")


if __name__ == "__main__":
    main()
