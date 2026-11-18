"""The conversation graph: each conversation a tree of its turns, every branch drawn off the turn it goes on from, in
lanes the way `git log --graph` draws branches. `farmout tree` prints it; `farmout ui` draws it in the terminal.

    ● 1  For a small web API, should sessions live in Redis?
    ├─╮ ⑂ api-1 · codex
    │ ● 2  Argue for keeping them in memory instead.
    ● 2  We run 3 servers behind a load balancer. Does that change it?
"""
import os
import re
import time
import unicodedata

NODE = {"ok": "●", "failed": "✕", "stopped": "○", "running": "◌"}
PALETTE = {"err": "#f37b72", "run": "#f2c04f", "ok": "#5bd38a", "acc": "#f2a65a", "code": "#7fb4ca",
           "w:claude": "#f47c7c", "w:codex": "#f2a65a", "w:antigravity": "#5ec4b6", "w:copilot": "#a78bfa",
           "w:deepseek": "#4d6bfe"}  # the workers' colours are bench.COLOURS, as on the leaderboard
ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[ -/]+[0-~]|[@-Z\\-_])"  # 7-bit
                     r"|\x9b[0-?]*[ -/]*[@-~]|\x9d[^\x07\x1b\x9c]*(?:\x07|\x9c|\x1b\\)?")  # and 8-bit CSI and OSC
CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def clean(text):
    """Text from a prompt or a worker, safe for a terminal: no escape sequences or other control characters, which a
    worker's output could use to rewrite the screen, the clipboard or the window title."""
    text = ESCAPES.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL.sub("", text.expandtabs(4))


