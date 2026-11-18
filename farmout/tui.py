"""`farmout ui`: every conversation as a graph in the terminal, each branch drawn off the turn it goes on from. Read any
turn; continue a conversation from its newest turn, branch off any turn or ask one again differently, on any worker or
model. `farmout ui --web` serves the same in a browser page.

App draws the screen into a Canvas, a grid of styled cells, and takes keys by name ("up", "enter", "^G", "x"), so the
tests drive it without a terminal; run() shows it with curses.
"""
import base64
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from . import flows, graph, sessions, ui, usage
from .graph import ago, clean, clip, cols, lane, short, take

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
WIDE = 100  # from this many columns the reader sits beside the graph
LONGEST = 200_000  # characters of an answer the reader shows
BASIC = {"err": 1, "ok": 2, "run": 3, "acc": 3, "code": 6, "w:claude": 1, "w:codex": 3, "w:antigravity": 6,
         "w:copilot": 5, "w:deepseek": 4}  # the 8-colour terminal's stand-ins for graph.PALETTE
HELP = [("↑↓ j k", "move"), ("← → h l", "fold, unfold; ← also goes up to the branch"),
        ("⏎ space", "read a turn; fold a branch"), ("Tab", "between the graph and the reader"), ("g G", "first, last"),
        ("/", "find a question, a name, or words in an answer"),
        ("c", "continue from the newest turn"), ("b", "branch from this turn"),
        ("e", "ask this turn differently: a branch from the turn before"), ("n", "new conversation"),
        ("x", "stop a running turn"), ("o O", "open the turn, or its whole conversation, in your pager"), ("y", "copy the turn's answer"),
        ("d", "an isolated session's patch, in your pager"), ("D", "delete a conversation or branch, with the branches off it"),
        ("a", "show review, ask and race runs too"), ("r q", "reload; quit")]


class Canvas:
    """The screen as rows of [character, style] cells. A wide character's second cell holds ""."""

    def __init__(self, w, h):
        self.w, self.h, self.cursor = w, h, None
        self.cells = [[[" ", ""] for _ in range(w)] for _ in range(h)]

    def put(self, y, x, text, style="", end=None):
        """Write text from column x, cut at column end (default: the right edge). Returns the column after it."""
        end = self.w if end is None else min(end, self.w)
        if not 0 <= y < self.h:
            return x
        row = self.cells[y]
        for c in text:
            c = " " if c < " " or "\x7f" <= c <= "\x9f" else c  # never a control character
            n = cols(c)
            if n == 0:  # a combining mark joins the character before it
                if 0 < x <= end and row[x - 1][0]:
                    row[x - 1][0] += c
                continue
            if x + n > end:
                break
            if row[x][0] == "" and x:  # over the second half of a wide character
                row[x - 1] = [" ", row[x - 1][1]]
            if x + n < self.w and row[x + n][0] == "":  # over the first half of one
                row[x + n] = [" ", row[x + n][1]]
            row[x] = [c, style]
            if n == 2:
                row[x + 1] = ["", style]
            x += n
        return x

    def clear(self, y, x, end, style=""):
        self.put(y, x, " " * max(0, end - x), style, end)

    def text(self):
        return "\n".join("".join(c for c, _ in row).rstrip() for row in self.cells)


class Field:
    """Text being typed, and its cursor."""

    def __init__(self, text=""):
        self.text, self.cur = text, len(text)

    def key(self, k):
        if isinstance(k, tuple) and k[0] != "paste":
            return False
        t, c = self.text, self.cur
        add = (k[1] if isinstance(k, tuple) else "\n" if k in ("^J", "alt-enter") else
               k if len(k) == 1 and k.isprintable() else None)
        if add is not None:
            add = clean(add)
            self.text, self.cur = t[:c] + add + t[c:], c + len(add)
        elif k == "backspace":
            if c:
                self.text, self.cur = t[:c - 1] + t[c:], c - 1
        elif k in ("delete", "^D"):
            self.text = t[:c] + t[c + 1:]
        elif k in ("left", "^B"):
            self.cur = max(0, c - 1)
        elif k in ("right", "^F"):
            self.cur = min(len(t), c + 1)
        elif k in ("home", "^A"):
            self.cur = t.rfind("\n", 0, c) + 1
        elif k in ("end", "^E"):
            self.cur = len(t) if t.find("\n", c) < 0 else t.find("\n", c)
        elif k in ("up", "down"):
            start = t.rfind("\n", 0, c) + 1
            if k == "up" and start:
                prev = t.rfind("\n", 0, start - 1) + 1
                self.cur = min(prev + c - start, start - 1)
            elif k == "down" and t.find("\n", c) >= 0:
                nxt = t.find("\n", c) + 1
                stop = len(t) if t.find("\n", nxt) < 0 else t.find("\n", nxt)
                self.cur = min(nxt + c - start, stop)
        elif k == "^U":
            start = t.rfind("\n", 0, c) + 1
            self.text, self.cur = t[:start] + t[c:], start
        elif k == "^K":
            stop = len(t) if t.find("\n", c) < 0 else t.find("\n", c)
            self.text = t[:c] + t[max(stop, c + 1) if stop == c else stop:]
        elif k == "^W":
            s = c
            while s and t[s - 1].isspace():
                s -= 1
            while s and not t[s - 1].isspace():
                s -= 1
            self.text, self.cur = t[:s] + t[c:], s
        else:
            return False
        return True


def flow(text, cur, width):
    """text in lines of width columns (broken at its newlines and wherever a line fills), and the cursor's line and
    column."""
    lines, at = [""], None
    for i, c in enumerate(text):
        if i == cur:
            at = (len(lines) - 1, cols(lines[-1]))
        if c == "\n":
            lines.append("")
        else:
            if cols(lines[-1]) + cols(c) > width:
                lines.append("")
            lines[-1] += c
    if at is None:
        at = (len(lines) - 1, cols(lines[-1]))
    if at[1] >= width:  # the cursor after a full line starts the next one
        if at[0] == len(lines) - 1:
            lines.append("")
        at = (at[0] + 1, 0)
    return lines, at[0], at[1]


