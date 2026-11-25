"""farmout as an Agent Client Protocol agent: editors that speak ACP (Zed, JetBrains IDEs, and ACP plugins for
Neovim and Emacs) start `farmout acp` and talk to your subscriptions through it. Each editor thread is a farmout
session, so it gets the same fallback at a usage limit, and `farmout ui`, `log` and `send` see it too. The worker,
and whether it may edit files, are session settings the editor shows as pickers. JSON-RPC 2.0 over stdio, one
message per line (ACP protocol version 1)."""
import base64
import datetime
import json
import os
import sys
import threading
import time
from urllib.parse import unquote, urlparse

from . import VERSION, engine, flows, sessions
from .workers import ENABLED, lane

PROTOCOL = 1
ACCESS = {"read": "Read only", "write": "Edit files", "isolate": "Own git worktree"}
PAGE = 50  # sessions per session/list page


class Missing(KeyError):
    """A session id this agent doesn't know (ACP's "resource not found")."""


class Agent:
    def __init__(self, out=None):
        self.out, self.lock, self.threads, self.ready = out or sys.stdout, threading.Lock(), {}, False
        self.chats = {}  # ACP session id -> {"cwd", "worker", "access", "name", "gen", "lock"}

    # transport

    def send(self, msg):
        with self.lock:
            self.out.write(json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False) + "\n")
            self.out.flush()

    def reply(self, mid, result=None, error=None):
        self.send({"id": mid, "error": error} if error else {"id": mid, "result": result})

    def update(self, sid, update):
        self.send({"method": "session/update", "params": {"sessionId": sid, "update": update}})

    def serve(self, stdin=None):
        for line in stdin or sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.reply(None, error={"code": -32700, "message": "Parse error"})
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
                if isinstance(msg, dict) and msg.get("id") is not None and "result" not in msg and "error" not in msg:
                    self.reply(msg["id"], error={"code": -32600, "message": "Invalid request"})
                continue  # else a response to a request of ours (we send none), or junk
            self.handle(msg)
        for t in list(self.threads.values()):
            t.join(timeout=1)
        return 0

    def handle(self, msg):
        method, mid, params = msg["method"], msg.get("id"), msg.get("params")
        params = params if isinstance(params, dict) else {}
        if not self.ready and method != "initialize":
            if mid is not None:
                self.reply(mid, error={"code": -32600, "message": "initialize first"})
            return
        fn = {"initialize": self.initialize, "authenticate": self.authenticate, "session/new": self.new,
              "session/list": self.list, "session/load": self.load,
              "session/set_config_option": self.set_option, "session/set_mode": self.set_mode,
              "session/prompt": self.prompt, "session/cancel": self.cancel}.get(method)
        if fn is None:
            if mid is not None:
                self.reply(mid, error={"code": -32601, "message": f"Method not found: {method}"})
            return
        if method == "session/prompt":  # long: keep reading, so a session/cancel can reach it
            chat = self.chats.get(params.get("sessionId")) if isinstance(params.get("sessionId"), str) else None
            if chat:  # noted before the thread starts, so a cancel right behind the prompt applies to it
                params = {**params, "_gen": chat["gen"]}
            t = threading.Thread(target=self.guarded, args=(fn, mid, params), daemon=True)
            self.threads[mid] = t
            t.start()
        else:
            self.guarded(fn, mid, params)

    def guarded(self, fn, mid, params):
        try:
            result = fn(params)
        except Missing as e:
            if mid is not None:
                self.reply(mid, error={"code": -32002, "message": f"Resource not found: session {e.args[0]!r}",
                                       "data": {"sessionId": e.args[0]}})
            return
        except KeyError as e:
            if mid is not None:
                self.reply(mid, error={"code": -32602, "message": f"Invalid params: {e.args[0] if e.args else e}"})
            return
        except Exception as e:  # never take the editor's agent down
            if mid is not None:
                self.reply(mid, error={"code": -32603, "message": f"{type(e).__name__}: {e}"})
            return
        finally:
            self.threads.pop(mid, None)
        if mid is not None:
            self.reply(mid, result)

    # methods

    def initialize(self, params):
        self.ready = True
        return {"protocolVersion": PROTOCOL,
                "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"list": {}},
                                      "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True}},
                "authMethods": [], "agentInfo": {"name": "farmout", "title": "farmout", "version": VERSION}}

    def authenticate(self, params):
        return {}

    def options(self, chat):
        workers = ["auto"] + list(ENABLED)
        return [{"id": "worker", "name": "Worker", "category": "model", "type": "select",
                 "description": "Which subscription answers; auto: the first ready one. At a usage limit the "
                                "conversation moves to the next either way.",
                 "currentValue": chat["worker"],
                 "options": [{"value": w, "name": w} for w in workers]},
                {"id": "access", "name": "Files", "category": "mode", "type": "select",
                 "description": "Read only, edit files in the project, or edit in a private git worktree (a patch for "
                                "`farmout apply`).",
                 "currentValue": chat["access"],
                 "options": [{"value": k, "name": v} for k, v in ACCESS.items()]}]

    def new(self, params):
        cwd = params["cwd"]
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            raise KeyError(f"cwd {cwd!r} isn't a folder")
        sid = f"acp-{int(time.time() * 1000):x}-{len(self.chats)}"
        self.chats[sid] = {"cwd": os.path.realpath(cwd), "worker": "auto", "access": "read", "name": None,
                           "gen": 0, "lock": threading.Lock()}  # gen: cancels so far; a prompt from before one stops
        chat = self.chats[sid]
        return {"sessionId": sid, "configOptions": self.options(chat),
                "modes": {"currentModeId": chat["access"],
                          "availableModes": [{"id": k, "name": v} for k, v in ACCESS.items()]}}

    def list(self, params):
        """farmout's conversations (not review, ask or race runs), newest first; with cwd, that folder's only."""
        cwd = params.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise KeyError("cwd must be a string")
        try:
            start = int(base64.b64decode(params["cursor"]).decode()) if params.get("cursor") else 0
        except (ValueError, TypeError):
            raise KeyError("cursor") from None
        if start < 0:
            raise KeyError("cursor")
        rows = []
        for name in sessions.names():
            try:
                meta = sessions.load(name)
            except (KeyError, OSError, ValueError):
                continue
            if meta.get("group") or (cwd and os.path.realpath(meta["cwd"]) != os.path.realpath(cwd)):
                continue
            rows.append(sessions.summary(meta) | {"title": title(name, meta)})
        rows.sort(key=lambda r: -(r["updated"] or 0))
        page = rows[start:start + PAGE]
        out = {"sessions": [{"sessionId": r["name"], "cwd": r["cwd"], "title": r["title"],
                             "updatedAt": iso(r["updated"])} for r in page]}
        if start + PAGE < len(rows):
            out["nextCursor"] = base64.b64encode(str(start + PAGE).encode()).decode()
        return out

    def load(self, params):
        """Open a farmout conversation in this editor: its turns replayed, then prompts go on in it."""
        name = params["sessionId"]
        if not isinstance(name, str):
            raise KeyError("sessionId must be a string")
        try:
            meta = sessions.load(name)
        except (KeyError, OSError, ValueError):
            raise Missing(name) from None
        open_ = next((c for k, c in self.chats.items() if k == name or c.get("name") == name), None)
        self.chats[name] = chat = open_ or {  # a thread already on it here: the same one, its lock and its cancels
            "cwd": meta["cwd"], "worker": "auto",  # auto: the session keeps its own worker
            "access": "isolate" if meta.get("isolate") else "write" if meta.get("write") else "read",
            "name": name, "gen": 0, "lock": threading.Lock()}
        ctx = sessions.path(name) / "context.md"
        if ctx.exists():  # the conversation before this session's own turns (a branch's, or one carried over)
            for who, text in earlier(ctx.read_text()):
                self.update(name, {"sessionUpdate": f"{who}_message_chunk", "content": {"type": "text", "text": text}})
        for r in sessions.turns(name):
            self.update(name, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": r["prompt"]}})
            if r["status"] != "running":
                self.update(name, {"sessionUpdate": "agent_message_chunk",
                                   "content": {"type": "text", "text": flows.text(r).strip()}})
        return {"configOptions": self.options(chat)}

    def chat(self, params):
        sid = params["sessionId"]
        if not isinstance(sid, str):
            raise KeyError("sessionId must be a string")
        if sid not in self.chats:
            raise Missing(sid)
        return sid, self.chats[sid]

    def set_option(self, params):
        sid, chat = self.chat(params)
        key, value = params["configId"], params["value"]
        allowed = {"worker": ["auto"] + list(ENABLED), "access": list(ACCESS)}.get(key)
        if allowed is None or value not in allowed:
            raise KeyError(f"{key}={value!r} (allowed: {', '.join(allowed or [])})")
        chat[key] = value
        return {"configOptions": self.options(chat)}

    def set_mode(self, params):
        sid, chat = self.chat(params)
        if params["modeId"] not in ACCESS:
            raise KeyError(f"mode {params['modeId']!r}")
        chat["access"] = params["modeId"]
        return {}

    def cancel(self, params):
        sid, chat = self.chat(params)
        chat["gen"] += 1  # every prompt sent before this one is cancelled, running or waiting its turn
        if chat["name"]:
            sessions.stop(chat["name"])
        return None

    def prompt(self, params):
        sid, chat = self.chat(params)
        text, files = words(params["prompt"])
        if not text.strip():
            raise KeyError("an empty prompt")
        gen = params.get("_gen", chat["gen"])
        with chat["lock"]:  # one turn at a time per thread
            return self.turn(sid, chat, text, files, lambda: chat["gen"] != gen)

    def turn(self, sid, chat, text, files, cancelled):
        if cancelled():
            return {"stopReason": "cancelled"}
        worker, model = engine.auto_worker(chat["cwd"]) if chat["worker"] == "auto" else lane(chat["worker"])
        write, isolate = chat["access"] in ("write", "isolate"), chat["access"] == "isolate"
        name, switch, meta = chat["name"], None, None
        if name:
            try:
                meta = sessions.load(name)
            except KeyError:  # removed (`farmout rm`, or D in the ui): this thread starts afresh
                name = chat["name"] = None
        if meta and (bool(meta["write"]) != write or bool(meta.get("isolate")) != isolate):
            name = None  # a session's file access is fixed: another one carries on (with the conversation so far)
        if not name:
            old = chat["name"]  # set only when the file access changed: that session carries the conversation over
            base = ((sessions.load(old).get("base") or 0) + len(sessions.turns(old))) if old else 0
            meta = sessions.create(worker, chat["cwd"], write=write, isolate=isolate, model=model, kind="acp",
                                   base=base)
            if old:  # carry the conversation over: the new session's worker reads it from context.md
                (sessions.path(meta["name"]) / "context.md").write_text(sessions.transcript(old))
            name = chat["name"] = meta["name"]
        elif chat["worker"] != "auto" and (meta["worker"], meta.get("model")) != (worker, model):
            switch = {"worker": worker, "model": model}
        if cancelled():  # cancelled before anything was sent
            return {"stopReason": "cancelled"}
        call = f"farmout-{int(time.time() * 1000):x}"
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": call, "title": f"{worker} is working (farmout "
                          f"session {name})", "kind": "think", "status": "in_progress"})
        msg = sessions.deliver(name, text, files, cwd=chat["cwd"], wait=False, switch=switch)
        if cancelled():  # the cancel came while the message was being queued
            sessions.stop(name)
        rec = sessions.wait_msg(name, msg["id"])
        who = rec.get("worker") or worker
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": call,
                          "status": "completed" if rec["status"] == "ok" else "failed",
                          "title": f"{who} answered" if rec["status"] == "ok" else f"{who}: {rec['status']}"})
        if cancelled():
            return {"stopReason": "cancelled"}
        said = flows.text(rec).strip()
        notes = "; ".join(rec.get("notes") or [])
        if notes:
            said += f"\n\n_{notes}_"
        self.update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": said}})
        return {"stopReason": "end_turn"}


