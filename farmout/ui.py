"""`farmout ui --web`: every conversation as a tree in your browser (`farmout ui` alone draws it in the terminal).
Read any turn, continue a conversation from its newest turn, or branch off any turn, on any worker or model. The
terminal one (tui.py) uses snapshot(), detail() and send() from here too.

The page is served on 127.0.0.1 by Python's own web server, and it can start workers, so each run makes a fresh key.
The page's address carries it and the page's requests send it back in a header. Another site's page can't add that
header (a cross-origin request with one needs a CORS preflight, which this server never answers), and requests that
name another host or come from another origin are refused, so neither a site you visit nor DNS rebinding can use it.
"""
import json
import os
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import flows, sessions, usage
from .bench import COLOURS
from .workers import ENABLED, lane

BODY = 1 << 20  # the largest request taken
IMAGE_BODY = 16 << 20  # a pasted image (base64 in JSON)
PASTE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


def pastes():
    from .config import OUT_DIR
    return os.path.realpath(OUT_DIR / "pastes")


def pasted_files(files):
    """The pasted images a message may carry: files under ~/.cache/farmout/pastes only, never another path."""
    root = pastes() + os.sep
    real = [os.path.realpath(f) for f in files or [] if isinstance(f, str) and os.path.isabs(f)]
    return [f for f in real if f.startswith(root) and os.path.isfile(f)]  # the real file: no link to swap later
PREVIEW = 600  # characters of each question in the tree; the whole turn comes with /api/turn


def snapshot(here, preview=PREVIEW):
    """Every session and its turns (each question cut to preview characters), for the tree."""
    rows = []
    for name in sessions.names():
        try:
            meta = sessions.load(name)
            recs = sessions.turns(name)
        except (KeyError, OSError):  # removed meanwhile
            continue
        rows.append({**sessions.summary(meta), "gone": not os.path.isdir(meta["cwd"]),
                     "history": [{"n": r["n"], "id": r.get("id"), "prompt": r["prompt"][:preview], "worker": r.get("worker"),
                                  "model": r.get("model"), "status": r["status"], "secs": r.get("secs"),
                                  "started": r.get("started")} for r in recs]})
    why = usage.reasons()
    held = {k: {"until": until, "why": why.get(k)} for k, until in usage.limits().items()}  # at a limit, or logged out
    return {"sessions": rows, "workers": list(ENABLED), "here": here, "colours": COLOURS, "held": held}


def detail(name, n):
    rec = next((r for r in sessions.turns(name) if r["n"] == n), None)
    if rec is None:
        raise KeyError(f"{name} has no turn {n}")
    return {**{k: rec.get(k) for k in ("n", "prompt", "worker", "model", "status", "secs", "notes", "files", "started", "ended")},
            "text": None if rec["status"] == "running" else flows.text(rec)}


def send(body, here):
    """{mode: new | continue | branch | carry, session, n (the turn picked), edit (branch before it, to ask it
    differently), source (carry: the native session's id), prompt, worker, model} -> {session, message, moved}."""
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("write a message first")
    worker, model = lane(str(body["worker"])) if body.get("worker") else (None, None)
    model = body.get("model") or model
    if worker and worker not in ENABLED:
        raise ValueError(f"{worker} isn't available here (`farmout doctor` says why)")
    mode = body.get("mode")
    pasted = pasted_files(body.get("files"))  # images pasted into the composer
    if mode == "carry":  # your own Claude Code, Codex or Copilot CLI session, carried on here (read-only)
        from . import native, relay
        found = [x for x in native.find(here) if not body.get("source") or x["id"] == body["source"]]
        if not found:
            raise ValueError("no Claude Code, Codex or Copilot CLI session in this folder or its git repository")
        w, m = (worker, model) if worker else relay.pick(found[0], here)
        meta = relay.carry(found[0], w, m, here)[0]
        msg = sessions.deliver(meta["name"], prompt, pasted, cwd=meta["cwd"], wait=False)
        return {"session": meta["name"], "message": msg["id"]}
    if mode == "new":
        if not worker:
            raise ValueError("pick a worker")
        meta = sessions.create(worker, here, model=model)
        return {"session": meta["name"], "message": sessions.deliver(meta["name"], prompt, pasted, cwd=here, wait=False)["id"]}
    name = str(body.get("session") or "")
    moved = sessions.rehome(name, here)  # a renamed or moved folder is looked for where `farmout ui` runs
    meta, n = sessions.load(name), int(body.get("n") or 0)
    if mode == "continue":
        if n != meta["turns"]:
            raise ValueError(f"turn {n} isn't the newest in {name}: branch from it instead")
        switch = {"worker": worker, "model": model} if worker or model else None
        msg = sessions.deliver(name, prompt, pasted, cwd=meta["cwd"], wait=False, switch=switch)
    elif mode == "branch":
        at = (meta.get("base") or 0) + n - (1 if body.get("edit") else 0)
        meta, files, note = sessions.fork(name, worker=worker, at=at, model=model)
        msg = sessions.deliver(meta["name"], prompt, files + pasted, cwd=meta["cwd"], wait=False, note=note)
    else:
        raise ValueError("mode is new, continue or branch")
    return {"session": meta["name"], "message": msg["id"], "moved": moved}


