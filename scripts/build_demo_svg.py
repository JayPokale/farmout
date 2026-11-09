"""Build assets/review-demo.svg: an animated terminal replay of one real /modelmux:review run.

The text comes from a real run (bench/results/<date>/demo-review.*); only the clock is compressed.
Stdlib only. Run:  python3 scripts/build_demo_svg.py
With --video it also renders launch/review-demo.{mp4,gif} for sites that don't play SVG animation
(needs rsvg-convert and ffmpeg).
"""
import json
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "scripts" / "demo.json").read_text())
W, LH, X0, TOP = 920, 23, 26, 70
CYCLE = DATA["cycle"]
COLORS = {"fg": "#ece8df", "dim": "#8b8fa3", "ok": "#7bd88f", "high": "#f47c7c", "medium": "#f2a65a",
          "low": "#a3a7b7", "antigravity": "#5ec4b6", "codex": "#f2a65a", "copilot": "#a78bfa", "claude": "#f47c7c",
          "prompt": "#a78bfa"}


def keyframes(name, on, off):
    """Hidden until `on` seconds, shown until `off` (or the end of the loop)."""
    a, b = 100 * on / CYCLE, 100 * (off if off is not None else CYCLE - 0.8) / CYCLE
    return (f"@keyframes {name}{{0%,{a:.2f}%{{opacity:0}}{a + 0.6:.2f}%,{b:.2f}%{{opacity:1}}"
            f"{min(b + 0.6, 100):.2f}%,100%{{opacity:0}}}}"
            f"#{name}{{animation:{name} {CYCLE}s linear infinite}}")


def tspan(text, color, bold=False):
    weight = ' font-weight="700"' if bold else ""
    return f'<tspan fill="{COLORS.get(color, color)}"{weight}>{escape(text)}</tspan>'


def main():
    rows = DATA["lines"]
    height = TOP + LH * (1 + max(r["row"] for r in rows)) + 24
    css, body = [], []
    for n, r in enumerate(rows):
        name = f"l{n}"
        css.append(keyframes(name, r["on"], r.get("off")))
        spans = "".join(tspan(*s) for s in r["spans"])
        cls = "l run" if r.get("off") is not None else "l"
        body.append(f'<text id="{name}" class="{cls}" x="{X0 + 9 * r.get("indent", 0)}" y="{TOP + LH * r["row"]}" '
                    f'xml:space="preserve">{spans}</text>')
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img">
<title>{escape(DATA["title"])}</title>
<style>
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace;font-size:14.5px}}
.l{{opacity:0}}
{"".join(css)}
@media (prefers-reduced-motion:reduce){{.l{{animation:none!important;opacity:1}}.run{{display:none}}}}
</style>
<rect width="{W}" height="{height}" rx="14" fill="#14161f"/>
<circle cx="26" cy="24" r="6" fill="#f47c7c"/><circle cx="46" cy="24" r="6" fill="#f2a65a"/><circle cx="66" cy="24" r="6" fill="#7bd88f"/>
<text x="{W // 2}" y="29" text-anchor="middle" fill="#8b8fa3" style="font-size:13px">{escape(DATA["window"])}</text>
{chr(10).join(body)}
</svg>
"""
    (ROOT / "assets" / "review-demo.svg").write_text(svg)
    print(f"wrote assets/review-demo.svg ({len(svg):,} bytes, {len(rows)} lines, {height}px tall)")
    if "--video" in sys.argv:
        video(svg, rows)


def video(svg, rows):
    """One still per distinct moment of the timeline, held for as long as nothing changes."""
    head, tail = svg.split("<style>")[0], svg.split("</style>")[1]
    moments = sorted({0.0, CYCLE - 0.8} | {r["on"] for r in rows} | {r["off"] for r in rows if r.get("off") is not None})
    out = ROOT / "launch"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = []
        for k, t in enumerate(moments):
            shown = ",".join(f"#l{n}" for n, r in enumerate(rows) if r["on"] <= t < (r.get("off") or CYCLE))
            style = (f"<style>text{{font-family:'DejaVu Sans Mono',monospace;font-size:14.5px}}.l{{opacity:0}}"
                     f"{shown}{{opacity:1}}</style>" if shown else "<style>.l{opacity:0}</style>")
            frame = Path(tmp) / f"{k:03d}.png"
            subprocess.run(["rsvg-convert", "-w", str(W * 2), "-o", str(frame)], input=(head + style + tail).encode(),
                           check=True)
            hold = (moments[k + 1] - t) if k + 1 < len(moments) else 3.0
            listing += [f"file '{frame}'", f"duration {hold:.2f}"]
        listing.append(f"file '{frame}'")
        (Path(tmp) / "list.txt").write_text("\n".join(listing) + "\n")
        concat = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(Path(tmp) / "list.txt")]
        subprocess.run(concat + ["-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "20", str(out / "review-demo.mp4")],
                       check=True)
        subprocess.run(concat + ["-fps_mode", "vfr", "-vf", f"scale={W}:-1:flags=lanczos,split[a][b];"
                                 "[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none",
                                 str(out / "review-demo.gif")], check=True)
    for f in ("review-demo.mp4", "review-demo.gif"):
        print(f"wrote launch/{f} ({(out / f).stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