def earlier(text):
    """[("user"|"agent", text)] from a context.md: its turns' prompts and replies in order, and what comes before the
    first turn (a heading, a summary) as the agent's."""
    lines, out = text.split("\n"), []
    starts = [i for i, line in enumerate(lines) if sessions.TURN.fullmatch(line) and lines[i + 1:i + 3] == ["", "### Prompt"]
              and (i == 0 or lines[i - 1] == "")]
    head = "\n".join(lines[:starts[0] if starts else len(lines)]).strip()
    if head:
        out.append(("agent", head))
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        part = lines[a + 3:b]
        reply = next((i for i, line in enumerate(part) if line == "### Reply"), None)
        said = "\n".join(part[:reply] if reply is not None else part).strip()
        got = "\n".join(part[reply + 1:]).strip() if reply is not None else ""
        out += [("user", said)] + ([("agent", got)] if got else [])
    return out


def title(name, meta):
    first = next(iter(sessions.turns(name)), None)
    said = sessions.gist(first["prompt"], 80) if first else ""
    origin = (meta.get("imported") or {}).get("label")
    return f"{said} (from {origin})" if origin and said else said or name


def iso(t):
    return datetime.datetime.fromtimestamp(t or 0, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def words(blocks):
    """(prompt text, files) from ACP content blocks: text as is, a file:// link as a file the worker reads, an
    embedded resource's text inline."""
    parts, files = [], []
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
        elif kind == "resource_link" and isinstance(b.get("uri"), str):
            u = urlparse(b["uri"])
            if u.scheme == "file" and u.netloc in ("", "localhost") and os.path.exists(unquote(u.path)):
                files.append(unquote(u.path))
            else:
                parts.append(b["uri"])
        elif kind == "resource" and isinstance(b.get("resource"), dict):
            r = b["resource"]
            if isinstance(r.get("text"), str):
                parts.append(f"<file uri=\"{r.get('uri', '')}\">\n{r['text']}\n</file>")
    return "\n\n".join(parts), files


def main():
    return Agent().serve()