class Handler(BaseHTTPRequestHandler):
    server_version = "farmout"

    def log_message(self, *args):  # quiet: the terminal shows only the address
        pass

    def allowed(self, key):
        port = self.server.server_address[1]
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") in hosts and (origin is None or origin in tuple("http://" + h for h in hosts))
                and bool(key) and secrets.compare_digest(str(key).encode(), self.server.key.encode()))

    def reply(self, code, body, kind="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        for k, v in (("Content-Type", f"{kind}; charset=utf-8"), ("Content-Length", str(len(data))),
                     ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                     ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"), *headers):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def failed(self, e):
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        self.reply(404 if isinstance(e, KeyError) else 400, {"error": msg})

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            if not self.allowed((q.get("key") or [""])[0]):
                return self.reply(403, b"Open the address `farmout ui --web` printed: it holds this run's key.\n", "text/plain")
            nonce = secrets.token_urlsafe(16)
            csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
                   "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            return self.reply(200, PAGE.replace("__NONCE__", nonce).encode(), "text/html",
                              (("Content-Security-Policy", csp),))
        if not self.allowed(self.headers.get("X-Farmout-Key")):
            return self.reply(403, {"error": "forbidden"})
        try:
            if url.path == "/api/state":
                return self.reply(200, snapshot(self.server.here))
            if url.path == "/api/turn":
                return self.reply(200, detail(q["session"][0], int(q["n"][0])))
        except (KeyError, ValueError, IndexError, OSError) as e:
            return self.failed(e)
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed(self.headers.get("X-Farmout-Key")):
            return self.reply(403, {"error": "forbidden"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            path = urllib.parse.urlsplit(self.path).path
            if size < 0:
                return self.reply(400, {"error": "bad Content-Length"})
            if size > (IMAGE_BODY if path == "/api/paste" else BODY):
                return self.reply(413, {"error": "too large"})
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            if path == "/api/paste":  # an image pasted into a composer: kept like `farmout paste` keeps one
                import base64
                from . import clip
                ext = PASTE_TYPES.get(str(body.get("type")))
                if not ext:
                    raise ValueError("paste a PNG, JPEG, GIF or WebP image")
                data = base64.b64decode(str(body.get("data") or ""), validate=True)
                return self.reply(200, {"path": str(clip.save(data, ext))})
            if path == "/api/send":
                return self.reply(200, send(body, self.server.here))
            if path == "/api/stop":
                name = str(body.get("session") or "")
                sessions.load(name)
                return self.reply(200, {"stopped": sessions.stop(name)})
        except (KeyError, ValueError, TypeError, FileNotFoundError, PermissionError, RuntimeError, OSError) as e:
            return self.failed(e)
        self.reply(404, {"error": "not found"})


def serve(here, port=0, browser=True):
    """Serve the page until Ctrl-C."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.key, server.here = secrets.token_urlsafe(24), here
    url = f"http://127.0.0.1:{server.server_address[1]}/?key={server.key}"
    print(f"farmout ui: {url}\nOnly this computer can open it. Ctrl-C stops it; conversations keep running.", flush=True)
    if browser:
        try:
            webbrowser.open(url)
        except Exception:  # no browser here: the address is printed
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>farmout conversations</title>
<style nonce="__NONCE__">
:root{--bg:#f6f5f1;--panel:#fff;--fg:#1c1e24;--dim:#676b77;--line:#e2e0d8;--hover:#efede6;--sel:#e6e1d3;
--accent:#b4541a;--ok:#1e7a3e;--bad:#b42318;--run:#a15c07;--code:#f1efe8;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#0b0c11;--panel:#11131a;--fg:#ece8df;--dim:#9a9eaf;--line:#242731;
--hover:#181b24;--sel:#20242f;--accent:#f2a65a;--ok:#5bd38a;--bad:#f37b72;--run:#f2c04f;--code:#171a22}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 var(--sans);display:flex;flex-direction:column}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;margin-right:6px}
.brand small{font-weight:400;color:var(--dim)}
#q{flex:1 1 220px;min-width:0;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
.tools{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.btn{padding:6px 11px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.btn:hover{background:var(--hover)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.btn.primary:hover{filter:brightness(1.08)}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.check{display:flex;gap:6px;align-items:center;color:var(--dim);font-size:13px}
main{flex:1;display:grid;grid-template-columns:minmax(300px,42%) 1fr;min-height:0}
#tree{overflow:auto;padding:10px 8px 40px;border-right:1px solid var(--line)}
#detail{overflow:auto;padding:18px 22px 60px;background:var(--panel)}
.conv{margin:2px 0}
.head{display:flex;align-items:center;gap:7px;width:100%;text-align:left;border:0;background:none;padding:5px 6px;border-radius:7px}
.head:hover{background:var(--hover)}
.caret{width:12px;color:var(--dim);flex:none}
.name{font-weight:600;white-space:nowrap}
.meta{color:var(--dim);font-size:12px;white-space:nowrap;margin-left:auto}
.fork{color:var(--accent);font-weight:700}
.turns{list-style:none;margin:0 0 4px 12px;padding:0 0 0 9px;border-left:1px solid var(--line)}
.turn{display:flex;align-items:baseline;gap:8px;width:100%;text-align:left;border:0;background:none;padding:4px 6px;border-radius:6px;min-width:0}
.turn:hover{background:var(--hover)}
.turn[aria-current=true]{background:var(--sel)}
.turn .num{color:var(--dim);font:12px var(--mono);min-width:1.6em;text-align:right;flex:none}
.turn .text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.turn.hit .text{color:var(--accent);font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;flex:none;display:inline-block;border:1px solid var(--line)}
.chip{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;border:1px solid var(--line);border-radius:999px;font-size:12px;white-space:nowrap}
.state{font-size:11px;padding:0 6px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.state.running,.state.queued{color:var(--run)}
.state.failed,.state.stopped,.state.interrupted,.state.gone{color:var(--bad)}
.group>.head .name{font-weight:500;color:var(--dim)}
.empty,.dim{color:var(--dim)}
.empty{padding:14px}
.crumbs{color:var(--dim);font-size:12px;margin-bottom:4px;overflow-wrap:anywhere}
h2{margin:0 0 6px;font-size:18px}
h3{margin:18px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.line{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--dim);font-size:13px}
.where{font:12px var(--mono);color:var(--dim);margin-top:6px;overflow-wrap:anywhere}
.warn{border:1px solid var(--bad);color:var(--bad);border-radius:8px;padding:8px 10px;margin-top:8px;font-size:13px}
.question,.prose{white-space:pre-wrap;overflow-wrap:anywhere}
.question{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;max-height:40vh;overflow:auto}
pre.code{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow:auto;font:12.5px/1.5 var(--mono)}
.notes{color:var(--dim);font-size:12px;padding-left:18px}
.prose code{font:12.5px var(--mono);background:var(--code);border:1px solid var(--line);border-radius:5px;padding:0 4px}
#held{background:var(--hover);color:var(--bad);padding:5px 16px;font-size:13px;border-bottom:1px solid var(--line)}
.composer{margin-top:22px;padding-top:16px;border-top:1px solid var(--line)}
.modes{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.modes .btn[aria-pressed=true]{border-color:var(--accent);color:var(--accent);font-weight:600}
.explain{color:var(--dim);font-size:13px;margin:0 0 8px}
textarea{width:100%;min-height:96px;resize:vertical;padding:9px 11px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.row label{display:flex;gap:6px;align-items:center;color:var(--dim);font-size:13px}
.row select,.row input{padding:5px 8px;border:1px solid var(--line);border-radius:7px;background:var(--bg)}
.row input{width:150px}
.row .btn.primary{margin-left:auto}
#toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:8px 14px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none;max-width:90vw}
#toast.show{opacity:1}
#toast.bad{background:var(--bad);color:#fff}
#offline{background:var(--bad);color:#fff;padding:6px 16px;font-size:13px}
@media (max-width:820px){main{display:block;overflow:auto}#tree{border-right:0;border-bottom:1px solid var(--line);max-height:48vh}#detail{padding:16px}.meta{display:none}}
</style>
</head>
<body>
<header>
  <div class="brand"><svg width="22" height="22" viewBox="0 0 512 512" aria-hidden="true"><rect width="512" height="512" rx="112" fill="#14161f"/><g fill="none" stroke-width="30" stroke-linecap="round"><path d="M104 256H196C250 256 262 140 326 140H384" stroke="#f2a65a"/><path d="M104 256H196C250 256 262 218 326 218H384" stroke="#5ec4b6"/><path d="M104 256H196C250 256 262 294 326 294H384" stroke="#a78bfa"/><path d="M104 256H196C250 256 262 372 326 372H384" stroke="#f47c7c"/></g><circle cx="104" cy="256" r="40" fill="#ece8df"/></svg>farmout <small>conversations</small></div>
  <input id="q" type="search" placeholder="Search your questions" aria-label="Search your questions">
  <div class="tools">
    <button class="btn" id="expand" type="button">Expand all</button>
    <button class="btn" id="collapse" type="button">Collapse all</button>
    <label class="check"><input id="groups" type="checkbox"> review, ask and race runs</label>
    <button class="btn primary" id="new" type="button">New conversation</button>
  </div>
</header>
<div id="offline" hidden>farmout ui has stopped. Run <code>farmout ui --web</code> again.</div>
<div id="held" hidden></div>
<main>
  <nav id="tree" aria-label="Conversations"></nav>
  <section id="detail"><div id="view"></div><div id="compose"></div></section>
</main>
<div id="toast" role="status" aria-live="polite"></div>
<script nonce="__NONCE__">
"use strict";
const KEY = new URLSearchParams(location.search).get("key") || "";
const $ = s => document.querySelector(s);
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}
const saved = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
let state = {sessions: [], workers: [], here: "", colours: {}};
let by = new Map(), kids = new Map(), sig = "", first = true;
let sel = null, mode = null, draft = "", wanted = null, viewKey = "", composeKey = "", ticket = 0, timer = 0;
const details = new Map();
const open = new Set(saved.get("farmout-open", []));
let groupsOn = saved.get("farmout-groups", false);

async function api(path, body) {
  const r = await fetch(path, {method: body ? "POST" : "GET", body: body ? JSON.stringify(body) : undefined,
    headers: Object.assign({"X-Farmout-Key": KEY}, body ? {"Content-Type": "application/json"} : {})});
  const data = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(text, bad) {
  const t = $("#toast");
  t.textContent = text;
  t.className = "show" + (bad ? " bad" : "");
  clearTimeout(t.hide);
  t.hide = setTimeout(() => { t.className = ""; }, bad ? 6000 : 2500);
}
const laneOf = (w, m) => m ? w + ":" + m : (w || "?");
const num = (s, n) => (s.base || 0) + n;
const busy = s => s.state === "running" || s.state === "queued";
const turnOf = (s, n) => s && s.history.find(t => t.n === n);
function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + "m ago" : s < 86400 ? Math.round(s / 3600) + "h ago" : Math.round(s / 86400) + "d ago";
}
function dot(w, m) {
  const d = el("span", {class: "dot", title: laneOf(w, m)});
  d.style.background = state.colours[w] || "transparent";
  return d;
}
const chip = (w, m) => el("span", {class: "chip"}, dot(w, m), laneOf(w, m));
const badge = (text, kind) => el("span", {class: "state " + kind}, text);
function line(s) { const out = []; for (let x = s; x; x = x.parent ? by.get(x.parent) : null) out.unshift(x); return out; }
function lastAct(s) { return Math.max(s.updated || 0, ...(kids.get(s.name) || []).map(lastAct)); }
function remember() { saved.set("farmout-open", [...open]); }

function index() {
  by = new Map(state.sessions.map(s => [s.name, s]));
  kids = new Map();
  for (const s of state.sessions) if (s.parent && by.has(s.parent)) {
    if (!kids.has(s.parent)) kids.set(s.parent, []);
    kids.get(s.parent).push(s);
  }
}

function renderTree() {
  const nav = $("#tree"), q = $("#q").value.trim().toLowerCase();
  const hit = s => !!q && (s.name.toLowerCase().includes(q) || s.history.some(t => t.prompt.toLowerCase().includes(q)));
  const shows = s => !q || hit(s) || (kids.get(s.name) || []).some(shows);
  const roots = state.sessions.filter(s => !(s.parent && by.has(s.parent)) && shows(s) && (!s.group || groupsOn || q))
    .sort((a, b) => lastAct(b) - lastAct(a));
  const out = [], groups = new Map();
  for (const s of roots) {
    if (!s.group) { out.push(conv(s, q, shows)); continue; }
    if (!groups.has(s.group)) { groups.set(s.group, []); out.push(s.group); }
    groups.get(s.group).push(s);
  }
  nav.replaceChildren(...out.map(x => typeof x === "string" ? group(x, groups.get(x), q, shows) : x));
  if (!out.length) nav.append(el("p", {class: "empty"}, q ? "No question matches." :
    "No conversations yet. Start one with New conversation, or `farmout run <worker> \"...\"` in a terminal."));
}
function group(g, list, q, shows) {
  const key = "group:" + g, isOpen = !!q || open.has(key);
  return el("div", {class: "conv group"},
    el("button", {class: "head", type: "button", "aria-expanded": String(isOpen), onclick: () => toggle(key)},
      el("span", {class: "caret"}, isOpen ? "▾" : "▸"), el("span", {class: "name"}, g),
      el("span", {class: "meta"}, list.length + " lane" + (list.length === 1 ? "" : "s") + " · " + ago(Math.max(...list.map(lastAct))))),
    isOpen ? el("div", {class: "turns"}, list.map(s => conv(s, q, shows))) : null);
}
function conv(s, q, shows) {
  const isOpen = !!q || open.has(s.name);
  const box = el("div", {class: "conv"},
    el("button", {class: "head", type: "button", "aria-expanded": String(isOpen), onclick: () => toggle(s.name, s)},
      el("span", {class: "caret"}, isOpen ? "▾" : "▸"),
      s.parent ? el("span", {class: "fork", title: "a branch"}, "⑂") : null,
      el("span", {class: "name"}, s.name), dot(s.worker, s.model),
      busy(s) ? badge(s.state, s.state) : null, s.gone ? badge("folder gone", "gone") : null,
      el("span", {class: "meta"}, s.history.length + " turn" + (s.history.length === 1 ? "" : "s") + " · " + ago(lastAct(s)))));
  if (!isOpen) return box;
  const ns = new Set(s.history.map(t => t.n)), at = new Map();
  for (const k of kids.get(s.name) || []) {
    if (!shows(k)) continue;
    let n = k.fork_at;
    if (n === null || n === undefined || (n !== 0 && !ns.has(n))) n = s.history.length ? s.history[s.history.length - 1].n : 0;
    if (!at.has(n)) at.set(n, []);
    at.get(n).push(k);
  }
  const ul = el("ul", {class: "turns"});
  const hang = n => { for (const k of at.get(n) || []) ul.append(el("li", null, conv(k, q, shows))); };
  hang(0);
  for (const t of s.history) { ul.append(el("li", null, turnRow(s, t, q))); hang(t.n); }
  if (!s.history.length) ul.append(el("li", {class: "dim"}, "no turns yet"));
  box.append(ul);
  return box;
}
function turnRow(s, t, q) {
  const text = t.prompt.trim().split("\n")[0] || "(empty message)";
  const current = sel && sel.s === s.name && sel.n === t.n;
  return el("button", {class: "turn" + (q && t.prompt.toLowerCase().includes(q) ? " hit" : ""), type: "button",
      "aria-current": current ? "true" : null, title: t.prompt.slice(0, 300), onclick: () => select(s.name, t.n)},
    el("span", {class: "num"}, num(s, t.n)), el("span", {class: "text"}, text), dot(t.worker, t.model),
    t.status === "ok" ? null : badge(t.status, t.status));
}
function toggle(key, s) {
  if (open.has(key)) open.delete(key);
  else {
    open.add(key);
    if (s && s.history.length && !(sel && sel.s === s.name)) return select(s.name, s.history[s.history.length - 1].n);
  }
  remember();
  renderTree();
}

function select(name, n) {
  const s = by.get(name);
  if (!s) return;
  if (mode === "edit") draft = "";
  sel = {s: name, n};
  mode = null;
  for (const x of line(s)) open.add(x.name);
  if (s.group) open.add("group:" + s.group);
  remember();
  renderTree();
  renderView(true);
  renderComposer();
  if (window.matchMedia("(max-width: 820px)").matches) $("#detail").scrollIntoView({behavior: "smooth"});
}

function fill(box, text) {
  box.replaceChildren();
  (text || "").split(/^```[^\n]*\n?/m).forEach((part, i) => {
    if (!part.trim() && i % 2 === 0) return;
    box.append(i % 2 ? el("pre", {class: "code"}, part.replace(/\n$/, "")) : prose(part));
  });
}
function prose(text) {  // **bold** and `code` as elements, never as HTML
  const box = el("div", {class: "prose"});
  let at = 0;
  for (const m of text.matchAll(/\*\*(.+?)\*\*|`([^`\n]+)`/g)) {
    box.append(text.slice(at, m.index), m[1] !== undefined ? el("strong", null, m[1]) : el("code", null, m[2]));
    at = m.index + m[0].length;
  }
  box.append(text.slice(at));
  return box;
}
async function load(s, t) {
  const key = s.name + "/" + t.n, mine = ++ticket;
  try {
    const d = await api("/api/turn?session=" + encodeURIComponent(s.name) + "&n=" + t.n);
    if (d.status !== "running") details.set(key, d);
    if (mine === ticket && sel && sel.s === s.name && sel.n === t.n) renderView(true);
  } catch (e) { toast(e.message, true); }
}
function renderView(force) {
  const view = $("#view");
  const s = sel && by.get(sel.s), t = turnOf(s, sel && sel.n);
  if (!s || !t) {  // the new-conversation form, drawn once so a refresh doesn't wipe what you're typing
    sel = null;
    if (force || viewKey !== "new") { viewKey = "new"; view.replaceChildren(newForm()); }
    return;
  }
  const key = s.name + "/" + t.n + "/" + t.status + "/" + s.gone + "/" + s.state;
  if (!force && key === viewKey) return;
  viewKey = key;
  const d = details.get(s.name + "/" + t.n);
  if (!d && t.status !== "running") load(s, t);
  const answer = el("div", {class: "answer"});
  if (d) fill(answer, d.text);
  else answer.append(el("p", {class: "dim"}, t.status === "running" ? "Working on it…" : "Loading…"));
  const copy = d ? el("button", {class: "btn", type: "button", onclick: () =>
    navigator.clipboard.writeText(d.text || "").then(() => toast("Copied"), () => toast("Couldn't copy", true))}, "Copy answer") : null;
  view.replaceChildren(...[  // replaceChildren would print a null as "null"
    el("div", {class: "crumbs"}, line(s).map(x => x.name).join(" › ")),
    el("h2", null, "Turn " + num(s, t.n)),
    el("div", {class: "line"}, chip(t.worker, t.model), t.status === "ok" ? null : badge(t.status, t.status),
      t.secs != null ? el("span", null, t.secs + " s") : null, el("span", null, ago(t.started)), busy(s) ? el("button", {class: "btn", type: "button", onclick: () =>
        api("/api/stop", {session: s.name}).then(() => { toast("Stopped " + s.name); refresh(); }, e => toast(e.message, true))}, "Stop") : null),
    el("div", {class: "where"}, s.cwd),
    s.gone ? el("div", {class: "warn"}, "This conversation's folder is gone (renamed or moved?). Sending finds it in the same git repository as " +
      state.here + ", where farmout ui runs. From anywhere else: farmout send " + s.name + " --here \"...\" in its new place.") : null,
    el("h3", null, "Question"), el("div", {class: "question"}, d ? d.prompt : t.prompt),
    el("h3", null, "Answer"), answer, copy,
    d && d.notes && d.notes.length ? el("ul", {class: "notes"}, d.notes.map(x => el("li", null, x))) : null].filter(Boolean));
}

function workerPicker(keep) {
  const pick = el("select", {"aria-label": "Worker"},
    keep ? el("option", {value: ""}, "same: " + keep) : null,
    state.workers.map(w => el("option", {value: w}, w)));
  return pick;
}
function renderComposer() {
  const box = $("#compose");
  const s = sel && by.get(sel.s), t = turnOf(s, sel && sel.n);
  if (!s || !t) { composeKey = ""; return box.replaceChildren(); }
  const newest = t.n === s.turns, at = num(s, t.n);
  composeKey = s.name + "/" + t.n + "/" + newest;
  if (!mode || (mode === "continue" && !newest)) mode = newest ? "continue" : "branch";
  const keeps = k => k < 1 ? "starts afresh in the same folder" : k === 1 ? "keeps turn 1" : "keeps turns 1–" + k;
  const explain = {
    continue: "Adds your message to " + s.name + " after turn " + at + ". Pick another worker or model to carry on there: it gets the conversation so far.",
    branch: "Starts a new conversation that " + keeps(at) + " and goes on from here. " + s.name + " stays as it is. A branch reads the folder as it is now: files aren't branched.",
    edit: "Starts a new conversation that " + keeps(at - 1) + " and asks this question your way instead.",
  }[mode];
  const text = el("textarea", {"aria-label": "Your message", placeholder: mode === "continue" ? "Your next message" : "Your message for the new branch"});
  const att = {};
  const chips = pasting(text, att);
  text.value = draft;
  text.addEventListener("input", () => { draft = text.value; });
  const worker = workerPicker(laneOf(t.worker, t.model)), model = el("input", {placeholder: "model (optional)", "aria-label": "Model"});
  const go = el("button", {class: "btn primary", type: "button"}, mode === "continue" ? "Send" : "Branch and send");
  const sendNow = async () => {
    const prompt = text.value.trim();
    if (!prompt) return toast("Write a message first", true);
    go.disabled = true;
    await att.ready();
    try {
      const r = await api("/api/send", {mode: mode === "edit" ? "branch" : mode, edit: mode === "edit", session: s.name, n: t.n,
        prompt, worker: worker.value || null, model: model.value.trim() || null, files: att.files});
      draft = text.value = "";
      att.files.length = 0; chips.textContent = "";
      go.disabled = false;
      wanted = {s: r.session, id: r.message};
      open.add(r.session); remember();
      toast((r.moved ? "Its folder is now " + r.moved + ". " : "") + (r.session === s.name ? "Sent" : "New branch " + r.session));
      refresh();
    } catch (e) { toast(e.message, true); go.disabled = false; }
  };
  go.addEventListener("click", sendNow);
  text.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendNow(); } });
  const modeBtn = (m, label, enabled) => el("button", {class: "btn", type: "button", "aria-pressed": String(mode === m),
    disabled: !enabled, onclick: () => {
      if (m === "edit" && mode !== "edit") draft = (details.get(s.name + "/" + t.n) || t).prompt;
      else if (mode === "edit" && m !== "edit") draft = "";
      mode = m; renderComposer(); $("#compose textarea").focus();
    }}, label);
  box.replaceChildren(el("div", {class: "composer"},
    el("div", {class: "modes"}, modeBtn("continue", "Continue", newest), modeBtn("branch", "Branch from here", true),
      modeBtn("edit", "Ask this differently", true)),
    el("p", {class: "explain"}, explain), text, chips,
    el("div", {class: "row"}, el("label", null, "Worker", worker), el("label", null, "Model", model), go),
    el("p", {class: "explain"}, "Ctrl+Enter sends. Paste an image into the message to send it along.")));
}
function pasting(text, holder) {
  // an image pasted into the message: kept by farmout, sent with the message
  const files = [], pending = [];
  const chips = el("p", {class: "explain"});
  holder.files = files;
  holder.chips = chips;
  holder.ready = () => Promise.all(pending);  // a paste still uploading goes with the message
  text.addEventListener("paste", async e => {
    const items = [...(e.clipboardData && e.clipboardData.items || [])].filter(i => i.kind === "file" && i.type.startsWith("image/"));
    if (!items.length) return;
    e.preventDefault();
    for (const item of items) {
      const blob = item.getAsFile();
      pending.push((async () => {
        const data = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1]); r.readAsDataURL(blob); });
        try {
          const r = await api("/api/paste", {type: blob.type, data});
          files.push(r.path);
          chips.textContent = "📎 " + files.length + " image" + (files.length === 1 ? "" : "s") + " attached";
        } catch (err) { toast(err.message, true); }
      })());
    }
  });
  return chips;
}
function newForm() {
  const worker = workerPicker(null), model = el("input", {placeholder: "model (optional)", "aria-label": "Model"});
  const text = el("textarea", {"aria-label": "Your first message", placeholder: "What should it do?"});
  const att = {};
  const chips = pasting(text, att);
  const go = el("button", {class: "btn primary", type: "button"}, "Start");
  const start = async () => {
    const prompt = text.value.trim();
    if (!prompt) return toast("Write a message first", true);
    go.disabled = true;
    await att.ready();
    try {
      const r = await api("/api/send", {mode: "new", prompt, worker: worker.value, model: model.value.trim() || null,
        files: att.files});
      wanted = {s: r.session, id: r.message};
      open.add(r.session); remember();
      toast("Started " + r.session);
      refresh();
    } catch (e) { toast(e.message, true); go.disabled = false; }
  };
  go.addEventListener("click", start);
  text.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); start(); } });
  return el("div", null,
    state.sessions.length ? el("p", {class: "dim"}, "Pick a question on the left to read it, then continue from there or branch off it.") : null,
    el("h2", null, "New conversation"),
    el("div", {class: "where"}, "in " + state.here),
    state.workers.length ? el("div", null, el("div", {class: "row"}, el("label", null, "Worker", worker), el("label", null, "Model", model)),
      text, chips, el("div", {class: "row"}, go)) : el("p", {class: "warn"}, "No worker is ready here: run farmout doctor in a terminal."));
}

async function refresh() {
  clearTimeout(timer);
  try {
    const data = await api("/api/state");
    $("#offline").hidden = true;
    const now = JSON.stringify(data.sessions);
    const changed = now !== sig;
    sig = now; state = data; index();
    const held = Object.entries(data.held || {});
    $("#held").hidden = !held.length;
    $("#held").textContent = held.map(([k, v]) => k + (v.why === "login" ? " is logged out" : " is at its usage limit") +
      " until " + new Date(v.until * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) +
      ": farmout gives its tasks to the next worker").join(" · ");
    if (wanted) {
      const s = by.get(wanted.s), t = s && s.history.find(x => x.id === wanted.id);
      if (t) { wanted = null; select(s.name, t.n); }
    }
    if (changed || first) {
      renderTree();
      renderView(first);
      const s = sel && by.get(sel.s), t = turnOf(s, sel && sel.n);
      if (first || !t || composeKey !== s.name + "/" + t.n + "/" + (t.n === s.turns)) renderComposer();  // else keep the draft as typed
      first = false;
    }
  } catch (e) {
    $("#offline").hidden = false;
  }
  timer = setTimeout(refresh, wanted || state.sessions.some(busy) ? 1500 : 6000);
}

$("#q").addEventListener("input", renderTree);
$("#expand").addEventListener("click", () => { for (const s of state.sessions) open.add(s.name); for (const s of state.sessions) if (s.group) open.add("group:" + s.group); remember(); renderTree(); });
$("#collapse").addEventListener("click", () => { open.clear(); remember(); renderTree(); });
$("#groups").checked = groupsOn;
$("#groups").addEventListener("change", e => { groupsOn = e.target.checked; saved.set("farmout-groups", groupsOn); renderTree(); });
$("#new").addEventListener("click", () => { sel = null; renderTree(); renderView(true); renderComposer(); $("#view textarea") && $("#view textarea").focus(); });
refresh();
</script>
</body>
</html>
"""
