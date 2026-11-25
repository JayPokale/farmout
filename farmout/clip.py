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
FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([\w+-]*)[^\n]*\n(.*?)^\1\2[ \t]*$", re.M | re.S)
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
    if first.startswith(("-", "$")) or "=" in span.split()[0] or span.rstrip().endswith((":", ".", ",")):
        return False
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
    os.write(sys.stdout.fileno(), b"\x1b]52;c;" + base64.b64encode(text.encode()) + b"\x07")
    return "your terminal"


def commands(text):
    """[{"text", "lang", "lines"}] in an answer's own Markdown, in order: every fenced block (a shell block's `$ `
    prompts taken off), then the one-line `code` spans that look like commands. Duplicates once."""
    found, seen = [], set()

    def add(body, lang):
        body = body.strip("\n")
        lines = body.splitlines()
        if next((line for line in lines if line.strip()), "").startswith("$ "):  # a shell session: the commands
            kept, going = [], False                                            # and their continued lines, not
            for line in lines:                                                  # what they printed
                if line.startswith("$ ") or going:
                    kept.append(line[2:] if line.startswith("$ ") and not going else line)
                    going = line.rstrip().endswith("\\")
            body = "\n".join(kept)
        body = body.strip()
        if body and body not in seen:
            seen.add(body)
            found.append({"text": body, "lang": lang or "", "lines": body.count("\n") + 1})
    blocks = list(FENCE.finditer(text))
    for m in blocks:
        add(m.group(4), m.group(3).lower())
    rest = FENCE.sub("", text)
    for m in INLINE.finditer(rest):
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
            if kind in kinds:
                return run(["wl-paste", "--type", kind]), ext
        tried.append("wl-paste")
    if shutil.which("xclip") and os.environ.get("DISPLAY"):
        kinds = run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]).decode(errors="replace").split()
        for kind, ext in (("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/webp", ".webp"), ("image/gif", ".gif")):
            if kind in kinds:
                return run(["xclip", "-selection", "clipboard", "-t", kind, "-o"]), ext
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
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            if f.exists() and f.stat().st_size:
                return f.read_bytes(), ".png"
        tried.append("osascript")
    if not tried:
        raise ValueError("no clipboard tool for images: install wl-clipboard (Wayland) or xclip (X11)")
    raise ValueError("the clipboard holds no image (copy a screenshot first; most screenshot tools have a copy "
                     "option, or press Ctrl+Shift+PrtSc)")


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return b""


def save(data, ext, dest=None):
    """Write an image where it's kept (~/.cache/farmout/pastes/, or dest); the path."""
    from .config import OUT_DIR
    if dest:
        path = Path(os.path.expanduser(dest))
        path = path / f"paste-{time.strftime('%Y%m%d-%H%M%S')}{ext}" if path.is_dir() else path
    else:
        path = OUT_DIR / "pastes" / f"{time.strftime('%Y%m%d-%H%M%S')}{ext}"
        n = 1
        while path.exists():
            n += 1
            path = path.with_name(f"{time.strftime('%Y%m%d-%H%M%S')}-{n}{ext}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def size(data):
    """(width, height) of a PNG, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None
