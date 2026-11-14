"""farmout hook: tell you when a session's turn or a pipeline ends.

Desktop: notify-send (Linux) or osascript (macOS). Phone: set NTFY_TOPIC (and NTFY_SERVER to self-host instead of
ntfy.sh) and subscribe to that topic in the ntfy app. Turns shorter than NOTIFY_MIN_SECS (default 30) stay quiet.
Only names and statuses are sent, never prompts or outputs.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

event = json.load(sys.stdin)
if event["event"] == "TaskEnd":
    if not event.get("session") or (event.get("secs") or 0) < float(os.environ.get("NOTIFY_MIN_SECS", 30)):
        sys.exit(0)
    title = f"farmout: {event['session']} {'answered' if event.get('ok') else 'failed'}"
    body = f"{event.get('worker')} · {event.get('secs'):.0f}s"
elif event["event"] == "PipelineEnd":
    title = f"farmout: {event['run']} {event['status']}"
    body = ", ".join(f"{s['name']} {s['status']}" for s in event.get("stages", []))
else:
    sys.exit(0)
if shutil.which("notify-send"):
    subprocess.run(["notify-send", title, body], check=False)
elif shutil.which("osascript"):
    subprocess.run(["osascript", "-e", f"display notification {json.dumps(body)} with title {json.dumps(title)}"], check=False)
topic = os.environ.get("NTFY_TOPIC")
if topic:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    try:
        urllib.request.urlopen(urllib.request.Request(f"{server}/{topic}", body.encode(), {"Title": title}), timeout=10)
    except OSError as e:
        sys.exit(f"ntfy: {e}")