def cols(text):
    """The columns text takes in a terminal: wide (CJK) characters take two, combining marks none."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def take(text, n):
    """The longest start of text that fits in n columns."""
    used = 0
    for i, c in enumerate(text):
        used += cols(c)
        if used > n:
            return text[:i]
    return text


def clip(text, n):
    """text in n columns, ending in … where it was cut."""
    return text if cols(text) <= n else take(text, n - 1) + "…" if n > 0 else ""


def ago(ts):
    s = max(0, time.time() - (ts or time.time()))
    return (f"{s:.0f}s" if s < 60 else f"{s / 60:.0f}m" if s < 3600 else f"{s / 3600:.0f}h" if s < 86400
            else f"{s / 86400:.0f}d")


def short(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p and p.startswith(home) else p


def lane(x):
    return f"{x.get('worker')}:{x['model']}" if x.get("model") else str(x.get("worker"))


def forest(rows, everything=False):
    """Sessions (ui.snapshot's rows) as conversations, the most recently active first: trees of sessions, each with
    its turns, numbered along the whole conversation, and the branches off it in the order they were made.
    everything: also review, ask, race and pipeline runs."""
    nodes = {r["name"]: {**{k: v for k, v in r.items() if k != "history"}, "branches": [],
                         "turns": [{**t, "num": (r.get("base") or 0) + t["n"]} for t in r.get("history") or []]}
             for r in rows}
    roots = []
    for node in sorted(nodes.values(), key=lambda x: x.get("created") or 0):
        parent = nodes.get(node.get("parent"))
        (parent["branches"] if parent and parent is not node else roots).append(node)

    def active(node):
        now = time.time() if node.get("state") in ("running", "queued") else node.get("updated") or 0
        return max([now] + [active(b) for b in node["branches"]])
    return sorted((r for r in roots if everything or not r.get("group")), key=active, reverse=True)


def items(node):
    """node's turns in order, each followed by the branches off it (an older fork that recorded no turn hangs at the
    end, and one that kept no turns comes first)."""
    ns = [t["n"] for t in node["turns"]]
    after = {}
    for b in node["branches"]:
        at = b.get("fork_at")
        after.setdefault(at if at == 0 or at in ns else (ns[-1] if ns else 0), []).append(b)
    out = [("fork", b) for b in after.get(0, [])]
    for t in node["turns"]:
        out += [("turn", t)] + [("fork", b) for b in after.get(t["n"], [])]
    return out


def numbers(node):
    return [t["num"] for t in node["turns"]] + [n for b in node["branches"] for n in numbers(b)]


def nodes(root):
    """root and every branch off it, at any depth."""
    return [root] + [x for b in root["branches"] for x in nodes(b)]


def layout(roots, folded=()):
    """The rows that draw roots: a head per conversation, then its turns, each branch hung after the turn it goes on
    from in a lane of its own. A row's graph holds its lanes as (text, worker) pieces; folded names the conversations
    and branches drawn shut."""
    out = []

    def walk(node, lanes, numw):  # lanes: for each lane left of node's, whether it goes on below, and whose it is
        seq = items(node)
        for i, (kind, x) in enumerate(seq):
            more = i < len(seq) - 1
            pre = [("│ " if on else "  ", w) for on, w in lanes]
            if kind == "turn":
                out.append({"kind": "turn", "node": node, "turn": x, "graph": pre, "numw": numw})
                continue
            shut, empty = x["name"] in folded, not (x["turns"] or x["branches"])
            out.append({"kind": "fork", "node": x, "parent": node, "numw": numw, "graph": pre + [
                ("├─" if more else "╰─", node["worker"]), ("─" if empty else "▸" if shut else "╮", x["worker"])]})
            if not (shut or empty):
                walk(x, lanes + [(more, node["worker"])], numw)

    for root in roots:
        numw = len(str(max(numbers(root) or [0])))
        out.append({"kind": "head", "node": root, "graph": [], "numw": numw})
        if root["name"] not in folded:
            walk(root, [], numw)
    return out


def count(node):
    """(turns, branches) in node and every branch off it."""
    sub = [count(b) for b in node["branches"]]
    return len(node["turns"]) + sum(t for t, _ in sub), len(node["branches"]) + sum(b for _, b in sub)


def pieces(row, width, mark=None, fold="", glyph=None, now=None, shut=False, here=None):
    """One row as (text, style) pieces in at most width columns: its lanes, then the conversation, branch or turn it
    shows. A conversation names its folder unless it is here. `farmout ui` passes a head's fold arrow, a turn's node
    glyph (a spinner while it runs), now (to show how long a turn has run) and shut (a folded row says what it holds)."""
    node, kind = row["node"], row["kind"]
    out = [(t, "w:" + str(w)) for t, w in row["graph"]]
    used = sum(cols(t) for t, _ in out)

    def add(text, style=""):
        nonlocal used
        text = clip(clean(text).replace("\n", " "), width - used)
        if text:
            out.append((text, style))
            used += cols(text)

    if kind in ("head", "fork"):
        info = [lane(node)]
        near = os.path.realpath(here) if here and kind == "head" else row.get("parent", {}).get("cwd")
        if node["cwd"] != near or node.get("gone"):  # here, or where its parent works, goes without saying
            info.append(short(node["cwd"]) + (" (folder gone)" if node.get("gone") else ""))
        if node.get("state") not in ("ok", "new", None):
            queued = node.get("queued") if node["state"] != "queued" else 0
            info.append(node["state"] + (f" ({queued} queued)" if queued else ""))
        if kind == "head":
            info.append(ago(node.get("updated")) + " ago")
        if shut:
            turns, branches = count(node)
            info.append(f"{turns} turn{'s' * (turns != 1)}"
                        + (f", {branches} branch{'es' * (branches != 1)}" if branches else ""))
        add(fold if kind == "head" else " ⑂ ", "w:" + node["worker"])
        add(clean(node["name"]), "bold")
        add(" · " + " · ".join(info), "dim")
        if mark and node["name"] == mark:
            add("  ◀", "acc")
        return out

    t = row["turn"]
    status = t["status"]
    style = "err" if status == "failed" else "run" if status == "running" else "w:" + str(t.get("worker") or node["worker"])
    add((glyph or NODE.get(status, "●")) + " ", style)
    add(str(t["num"]).rjust(row["numw"]), "bold")
    bits = [lane(t)] if t.get("worker") and lane(t) != lane(node) else []  # a fallback's or a switch's worker
    if status == "running" and now and t.get("started"):
        bits.append(f"running {now - t['started']:.0f}s")
    elif status != "ok":
        bits.append(status)
    meta = " · ".join(bits)
    first = next((x.strip() for x in clean(t["prompt"]).splitlines() if x.strip()), "")
    room = width - used - 2
    if meta and cols(meta) + 14 <= room:  # right-aligned, if the question keeps some room
        q = clip(first, room - cols(meta) - 2)
        add("  " + q + " " * (room - cols(q) - cols(meta)))
        add(meta, "err" if status == "failed" else "dim")
    else:
        add("  " + first)
    return out


def paint(text, style):
    """text in style's ANSI colours, for `farmout tree` on a terminal."""
    codes = [{"bold": "1", "dim": "2"}.get(s) or (f"38;5;{xterm(PALETTE[s])}" if s in PALETTE else "")
             for s in style.split()]
    codes = ";".join(c for c in codes if c)
    return f"\x1b[{codes}m{text}\x1b[0m" if codes and text.strip() else text


def xterm(hex_colour):
    """The xterm-256 colour nearest #rrggbb."""
    rgb = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    steps = [0, 95, 135, 175, 215, 255]
    cube = [min(range(6), key=lambda i: abs(steps[i] - v)) for v in rgb]
    grey = min(23, max(0, round((sum(rgb) / 3 - 8) / 10)))

    def dist(c):
        return sum((a - b) ** 2 for a, b in zip(rgb, c))
    if dist([steps[i] for i in cube]) <= dist([8 + 10 * grey] * 3):
        return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
    return 232 + grey


def text(roots, width, mark=None, colour=False, here=None):
    """`farmout tree`'s printout: each conversation's head line, then its graph."""
    def line(row):
        return "".join(paint(t, s) if colour else t for t, s in pieces(row, width, mark, here=here)).rstrip()
    return "\n\n".join("\n".join(line(r) for r in layout([root])) for root in roots)
