"""Live lanes: while workers run, one row per worker on stderr, redrawn in place.

Only on a terminal, and never with --json, so pipes, scripts and the MCP server see nothing. NO_COLOR turns the
colours off; FARMOUT_LIVE=0 turns the lanes off.
"""
import os
import re
import shutil
import sys
import threading
import time

from . import review, sessions

# the logo's lane colours, one per worker
RGB = {"codex": (242, 166, 90), "antigravity": (94, 196, 182), "copilot": (167, 139, 250),
       "claude": (244, 124, 124), "deepseek": (77, 107, 254), "api": (236, 232, 223)}
GOOD, BAD = (123, 216, 143), (255, 107, 107)
SPIN = "◐◓◑◒"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def fit(line, width):
    """A line that can't wrap: a wrapped row would throw the in-place redraw off by a line. A row that doesn't fit
    loses its colours rather than half an escape sequence."""
    plain = ANSI.sub("", line)
    return line if len(plain) < width else plain[:max(width - 2, 1)] + "…"


def enabled(a=None):
    """Whether to draw lanes for this command: a terminal on stderr, no --json, not switched off."""
    if getattr(a, "json", False) or os.environ.get("FARMOUT_LIVE") == "0" or os.environ.get("TERM") == "dumb":
        return False
    try:
        return sys.stderr.isatty() and shutil.get_terminal_size((80, 24)).columns >= 50
    except (AttributeError, ValueError, OSError):
        return False


def paint(text, rgb=None, bold=False, dim=False):
    if os.environ.get("NO_COLOR"):
        return text
    codes = (["1"] if bold else []) + (["2"] if dim else []) + ([f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"] if rgb else [])
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text


class Lanes:
    """`with Lanes(group, workers, "reviewing", on=enabled(a)):` draws a row for each `<group>-<worker>` session
    until the block ends, then leaves the final state on screen. `note(name, text)` replaces a finished row's
    summary (a race's "testing…", then "tests pass")."""

    def __init__(self, group, workers, verb, title=None, findings=False, on=True, out=None):
        self.group, self.workers, self.verb, self.title, self.findings = group, list(workers), verb, title, findings
        self.on, self.out = on and bool(self.workers), out or sys.stderr
        self.notes, self.counts, self.drawn, self.seen = {}, {}, 0, False
        self.stop, self.lock, self.thread, self.t0 = threading.Event(), threading.Lock(), None, time.time()

    def write(self, text):
        """Write to the terminal; a terminal that has gone away mustn't take the command with it."""
        try:
            self.out.write(text)
            self.out.flush()
        except (OSError, ValueError):
            pass

    def __enter__(self):
        if self.on:
            self.write("\x1b[?25l")  # hide the cursor while redrawing
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.on:
            self.stop.set()
            self.thread.join(2)
            try:
                self.draw()
            except Exception:  # the command's own result matters more than the last frame
                pass
            finally:
                self.write("\x1b[?25h")
        return False

    def note(self, name, text):
        self.notes[name] = text
        if self.on:
            try:
                self.draw()
            except Exception:
                pass

    def loop(self):
        while not self.stop.wait(0.2):
            try:
                self.draw()
            except Exception:  # drawing must never break the command it decorates
                pass

    def turn(self, name):
        if not sessions.path(name).exists():
            return None
        recs = sessions.turns(name)
        return recs[-1] if recs else None

    def row(self, w, tick):
        name = f"{self.group}-{w}"
        rec = self.turn(name)
        label = paint(f"{w:<12}", RGB.get(w), bold=True)
        if rec is None:
            return f"  {paint('·', dim=True)} {label}{paint('starting…', dim=True)}"
        self.seen = True
        status, via = rec["status"], rec.get("worker") or w
        secs = rec.get("secs") if status != "running" else time.time() - rec["started"]
        took = paint(f"{secs or 0:.0f}s", dim=True)
        fallback = f" → {via}" if via != w else ""
        if status == "running":
            return f"  {paint(SPIN[tick % len(SPIN)], RGB.get(w))} {label}{self.verb}…{fallback}  {took}"
        if status == "ok":
            what = self.notes.get(name) or self.summary(rec, via)
            return f"  {paint('✓', GOOD)} {label}{what}{fallback}  {took}"
        return f"  {paint('■' if status == 'stopped' else '✗', BAD)} {label}{status}{fallback}  {took}"

    def summary(self, rec, worker):
        if not self.findings:
            return "done"
        key = rec.get("output")
        if key not in self.counts:
            try:
                with open(key, errors="replace") as fh:
                    self.counts[key] = len(review.parse(fh.read(), worker))
            except (OSError, TypeError):
                return "done"
        n = self.counts[key]
        return f"{n} finding{'s' * (n != 1)}"

    def draw(self):
        with self.lock:
            tick = int((time.time() - self.t0) * 5)
            rows = [self.row(w, tick) for w in self.workers]
            if not self.seen:  # nothing started (say, nothing to review): draw nothing at all
                return
            width = shutil.get_terminal_size((80, 24)).columns
            lines = [fit(line, width) for line in ([paint(self.title, dim=True)] if self.title else []) + rows]
            up = f"\x1b[{self.drawn}A\r" if self.drawn else ""
            self.write(up + "".join(f"\x1b[2K{line}\n" for line in lines))
            self.drawn = len(lines)