def wrap(text, width):
    """text in lines of width columns, broken at spaces, each keeping its indent; a longer word is cut."""
    out = []
    for para in text.split("\n"):
        indent = para[:len(para) - len(para.lstrip())]
        indent = indent if cols(indent) <= width // 2 else ""
        line = indent
        for word in para.split():
            sep = " " if line.strip() else ""
            if cols(line + sep + word) <= width:
                line += sep + word
                continue
            if line.strip():
                out.append(line)
            while cols(indent + word) > width:
                part = take(word, max(1, width - cols(indent)))
                out.append(indent + part)
                word = word[len(part):]
            line = indent + word
        out.append(line if line.strip() else "")
    return out


INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def marked(line, style=""):
    """A line of an answer as (text, style) pieces: **bold** and `code` styled, their marks dropped."""
    out, at = [], 0
    for m in INLINE.finditer(line):
        out += [(line[at:m.start()], style)] if m.start() > at else []
        out.append((m.group(1), (style + " bold").strip()) if m.group(1) is not None else (m.group(2), "code"))
        at = m.end()
    return out + ([(line[at:], style)] if at < len(line) else [])


def flowed(pieces, width):
    """(text, style) pieces in lines of width columns, broken at spaces; a longer word is cut. A list item's later
    lines hang under its text, and its - or * shows as a bullet."""
    text = "".join(t for t, _ in pieces)
    lead = re.match(r" *(?:[-*+] |\d{1,3}[.)] )?", text).group(0)
    lead = lead if cols(lead) <= width // 2 else ""
    room, words, word = max(1, width - cols(lead)), [], []
    for c, s in [(c, s) for t, s in pieces for c in t][len(lead):] + [(" ", "")]:
        if c != " ":
            word.append((c, s))
        elif word:
            words.append(word)
            word = []
    lines, line, used = [], [], 0
    for word in words:
        while word:
            n, gap = sum(cols(c) for c, _ in word), 1 if line else 0
            if used + gap + n <= room:
                line, used = line + [(" ", "")] * gap + word, used + gap + n
                break
            if line:  # it starts the next line
                lines.append(line)
                line, used = [], 0
                continue
            k = w = 0
            while k < len(word) and w + cols(word[k][0]) <= room:
                w, k = w + cols(word[k][0]), k + 1
            lines.append(word[:max(k, 1)])
            word = word[max(k, 1):]
    out = []
    for i, ln in enumerate(lines + [line] if line or not lines else lines):
        merged = [(re.sub(r"[-*+] $", "• ", lead) if i == 0 else " " * cols(lead), "")]
        for c, s in ln:
            merged[-1:] = [(merged[-1][0] + c, s)] if merged[-1][1] == s else [merged[-1], (c, s)]
        out.append([p for p in merged if p[0]])
    return out


def chop(line, width):
    """A line of code in pieces of width columns, its spacing kept."""
    out = []
    while cols(line) > width:
        part = take(line, max(1, width))
        out.append(part)
        line = line[len(part):]
    return out + [line]


