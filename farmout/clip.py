"""The clipboard: copy text to it (`farmout copy`, `y` and `Y` in `farmout ui`), take an image from it (`farmout paste`,
Ctrl+P in the ui's composer), and pick out the commands in an answer, whole, from its own text: a terminal wraps a
long command over several lines, and copying it there breaks it."""
import base64
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SHELLS = {"", "bash", "sh", "shell", "zsh", "fish", "console", "terminal", "powershell", "ps1", "cmd", "bat"}
INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
COMMANDY = re.compile(r"[A-Za-z0-9_./~$-]+(?:\s+\S+)+")  # a word and something after it: `farmout relay on`
KNOWN = {"farmout", "claude", "codex", "copilot", "agy", "git", "gh", "npm", "npx", "pnpm", "yarn", "bun", "pip",
         "pip3", "pipx", "uv", "uvx", "python", "python3", "node", "deno", "docker", "kubectl", "make", "cargo", "go",
         "cd", "ls", "cat", "curl", "wget", "brew", "apt", "sudo", "bash", "sh", "zsh", "export", "source", "ssh",
         "scp", "rm", "mv", "cp", "mkdir", "chmod", "tar", "pytest", "ruff", "eslint", "tsc", "grep", "rg", "sed",
         "echo", "touch", "open", "code", "vim", "nvim", "systemctl", "journalctl", "env"}


def runnable(span):
    """Does this one-line `code` span look like a command to run: a program on PATH, a well-known one, or a path?"""
    first = span.split()[0]
    if first.startswith(("-", "$")) or "=" in first or re.search(r"[A-Za-z][.:,]$", span.rstrip()):
        return False  # a flag, a setting, or prose that ends a sentence (`git add .` and `cd ..` still count)
    return first in KNOWN or first.startswith(("./", "~/", "/")) or bool(shutil.which(first))
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


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
    seq = b"\x1b]52;c;" + base64.b64encode(text.encode()) + b"\x07"
    try:  # the terminal itself, also when stdout is captured (`!farmout copy` in Claude Code)
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
        try:
            os.write(fd, seq)
        finally:
            os.close(fd)
    except OSError:
        os.write(sys.stdout.fileno(), seq)
    return "your terminal"


def tools():
    """The clipboard tools found here, by what they can do: {"copy": [...], "image": [...]}."""
    found = {"copy": [c for c in ("wl-copy", "xclip", "xsel", "pbcopy") if shutil.which(c)],
             "image": [c for c in ("wl-paste", "xclip", "pngpaste") if shutil.which(c)]}
    if sys.platform == "darwin":
        found["image"].append("osascript")
    return found


def fences(text):
    """[(lang, body)] of the fenced code blocks in Markdown: a fence of three or more ` or ~, indented as far as a
    list item may put it, closed by a fence of the same character at least as long; the body loses that indent."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        m = re.match(r"^( *)(`{3,}|~{3,})[ \t]*([\w+-]*)", lines[i])
        if not m or (m.group(2)[0] == "`" and "`" in lines[i][m.end(2):]):
            i += 1
            continue
        indent, char, size = len(m.group(1)), m.group(2)[0], len(m.group(2))
        body, j = [], i + 1
        while j < len(lines) and not re.match(rf"^ *{re.escape(char)}{{{size},}}[ \t]*$", lines[j]):
            body.append(lines[j][indent:] if lines[j][:indent].strip() == "" else lines[j].lstrip())
            j += 1
        out.append((m.group(3).lower(), "\n".join(body)))
        i = j + 1
    return out


def without_fences(text):
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        m = re.match(r"^ *(`{3,}|~{3,})", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        char, size, i = m.group(1)[0], len(m.group(1)), i + 1
        while i < len(lines) and not re.match(rf"^ *{re.escape(char)}{{{size},}}[ \t]*$", lines[i]):
            i += 1
        i += 1
    return "\n".join(out)


def commands(text):
    """[{"text", "lang", "lines"}] in an answer's own Markdown, in order: every fenced block (a shell session's `$ `
    prompts and `> ` continuations taken off, its output left out), then the one-line `code` spans that look like
    commands. Duplicates once."""
    text = text.replace("\r\n", "\n")
    found, seen = [], set()

    def add(body, lang):
        lines = body.strip("\n").splitlines()
        if next((line for line in lines if line.strip()), "").startswith("$ "):  # a shell session: the commands
            kept, going = [], False                                            # and their continued lines, not
            for line in lines:                                                  # what they printed
                if line.startswith("$ "):
                    kept.append(line[2:])
                elif going or (kept and line.startswith("> ")):
                    kept.append(line[2:] if line.startswith("> ") else line)
                else:
                    continue
                going = kept[-1].rstrip().endswith("\\")
            lines = kept
        body = "\n".join(lines).strip()
        if body and body not in seen:
            seen.add(body)
            found.append({"text": body, "lang": lang or "", "lines": body.count("\n") + 1})
    for lang, body in fences(text):
        add(body, lang)
    for m in INLINE.finditer(without_fences(text)):
        span = m.group(1).strip()
        if COMMANDY.fullmatch(span) and "://" not in span.split()[0] and runnable(span):
            add(span, "")
    return found


def image():
    """(bytes, extension) of the image on the clipboard. Raises ValueError: no image there, or no tool to read it."""
    tried = []
    if shutil.which("wl-paste") and os.environ.get("WAYLAND_DISPLAY"):
        kinds = run(["wl-paste", "--list-types"]).decode(errors="replace").split()
        for kind, ext in (("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/webp", ".webp"), ("image/gif", ".gif")):
            data = run(["wl-paste", "--type", kind]) if kind in kinds else b""
            if data:
                return data, ext
        tried.append("wl-paste")
    if shutil.which("xclip") and os.environ.get("DISPLAY"):
        kinds = run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]).decode(errors="replace").split()
        for kind, ext in (("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/webp", ".webp"), ("image/gif", ".gif")):
            data = run(["xclip", "-selection", "clipboard", "-t", kind, "-o"]) if kind in kinds else b""
            if data:
                return data, ext
        tried.append("xclip")
    if sys.platform == "darwin":
        if shutil.which("pngpaste"):
            data = run(["pngpaste", "-"])
            if data:
                return data, ".png"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "clip.png")
            script = (f'set f to open for access POSIX file "{f}" with write permission\n'
                      'try\nwrite (the clipboard as «class PNGf») to f\nend try\nclose access f')
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            if f.exists() and f.stat().st_size:
                return f.read_bytes(), ".png"
        tried.append("osascript")
    if not tried:
        raise ValueError("no clipboard tool for images: install wl-clipboard (Wayland) or xclip (X11)")
    raise ValueError("the clipboard holds no image (copy a screenshot first; most screenshot tools have a copy "
                     "option, or press Ctrl+Shift+PrtSc)")


def run(cmd):
    """stdout of cmd, or b"" when it fails or takes too long."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return b""
    return p.stdout if p.returncode == 0 else b""


def save(data, ext, dest=None):
    """Write an image where it's kept: a new file in ~/.cache/farmout/pastes/ or in the folder dest names, never over
    another (two pastes in one second each get theirs), or the file dest names. The path."""
    from .config import OUT_DIR
    where = Path(os.path.expanduser(dest)) if dest else OUT_DIR / "pastes"
    if dest and not where.is_dir():  # a file name
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(data)
        return where
    where.mkdir(parents=True, exist_ok=True)
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    path = where / f"{stamp}{ext}"
    while True:
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            n += 1
            path = where / f"{stamp}-{n}{ext}"


def size(data):
    """(width, height) of a PNG, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None
