import { ImageResponse } from "next/og";
import { ogFonts } from "@/lib/og-fonts";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "farmout: put every AI subscription you pay for to work";

// The logo's four lanes, as in assets/logo.svg.
const LANES = [
  { d: "M104 256H196C256 256 262 140 326 140H384", c: "#f2a65a", y: 140 },
  { d: "M104 256H196C250 256 262 218 326 218H384", c: "#5ec4b6", y: 218 },
  { d: "M104 256H196C250 256 262 294 326 294H384", c: "#a78bfa", y: 294 },
  { d: "M104 256H196C256 256 262 372 326 372H384", c: "#f47c7c", y: 372 },
];

export default async function OG() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#0b0c11",
          color: "#ece8df",
          padding: 72,
          fontFamily: "Hubot",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <svg width="72" height="72" viewBox="0 0 512 512">
            <rect width="512" height="512" rx="112" fill="#14161f" />
            {LANES.map((l) => (
              <path key={l.c} d={l.d} stroke={l.c} strokeWidth="26" strokeLinecap="round" fill="none" />
            ))}
            <path d="M104 256H204" stroke="#ece8df" strokeWidth="26" strokeLinecap="round" />
            <circle cx="104" cy="256" r="40" fill="#ece8df" />
            {LANES.map((l) => (
              <circle key={l.c} cx="404" cy={l.y} r="30" fill={l.c} />
            ))}
          </svg>
          <div style={{ fontSize: 44 }}>farmout</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", letterSpacing: -2 }}>
          <div style={{ fontSize: 66, lineHeight: 1.04 }}>You already pay for the models.</div>
          <div
            style={{
              fontSize: 66,
              lineHeight: 1.04,
              backgroundImage: "linear-gradient(90deg, #f2a65a, #f47c7c 36%, #a78bfa 68%, #5ec4b6)",
              backgroundClip: "text",
              color: "transparent",
            }}
          >
            Put all of them to work.
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 22, color: "#9a9eaf", fontFamily: "Martian" }}>
          <div style={{ display: "flex", gap: 22 }}>
            <span style={{ color: "#f2a65a" }}>codex</span>
            <span style={{ color: "#5ec4b6" }}>antigravity</span>
            <span style={{ color: "#a78bfa" }}>copilot</span>
            <span style={{ color: "#f47c7c" }}>claude</span>
            <span>local</span>
          </div>
          <div>read-only · zero deps</div>
        </div>
      </div>
    ),
    { ...size, fonts: await ogFonts() },
  );
}