class App:
    """The screen's state. key() takes a key, tick() runs when none came, frame() draws it."""

    def __init__(self, here, focus=None):
        self.here = here
        self.folded, self.everything = set(), False
        self.sel, self.i, self.top, self.rtop, self.rpage = None, 0, 0, 0, 10
        self.pane, self.box, self.note, self.help, self.done = "graph", None, None, False, False
        self.external = None  # ("pager" | "editor", text): run() leaves the screen for it
        self.wanted = None  # (session, message id): select that turn once it shows up
        self.drafts, self.answers, self.cache = {}, {}, (None, None)
        self.hit, self.geo, self.rw = {}, {}, 0
        self.load(first=True, focus=focus)

    # The conversations

    def load(self, first=False, focus=None):
        snap = ui.snapshot(self.here)
        self.workers = list(snap["workers"])
        self.roots = graph.forest(snap["sessions"], self.everything)
        self.by = {}

        def index(node):
            self.by[node["name"]] = node
            for b in node["branches"]:
                index(b)
        for r in self.roots:
            index(r)
        why = usage.reasons()
        self.held = {k: (until, why.get(k)) for k, until in usage.limits().items()}  # out of quota, or logged out
        self.loaded = time.time()
        if first:
            self.folded.update(r["name"] for r in self.roots[3:])  # the three most recent conversations open
            start = self.by.get(focus) or (self.roots[0] if self.roots else None)
            if focus and focus not in self.by:
                self.say(f"No conversation named {focus}", "err")
            if start:
                self.show(*self.newest(start, deep=start["name"] != focus))
        if self.wanted and self.wanted[0] in self.by:
            name, msg = self.wanted
            t = next((t for t in self.by[name]["turns"] if t.get("id") == msg), None)
            if t:
                self.wanted = None
                return self.show(name, t["n"])
        self.relayout()

    def newest(self, node, deep=True):
        """(session, turn) of the newest turn in node, or with deep, anywhere in its tree."""
        when, at = -1, (node["name"], None)
        for n in graph.nodes(node) if deep else [node]:
            for t in n["turns"]:
                if (t.get("started") or 0) >= when:
                    when, at = t.get("started") or 0, (n["name"], t["n"])
        return at

    def show(self, name, n=None):
        """Select a session's turn n (or its head or branch row), opening the branches it sits in."""
        node = self.by.get(name)
        self.unfold(name if n else node.get("parent") if node else None)
        self.sel = ("turn", name, n) if n else ("head" if node and not node.get("parent") in self.by else "fork", name)
        self.rtop = 0
        self.relayout()

    def unfold(self, name):
        while name in self.by:
            self.folded.discard(name)
            name = self.by[name].get("parent")

    def relayout(self):
        self.rows = graph.layout(self.roots, self.folded)
        self.lines = []
        for i, r in enumerate(self.rows):
            if r["kind"] == "head" and self.lines:
                self.lines.append(None)  # a blank line between conversations
            self.lines.append(i)
        keys = [self.key_of(r) for r in self.rows]
        name = self.sel[1] if self.sel else None
        while self.sel not in keys and name in self.by:  # hidden in a folded branch: its nearest open row
            self.sel = next((k for k in (("fork", name), ("head", name)) if k in keys), self.sel)
            name = self.by[name].get("parent")
        if self.sel not in keys:
            self.sel = keys[min(self.i, len(keys) - 1)] if keys else None
        self.i = keys.index(self.sel) if self.sel else 0

    @staticmethod
    def key_of(row):
        name = row["node"]["name"]
        return ("turn", name, row["turn"]["n"]) if row["kind"] == "turn" else (row["kind"], name)

    def row(self):
        return self.rows[self.i] if self.rows else None

    def busy(self):
        return [n for n in self.by.values() if n.get("state") in ("running", "queued")]

    def say(self, text, style=""):
        self.note = (text, style, time.time())

    def tick(self):
        if time.time() - self.loaded > (1.5 if self.busy() else 6):
            self.load()
        if self.note and time.time() - self.note[2] > 8:
            self.note = None

    def wait_ms(self):
        return 120 if self.busy() else 1000

    def detail(self, name, n, status):
        k = (name, n, status)
        if k not in self.answers:
            try:
                self.answers[k] = ui.detail(name, n)
            except (KeyError, OSError) as e:
                return {"n": n, "status": status, "text": f"(this turn can't be read: {e})"}
        return self.answers[k]

    # Keys

    def key(self, k):
        if k == "resize":
            return
        self.note = None
        if self.help:
            self.help = False
            return
        if k == "^C" and not self.box:
            return self.quit()
        if self.box:
            return {"compose": self.compose_key, "find": self.find_key, "ask": self.ask_key}[self.box["kind"]](k)
        if isinstance(k, tuple):
            return self.mouse(*k[1:]) if k[0] == "mouse" else None
        self.wanted = None  # a key takes over from waiting for a sent message's turn
        if self.pane == "reader" and self.reader_key(k):
            return
        row = self.row()
        node = row["node"] if row else None
        act = {"up": lambda: self.move(-1), "k": lambda: self.move(-1), "down": lambda: self.move(1),
               "j": lambda: self.move(1), "pgup": lambda: self.move(-10), "pgdn": lambda: self.move(10),
               "home": lambda: self.move(-len(self.rows)), "g": lambda: self.move(-len(self.rows)),
               "end": lambda: self.move(len(self.rows)), "G": lambda: self.move(len(self.rows)),
               "left": self.left, "h": self.left, "right": self.right, "l": self.right,
               "enter": self.enter, " ": self.enter, "tab": self.swap, "btab": self.swap,
               "c": lambda: self.compose("continue"), "b": lambda: self.compose("branch"),
               "e": lambda: self.compose("edit"), "n": lambda: self.compose("new"), "x": self.stop,
               "/": self.find, "o": self.page, "O": self.whole, "y": self.yank, "d": self.patch, "D": self.delete, "a": self.all_runs,
               "r": self.load, "?": self.open_help, "q": self.quit}.get(k)
        if act and (node or k in ("n", "a", "r", "?", "q")):
            act()

    def move(self, d):
        if self.rows:
            self.i = max(0, min(len(self.rows) - 1, self.i + d))
            self.sel, self.rtop = self.key_of(self.rows[self.i]), 0

    def fold(self, name, shut):
        (self.folded.add if shut else self.folded.discard)(name)
        self.relayout()

    def left(self):
        row = self.row()
        node = row["node"]
        if row["kind"] != "turn" and node["name"] not in self.folded and (node["turns"] or node["branches"]):
            return self.fold(node["name"], True)
        up = node["name"] if row["kind"] == "turn" else node.get("parent")
        if up in self.by:
            self.show(up)

    def right(self):
        row = self.row()
        if row["kind"] != "turn" and row["node"]["name"] in self.folded:
            return self.fold(row["node"]["name"], False)
        self.move(1)

    def enter(self):
        row = self.row()
        if row["kind"] != "turn":
            return self.fold(row["node"]["name"], row["node"]["name"] not in self.folded)
        self.pane = "reader"

    def swap(self):
        self.pane = "graph" if self.pane == "reader" else "reader"

    def reader_key(self, k):
        page = max(1, self.rpage - 1)
        d = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -page, "pgdn": page, " ": page,
             "home": -10 ** 9, "g": -10 ** 9, "end": 10 ** 9, "G": 10 ** 9}.get(k)
        if d is not None:
            self.rtop = max(0, self.rtop + d)
            return True
        if k in ("esc", "left", "h", "tab", "btab"):
            self.pane = "graph"
            return True
        return False

    def mouse(self, x, y, kind):
        for pane, (px, py, pw, ph) in self.geo.items():
            if not (px <= x < px + pw and py <= y < py + ph):
                continue
            if pane == "reader":
                if kind == "press":
                    self.pane = "reader"
                else:
                    self.rtop = max(0, self.rtop + (3 if kind == "down" else -3))
            elif kind != "press":
                self.move(3 if kind == "down" else -3)
            elif y in self.hit:
                again = self.hit[y] == self.i
                self.i, self.pane = self.hit[y], "graph"
                self.sel, self.rtop = self.key_of(self.rows[self.i]), 0
                row = self.row()
                lanes = sum(cols(t) for t, _ in row["graph"]) + px + 2
                if row["kind"] != "turn" and (x <= lanes + 1 or again):  # the arrow, or a second click, folds
                    self.fold(row["node"]["name"], row["node"]["name"] not in self.folded)
                elif row["kind"] == "turn" and again:
                    self.pane = "reader"
            return

    def all_runs(self):
        self.everything = not self.everything
        self.say("Showing review, ask, race and pipeline runs too" if self.everything else "Showing conversations only",
                 "dim")
        self.load()

    def open_help(self):
        self.help = True

    def quit(self):
        self.done = True

    def page(self):
        row = self.row()
        if row["kind"] != "turn":
            return self.say("Pick a turn to open", "dim")
        self.external = ("pager", clean("\n".join("".join(t for t, _ in line) for line in self.reading(row, 100))))

    def whole(self):
        node = self.row()["node"]
        try:  # a branch's conversation includes the turns before it, as `farmout export` writes it
            text = sessions.transcript(node["name"])
        except (OSError, KeyError) as e:
            return self.say(f"Couldn't read {node['name']}: {e}", "err")
        self.external = ("pager", clean(f"# {node['name']} ({lane(node)})\n\n{text}"))

    def yank(self):
        row = self.row()
        if row["kind"] != "turn" or row["turn"]["status"] == "running":
            return self.say("Pick a turn that has answered to copy", "dim")
        t = row["turn"]
        text = clean(self.detail(row["node"]["name"], t["n"], t["status"]).get("text") or "")
        self.external = ("copy", text, f"turn {t['num']}'s answer ({len(text):,} characters)")

    def patch(self):
        node = self.row()["node"]
        name = node["name"]
        if node.get("state") in ("running", "queued"):  # its worker is still editing that worktree
            return self.say(f"{name} is still working: its patch is ready when it's done", "dim")
        try:
            path, stat, _ = flows.current_patch(name)
            self.external = ("pager", clean(stat) + "\n\n" + clean(path.read_text(errors="replace")))
        except (ValueError, OSError, KeyError) as e:
            self.say(str(e), "dim")

    def delete(self):
        node = self.row()["node"]
        tree = graph.nodes(node)  # it, and every branch off it at any depth
        busy = [n["name"] for n in tree if n.get("state") in ("running", "queued")]
        if busy:
            return self.say(f"{', '.join(busy)} {'is' if len(busy) == 1 else 'are'} running: x stops it first", "err")
        turns, branches = graph.count(node)

        def yes():
            names = [n["name"] for n in tree]
            late = [n for n in names if sessions.busy(n) or sessions.pending(n)]  # a message came in meanwhile
            if late:
                return self.say(f"{', '.join(late)} has a message to answer now: x stops it first", "err")
            gone = 0
            try:
                for n in reversed(names):  # branches before what they branch off
                    sessions.remove(n)
                    gone += 1
                self.say(f"Deleted {node['name']}" + (f" and the {gone - 1} branch{'es' * (gone != 2)} off it"
                                                      if gone > 1 else ""), "ok")
            except (OSError, KeyError, RuntimeError) as e:
                self.say(f"Couldn't delete all of {node['name']}: {e}", "err")
            self.load()
        what = f"{turns} turn{'s' * (turns != 1)}" + (f" and the {branches} branch{'es' * (branches != 1)} off it"
                                                       if branches else "")
        self.box = {"kind": "ask", "yes": yes, "text": f"Delete {node['name']} with its {what}"
                    + (" and worktree" if node.get("isolate") else "") + "? This can't be undone."}

    def stop(self):
        node = self.row()["node"]
        if node.get("state") not in ("running", "queued"):
            return self.say(f"Nothing is running in {node['name']}", "dim")

        def yes():
            try:
                self.say(f"Stopped {node['name']}" if sessions.stop(node["name"]) else f"{node['name']} had finished",
                         "ok")
            except (OSError, KeyError) as e:  # it ended as you pressed y
                self.say(f"Couldn't stop {node['name']}: {e}", "err")
            self.load()
        self.box = {"kind": "ask", "yes": yes,
                    "text": f"Stop {node['name']}? Its running turn ends and its queued messages are dropped."}

    def ask_key(self, k):
        yes, self.box = self.box["yes"], None
        if k in ("y", "Y"):
            yes()

    # Writing a message

    def compose(self, mode):
        row = self.row()
        if mode == "new":
            if not self.workers:
                return self.say("No worker is set up here: `farmout doctor` says why", "err")
            worker = row["node"]["worker"] if row and row["node"]["worker"] in self.workers else self.workers[0]
            box = {"session": None, "n": None, "lane": Field(worker), "choices": self.workers,
                   "title": f"New conversation in {short(self.here)}"}
        else:
            node = row["node"]
            own = [t["n"] for t in node["turns"]]
            n = row["turn"]["n"] if row["kind"] == "turn" else own[-1] if own else 0
            base, newest = node.get("base") or 0, own[-1] if own else 0
            num = base + n
            if mode == "continue" and n != newest:
                return self.say(f"Turn {num} isn't the newest in {node['name']}: b branches from it, or continue from "
                                f"turn {base + newest}", "err")
            if mode == "edit" and row["kind"] != "turn":
                return self.say("Pick a turn to ask differently", "err")
            if mode == "branch" and any(t["n"] == n and t["status"] == "running" for t in node["turns"]):
                return self.say(f"Turn {num} is still running: branch from it once it's done", "err")
            box = {"session": node["name"], "n": n, "lane": Field(""), "choices": [""] + self.workers, "same": lane(node),
                   "title": {"continue": f"Continue {node['name']} after turn {num}",
                             "branch": f"Branch from turn {num} of {node['name']}",
                             "edit": f"Ask turn {num} differently: a branch "
                                     + (f"after turn {num - 1}" if num > 1 else "from the start")}[mode]}
        key = (mode, box["session"], box["n"])
        text = self.drafts.get(key)
        if text is None and mode == "edit":
            text = self.detail(box["session"], box["n"], row["turn"]["status"]).get("prompt") or row["turn"]["prompt"]
        self.box = {**box, "kind": "compose", "mode": mode, "key": key, "text": Field(text or ""), "field": "text"}

    def compose_key(self, k):
        b = self.box
        if k == "enter":
            return self.send()
        if k in ("esc", "^C"):
            self.drafts[b["key"]] = b["text"].text
            self.box = None
        elif k in ("tab", "btab"):
            b["field"] = "lane" if b["field"] == "text" else "text"
        elif k == "^G":
            self.external = ("editor", b["text"].text)
        elif b["field"] == "lane" and k in ("up", "down"):
            ch = b["choices"]
            now = b["lane"].text.strip()
            at = ch.index(now) if now in ch else -1
            b["lane"] = Field(ch[(at + (1 if k == "down" else -1)) % len(ch)])
        elif isinstance(k, tuple) and k[0] == "mouse":
            pass
        elif b["field"] == "lane":
            if not (k == "^J" or (isinstance(k, tuple) and "\n" in k[1])):
                b["lane"].key(k)
        else:
            b["text"].key(k)

    def edited(self, text):
        if self.box and self.box["kind"] == "compose":
            self.box["text"] = Field(text)

    def send(self):
        b = self.box
        prompt = b["text"].text.strip()
        if not prompt:
            return self.say("Write a message first", "err")
        body = {"mode": "branch" if b["mode"] == "edit" else b["mode"], "edit": b["mode"] == "edit",
                "session": b["session"], "n": b["n"], "prompt": prompt, "worker": b["lane"].text.strip() or None}
        try:
            r = ui.send(body, self.here)
        except Exception as e:  # a refusal or a failure is shown, never fatal
            return self.say(str(e.args[0] if isinstance(e, KeyError) and e.args else e) or type(e).__name__, "err")
        self.drafts.pop(b["key"], None)
        self.box, self.pane, self.wanted = None, "graph", (r["session"], r["message"])
        self.say(f"Sent to {r['session']}" + (f" (its folder moved to {r['moved']})" if r.get("moved") else ""), "ok")
        self.load()

    # Finding

    def find(self):
        self.box = {"kind": "find", "text": Field(""), "back": (self.sel, set(self.folded)), "hits": [], "at": 0}

    def find_key(self, k):
        b = self.box
        if k == "enter":
            self.box = None
        elif k in ("esc", "^C"):
            self.sel, self.folded = b["back"]
            self.box = None
            self.relayout()
        elif k in ("up", "down", "tab", "btab"):
            if b["hits"]:
                b["at"] = (b["at"] + (-1 if k in ("up", "btab") else 1)) % len(b["hits"])
                self.go(b["hits"][b["at"]])
        elif b["text"].key(k):
            q = b["text"].text.strip().lower()

            def hit(r):  # a name or a question, or from three letters on an answer too
                t = r.get("turn")
                if not t:
                    return q in r["node"]["name"].lower()
                return q in t["prompt"].lower() or (len(q) >= 3 and t["status"] != "running" and q in (
                    self.detail(r["node"]["name"], t["n"], t["status"]).get("text") or "").lower())
            b["hits"] = [self.key_of(r) for r in graph.layout(self.roots) if q and hit(r)]
            b["at"] = 0
            if b["hits"]:
                self.go(b["hits"][0])

    def go(self, key):
        self.show(key[1], key[2] if key[0] == "turn" else None)
        q = self.box["text"].text.strip().lower() if self.box and self.box["kind"] == "find" else ""
        row = self.row()
        if q and row and row["kind"] == "turn" and self.rw:  # the reader opens where the answer says it
            lines = ["".join(t for t, _ in line).lower() for line in self.reading(row, self.rw)]
            heads = [i for i, line in enumerate(lines) if line.startswith("▌")]  # "▌You", then the answer's
            at = next((i for i in range(heads[1] if len(heads) > 1 else 0, len(lines)) if q in lines[i]), None)
            self.rtop = max(0, (at if at is not None else next((i for i, x in enumerate(lines) if q in x), 3)) - 3)

    # Drawing

    def frame(self, w, h):
        c = Canvas(w, h)
        self.hit, self.geo = {}, {}
        if w < 40 or h < 10:
            c.put(0, 0, clip("farmout ui needs at least 40×10", w), "bold")
            return c
        ph = h - (self.composer_rows(w, h) + 2 if self.box and self.box["kind"] == "compose" else 1)
        if w >= WIDE:
            gw = max(44, min(w - 40, w * 11 // 20))
            self.draw_graph(c, 0, 0, gw, ph, self.pane == "graph")
            self.draw_reader(c, gw, 0, w - gw, ph, self.pane == "reader")
        elif self.pane == "reader":
            self.draw_reader(c, 0, 0, w, ph, True)
        else:
            self.draw_graph(c, 0, 0, w, ph, True)
        self.draw_bottom(c, w, h)
        if self.help:
            self.draw_help(c, w, h)
        return c

    def border(self, c, x, y, w, h, style, title=(), right=(), foot=()):
        c.put(y, x, "╭" + "─" * (w - 2) + "╮", style)
        for k in range(y + 1, y + h - 1):
            c.put(k, x, "│", style)
            c.clear(k, x + 1, x + w - 1)
            c.put(k, x + w - 1, "│", style)
        c.put(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", style)
        end = start = x + w - 2
        if right:
            start = xx = max(x + 2, end - sum(cols(t) for t, _ in right))
            for t, s in right:
                xx = c.put(y, xx, t, s, end)
        for row, pieces, stop in ((y, title, start - 1 if right else end), (y + h - 1, foot, end)):
            xx = x + 2
            for t, s in pieces:
                xx = c.put(row, xx, t, s, stop)
        return start

    @staticmethod
    def bar(c, x, y, h, top, total, style):
        """A scrollbar thumb on a pane's right border, when it holds more than it shows."""
        if total > h:
            size = max(1, h * h // total)
            at = min(h - size, top * h // total)
            for k in range(size):
                c.put(y + at + k, x, "┃", style)

    def draw_graph(self, c, x, y, w, h, focus):
        style = "acc" if focus else "dim"
        busy, n = len(self.busy()), len(self.roots)
        info = f" {n} conversation{'s' * (n != 1)}" + (f" · {busy} running" if busy else "") + " "
        foot = [p for k in sorted(self.held) for p in ((f" {self.out(k)} ", "err" if self.held[k][1] == "login" else "run"),
                                                       ("─", style))][:-1]
        self.border(c, x, y, w, h, style, [(" farmout ", "bold acc"), ("─", style), (info, "dim")], foot=foot)
        self.geo["graph"] = (x, y, w, h)
        iw, ih = w - 4, h - 2
        if not self.rows:
            for k, line in enumerate(["No conversations yet.", "", "Press n to start one here, or from any terminal:",
                                      "  farmout run codex \"...\""]):
                c.put(y + 2 + k, x + 2, line, "dim" if k else "bold", x + w - 2)
            return
        at = self.lines.index(self.i)
        if at <= self.top:
            self.top = max(0, at - 1)
        elif at >= self.top + ih - 1:
            self.top = at - ih + 2
        self.top = max(0, min(self.top, len(self.lines) - ih))
        hits = set(self.box["hits"]) if self.box and self.box["kind"] == "find" else ()
        for k, ri in enumerate(self.lines[self.top:self.top + ih]):
            if ri is None:
                continue
            row, yy, xx = self.rows[ri], y + 1 + k, x + 2
            sel = ri == self.i
            node = row["node"]
            glyph = None
            if row["kind"] == "turn":
                st = row["turn"]["status"]
                glyph = SPIN[int(time.time() * 8) % len(SPIN)] if st == "running" else "◉" if sel and st == "ok" else None
            fold = ("▸ " if node["name"] in self.folded else "▾ ") if row["kind"] == "head" else ""
            pieces = graph.pieces(row, iw, fold=fold, glyph=glyph, now=time.time(), shut=node["name"] in self.folded,
                                  here=self.here)
            for m, (t, s) in enumerate(pieces):
                if m >= len(row["graph"]):
                    s += (" sel" if sel else "") + (" ul" if self.key_of(row) in hits else "")
                xx = c.put(yy, xx, t, s, x + 2 + iw)
            if sel:
                c.clear(yy, xx, x + 2 + iw, "sel")
            self.hit[yy] = ri
        self.bar(c, x + w - 1, y + 1, ih, self.top, len(self.lines), style)

    def out(self, lane_name):
        """"claude logged out till 23:51" if that lane can't run now (at its limit, or not logged in), else ""."""
        k = next((k for k in (lane_name, lane_name.split(":")[0]) if k in self.held), None)
        if not k:
            return ""
        until, why = self.held[k]
        return f"{k} {'logged out' if why == 'login' else 'at its limit'} till {time.strftime('%H:%M', time.localtime(until))}"

    def draw_reader(self, c, x, y, w, h, focus):
        style = "acc" if focus else "dim"
        row = self.row()
        title = []
        if row:
            node = row["node"]
            mark = {"head": "", "fork": "⑂ "}.get(row["kind"], "")
            title = [(" " + mark + node["name"], "bold"),
                     (f" · turn {row['turn']['num']} " if row["kind"] == "turn" else " ", "bold")]
        self.border(c, x, y, w, h, style, title)
        self.geo["reader"] = (x, y, w, h)
        if not row:
            return
        lines = self.reading(row, w - 4)
        self.rw = w - 4
        ih = h - 2
        self.rpage = ih
        self.rtop = max(0, min(self.rtop, len(lines) - ih))
        for k, line in enumerate(lines[self.rtop:self.rtop + ih]):
            xx = x + 2
            for t, s in line:
                xx = c.put(y + 1 + k, xx, t, s, x + w - 2)
        self.bar(c, x + w - 1, y + 1, ih, self.rtop, len(lines), style)

    def reading(self, row, w):
        """The selected row's text, as lines of (text, style) pieces in w columns."""
        node = row["node"]
        t = row.get("turn")
        key = (self.key_of(row), t["status"] if t else (node.get("state"), len(node["turns"]), len(node["branches"])), w)
        if self.cache[0] == key:
            return self.cache[1]
        out = []

        def say(text, style=""):
            out.extend([(line, style)] for line in wrap(clean(text), w))
        if t:
            d = self.detail(node["name"], t["n"], t["status"])
            status = d.get("status") or t["status"]
            if status != t["status"]:  # the turn moved on since the last load: load again, so the graph agrees
                self.loaded = 0
            who = clean(lane(d) if d.get("worker") else lane(node))
            bits = [who] + ([f"{d['secs']:.0f}s"] if d.get("secs") is not None else [])
            bits.append(status)
            say(" · ".join(bits + ([ago(d["started"]) + " ago"] if d.get("started") else [])),
                "err" if status == "failed" else "dim")
            if node.get("parent"):
                say(f"⑂ a branch of {node['parent']}, after its turn {node.get('base') or 0}", "dim")
            if node.get("gone"):
                say(f"Its folder is gone: {short(node['cwd'])}", "err")
            out.append([])
            out.append([("▌", "acc"), ("You", "bold acc")])
            say(clean(d.get("prompt") or t["prompt"]))
            out.append([])
            colour = "w:" + str(d.get("worker") or node["worker"])
            out.append([("▌", colour), (who, "bold " + colour)])
            if status == "running":
                say("Working on it. The answer shows here when it's done.", "dim")
            else:
                fence, text = False, clean(d.get("text") or "(no answer)")
                if len(text) > LONGEST:
                    text = text[:LONGEST] + f"\n\n(cut here: `farmout export {node['name']}` has all of it)"
                for line in text.split("\n"):
                    if line.lstrip().startswith("```"):
                        fence = not fence
                        out.append([(clip(line.strip(), w), "dim")])
                    elif fence:
                        out.extend([(part, "code")] for part in chop(line, w))
                    elif re.match(r"#{1,6} ", line):
                        out.extend(flowed(marked(line.lstrip("#").strip(), "bold"), w))
                    else:
                        out.extend(flowed(marked(line, "err" if status == "failed" else ""), w))
            if d.get("notes"):
                out.append([])
                for note in d["notes"]:
                    say("· " + clean(note), "dim")
        else:
            turns, branches = graph.count(node)
            say(lane(node) + " · " + short(node["cwd"]) + (" (folder gone)" if node.get("gone") else ""), "dim")
            if node.get("parent"):
                say(f"⑂ a branch of {node['parent']}, after its turn {node.get('base') or 0}", "dim")
            say(f"{turns} turn{'s' * (turns != 1)}, {branches} branch{'es' * (branches != 1)} · {node.get('state')}"
                f" · started {ago(node.get('created'))} ago · last {ago(node.get('updated'))} ago", "dim")
            out.append([])
            say("⏎ folds it or opens it. c continues from its newest turn; b branches from there.", "dim")
        self.cache = (key, out)
        return out

    def draw_bottom(self, c, w, h):
        b, y = self.box, h - 1
        if b and b["kind"] == "compose":
            return self.draw_composer(c, w, h)
        c.clear(y, 0, w)
        if b and b["kind"] == "find":
            x = c.put(y, 1, "/ ", "bold acc")
            x0, x = x, c.put(y, x, b["text"].text, "", w - 30)
            c.cursor = (y, x0 + cols(b["text"].text[:b["text"].cur]))
            q = b["text"].text.strip()
            n = len(b["hits"])
            info = (f"{b['at'] + 1} of {n}" if n else "no match" if q else "type to find a question or an answer")
            info += " · ↑↓ next · ⏎ go · Esc back"
            c.put(y, max(x + 2, w - cols(info) - 1), info, "dim")
        elif b and b["kind"] == "ask":
            x = c.put(y, 1, b["text"], "bold acc", w - 12)
            c.put(y, x + 2, "y / n", "bold")
        elif self.note:
            c.put(y, 1, self.note[0], self.note[1], w - 1)
        else:
            row = self.row()
            keys = ([("↑↓", "scroll"), ("Tab", "graph")] if self.pane == "reader" else
                    [("↑↓", "move"), ("←→", "fold"), ("⏎", "read")]) if row else []
            keys += [("c", "continue"), ("b", "branch"), ("e", "ask differently")] if row else []
            keys += [("n", "new")] + ([("x", "stop")] if row and row["node"].get("state") in ("running", "queued") else [])
            keys += [("/", "find"), ("?", "help"), ("q", "quit")]
            x = 1
            for k, label in keys:
                if x + cols(k) + cols(label) + 3 > w:
                    break
                x = c.put(y, x, k, "bold acc")
                x = c.put(y, x, " " + label + "   ", "dim")

    def composer_rows(self, w, h):
        return max(1, min(len(flow(self.box["text"].text, self.box["text"].cur, w - 4)[0]), 8, h // 3))

    def draw_composer(self, c, w, h):
        b = self.box
        lines, cy, cx = flow(b["text"].text, b["text"].cur, w - 4)
        shown = self.composer_rows(w, h)
        first = max(0, min(cy - shown + 1, len(lines) - shown))
        top = h - shown - 2
        typed = b["lane"].text
        on = typed if typed or b["field"] == "lane" or "same" not in b else f"same ({b['same']})"
        out = self.out(typed.strip() or b.get("same") or "")
        keys = " ⏎ send · ^J new line · Tab worker" + (" (↑↓ picks)" if b["field"] == "lane" else "")
        keys += " · ^G editor · Esc close "
        start = self.border(c, 0, top, w, shown + 2, "acc", [(" " + b["title"] + " ", "bold acc")],
                            [(" on ", "dim"), (on or " ", "sel bold" if b["field"] == "lane" else "bold"),
                             (f" · {out} ", "err") if out else (" ", "")],
                            [(keys, "dim")])
        if not b["text"].text:
            c.put(top + 1, 2, "Write your message", "dim", w - 2)
        for k in range(shown):
            c.put(top + 1 + k, 2, lines[first + k], "", w - 2)
        if b["field"] == "lane":
            c.cursor = (top, min(w - 3, start + 4 + cols(typed[:b["lane"].cur])))
        else:
            c.cursor = (top + 1 + cy - first, 2 + cx)

    def draw_help(self, c, w, h):
        width = min(w - 4, 78)
        lines = [(k, v) for k, v in HELP]
        bh = min(h - 2, len(lines) + 5)
        x, y = (w - width) // 2, max(0, (h - bh) // 2)
        self.border(c, x, y, width, bh, "acc", [(" Keys ", "bold acc")], foot=[(" any key closes this ", "dim")])
        for k, (keys, what) in enumerate(lines[:bh - 5]):
            xx = c.put(y + 1 + k, x + 2, keys.ljust(10), "bold acc", x + width - 2)
            c.put(y + 1 + k, xx, what, "", x + width - 2)
        for k, line in enumerate(wrap("Turns are numbered along the whole conversation: a branch keeps the turns before "
                                      "it. From a shell: farmout fork <session> --at <turn> \"...\"", width - 4)[:2]):
            c.put(y + bh - 3 + k, x + 2, line, "dim", x + width - 2)


# The terminal

def run(here, focus=None):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.stderr.write("farmout ui draws in a terminal, and this isn't one. For a browser page: farmout ui --web; "
                         "for a printout: farmout tree\n")
        return 2
    import curses
    import locale
    try:
        curses.setupterm()
        moves = curses.tigetstr("cup") is not None  # a "dumb" terminal can't put text where it wants
    except curses.error:  # no TERM, or one this system doesn't know
        moves = False
    if not moves:
        sys.stderr.write(f"farmout ui can't draw on this terminal (TERM={os.environ.get('TERM')!r}). Try "
                         "TERM=xterm-256color farmout ui, or farmout ui --web; farmout tree prints the graph\n")
        return 2
    try:
        locale.setlocale(locale.LC_CTYPE, "")  # so curses writes UTF-8
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")
    app = App(here, focus)
    try:
        curses.wrapper(loop, app)
    except KeyboardInterrupt:
        pass
    return 0


def loop(scr, app):
    import curses
    curses.nonl()  # Enter arrives as \r and Ctrl+J as \n, so they can mean different things
    scr.keypad(True)
    wheel_down = getattr(curses, "BUTTON5_PRESSED", 0x200000)
    curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON4_PRESSED | wheel_down)
    curses.mouseinterval(0)
    attr = styles(curses)
    os.write(sys.stdout.fileno(), b"\x1b[?2004h")  # bracketed paste: a pasted newline doesn't send the message
    try:
        while not app.done:
            h, w = scr.getmaxyx()
            blit(scr, app.frame(w, h), attr, curses)
            scr.timeout(app.wait_ms())
            try:
                k = read(scr, curses, wheel_down)
            except KeyboardInterrupt:  # Ctrl+C: closes the composer or the finder, or quits
                k = "^C"
            try:
                app.tick() if k is None else app.key(k)
                if app.external:
                    outside(scr, app, curses)
            except Exception as e:  # shown, not fatal: a draft being written survives it
                app.say(f"{type(e).__name__}: {e}", "err")
    finally:
        os.write(sys.stdout.fileno(), b"\x1b[?2004l")


def styles(curses):
    """Style words -> curses attributes; colours unless NO_COLOR is set or the terminal has none."""
    attrs = {"bold": curses.A_BOLD, "dim": curses.A_DIM, "sel": curses.A_REVERSE, "ul": curses.A_UNDERLINE}
    if os.environ.get("NO_COLOR") or not curses.has_colors():
        return attrs
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = 0
    for i, (name, hex_colour) in enumerate(graph.PALETTE.items(), 1):
        fg = graph.xterm(hex_colour) if curses.COLORS >= 256 else BASIC[name]
        if i < curses.COLOR_PAIRS:
            curses.init_pair(i, fg, bg)
            attrs[name] = curses.color_pair(i)
    return attrs


def blit(scr, canvas, attrs, curses):
    cache = {}
    scr.erase()
    for y, row in enumerate(canvas.cells):
        x = 0
        while x < canvas.w:
            style, end = row[x][1], x
            while end < canvas.w and row[end][1] == style:
                end += 1
            if style not in cache:
                cache[style] = 0
                for word in style.split():
                    cache[style] |= attrs.get(word, 0)
            try:
                scr.addstr(y, x, "".join(ch for ch, _ in row[x:end]), cache[style])
            except curses.error:  # the bottom-right cell can't move the cursor on
                pass
            x = end
    try:
        curses.curs_set(1 if canvas.cursor else 0)
        if canvas.cursor:
            scr.move(*canvas.cursor)
    except curses.error:
        pass
    scr.refresh()


NAMES = {"KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_PPAGE": "pgup",
         "KEY_NPAGE": "pgdn", "KEY_HOME": "home", "KEY_END": "end", "KEY_BACKSPACE": "backspace", "KEY_DC": "delete",
         "KEY_ENTER": "enter", "KEY_BTAB": "btab", "KEY_RESIZE": "resize"}


def read(scr, curses, wheel_down):
    """The next key, by name; None when none came in time."""
    try:
        ch = scr.get_wch()
    except curses.error:
        return None
    if ch == curses.KEY_MOUSE:
        try:
            _, x, y, _, b = curses.getmouse()
        except curses.error:
            return None
        kind = ("up" if b & curses.BUTTON4_PRESSED else "down" if b & wheel_down else
                "press" if b & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED) else None)
        return ("mouse", x, y, kind) if kind else None
    if isinstance(ch, int):
        return next((name for key, name in NAMES.items() if getattr(curses, key, None) == ch), None)
    if ch == "\x1b":
        return escape(scr, curses)
    return {"\r": "enter", "\n": "^J", "\t": "tab", "\x7f": "backspace", "\x08": "backspace"}.get(ch) or (
        "^" + chr(ord(ch) + 64) if ord(ch) < 32 else ch)


def escape(scr, curses):
    """Esc alone, Alt+Enter, or a bracketed paste (ESC [200~ ... ESC [201~)."""
    scr.timeout(30)
    seq = ""
    try:
        while "[200~".startswith(seq) and seq != "[200~":
            c = scr.get_wch()
            if not isinstance(c, str):
                break
            seq += c
    except curses.error:
        pass
    if seq in ("\r", "\n"):
        return "alt-enter"
    if seq != "[200~":
        return "esc" if not seq else None
    buf = ""
    scr.timeout(500)
    while not buf.endswith("\x1b[201~"):
        try:
            c = scr.get_wch()
        except curses.error:  # the end marker never came
            break
        if isinstance(c, str):
            buf += c
    return ("paste", buf[:-6] if buf.endswith("\x1b[201~") else buf)


def copy(text):
    """Put text on the clipboard, through the system's clipboard tool or else the terminal (OSC 52, which most
    terminals and tmux take, over ssh too). Says which."""
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["pbcopy"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, check=True)
                return cmd[0]
            except (OSError, subprocess.SubprocessError):
                continue
    os.write(sys.stdout.fileno(), b"\x1b]52;c;" + base64.b64encode(text.encode()) + b"\x07")
    return "your terminal"


def outside(scr, app, curses):
    """Leave the screen for the pager or the editor, then come back; or copy, without leaving it."""
    kind, text = app.external[:2]
    if kind == "copy":
        what, app.external = app.external[2], None
        return app.say(f"Copied {what}, through {copy(text)}", "ok")
    app.external = None
    curses.def_prog_mode()
    curses.endwin()
    os.write(sys.stdout.fileno(), b"\x1b[?2004l")
    try:
        if kind == "pager":
            subprocess.run(shlex.split(os.environ.get("PAGER") or "") or ["less"], input=text, text=True)
        else:
            fd, path = tempfile.mkstemp(prefix="farmout-", suffix=".md")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(text)
                editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "") or ["vi"]
                subprocess.run(editor + [path])
                with open(path) as f:
                    app.edited(f.read().rstrip("\n"))
            finally:
                os.unlink(path)
    except (OSError, ValueError) as e:
        app.say(f"Couldn't open the {kind}: {e}", "err")
    finally:
        os.write(sys.stdout.fileno(), b"\x1b[?2004h")
        curses.reset_prog_mode()
        scr.clear()
