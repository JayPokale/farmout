"use client";

import { useState } from "react";
import { motion } from "motion/react";
import { INSTALL, REPO_URL, blob } from "@/lib/site";
import { GitHubIcon, Glyph, Mark } from "./icons";

const TAGS = [
  { icon: "shield", label: "Read-only by default" },
  { icon: "deps", label: "Zero dependencies" },
  { icon: "key", label: "Your logins, no API keys" },
  { icon: "branch", label: "Git-worktree isolation" },
  { icon: "plug", label: "CLI + MCP server" },
  { icon: "limit", label: "Works with one subscription" },
];

// The logo, life-size: one bone line runs into the install card from the left
// edge of the screen, and four lanes leave it on the right, one per worker. A
// pulse of light travels the root line, then all four lanes, on a loop.
//
// The SVGs use a fixed scale ("slice", anchored at the card) rather than
// stretching, so curves never distort and dash lengths stay true; whatever
// doesn't fit the screen is simply cropped.
const LANES = [
  { id: "codex", y: 40, color: "var(--color-codex)", label: "codex · ChatGPT" },
  { id: "agy", y: 122, color: "var(--color-agy)", label: "antigravity · Google" },
  { id: "copilot", y: 198, color: "var(--color-copilot)", label: "copilot · GitHub" },
  { id: "claude", y: 280, color: "var(--color-claude)", label: "claude · second account" },
];

function RootLine() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 1400 320"
      preserveAspectRatio="xMaxYMid slice"
      className="pointer-events-none absolute right-full top-1/2 hidden h-[320px] w-[calc(50vw_-_50%)] -translate-y-1/2 lg:block"
    >
      <defs>
        {/* user-space units: a perfectly horizontal line has a zero-height box, and a
            bounding-box gradient on it would not render at all */}
        <linearGradient id="root-fade" x1="760" x2="1400" y1="0" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--color-fg)" stopOpacity="0" />
          <stop offset="1" stopColor="var(--color-fg)" stopOpacity="0.55" />
        </linearGradient>
      </defs>
      <path d="M760 160H1400" stroke="url(#root-fade)" strokeWidth="2" fill="none" />
      <path d="M760 160H1400" pathLength={1000} className="lane-pulse root" stroke="var(--color-fg)" strokeOpacity="0.3" strokeWidth="7" strokeLinecap="round" fill="none" />
      <path d="M760 160H1400" pathLength={1000} className="lane-pulse root" stroke="var(--color-fg)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
    </svg>
  );
}

function FanLanes() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 1400 320"
      preserveAspectRatio="xMinYMid slice"
      className="pointer-events-none absolute left-full top-1/2 hidden h-[320px] w-[calc(50vw_-_50%)] -translate-y-1/2 lg:block"
    >
      <defs>
        {LANES.map((l) => (
          <linearGradient key={l.id} id={`fade-${l.id}`} x1="0" x2="640" y1="0" y2="0" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor={l.color} stopOpacity="0.75" />
            <stop offset="0.55" stopColor={l.color} stopOpacity="0.45" />
            <stop offset="1" stopColor={l.color} stopOpacity="0" />
          </linearGradient>
        ))}
      </defs>
      {LANES.map((l, i) => {
        const d = `M0 160H24C120 160 100 ${l.y} 196 ${l.y}H640`;
        return (
          <g key={l.id}>
            <path d={d} stroke={`url(#fade-${l.id})`} strokeWidth="2" fill="none" />
            <path d={d} pathLength={1000} className="lane-pulse fan" style={{ animationDelay: `${i * 0.08}s` }} stroke={l.color} strokeOpacity="0.35" strokeWidth="7" strokeLinecap="round" fill="none" />
            <path d={d} pathLength={1000} className="lane-pulse fan" style={{ animationDelay: `${i * 0.08}s` }} stroke={l.color} strokeWidth="2.5" strokeLinecap="round" fill="none" />
            <text x="214" y={l.y - 9} fill={l.color} fontSize="10.5" fontFamily="var(--font-martian), monospace" opacity="0.9">
              {l.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function InstallCard() {
  const [copied, setCopied] = useState(false);
  return (
    <div className="neon relative w-full max-w-[640px]">
      <div className="overflow-hidden rounded-[11px] bg-term text-left">
        <div className="flex items-center gap-2.5 border-b border-term-line bg-white/[0.02] px-4 py-3">
          <Mark size={20} />
          <span className="flex-1 text-sm font-medium text-term-fg">One install. Every subscription you have.</span>
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(INSTALL);
              setCopied(true);
              setTimeout(() => setCopied(false), 1600);
            }}
            aria-label={copied ? "Copied" : `Copy ${INSTALL}`}
            className={`rounded p-1 transition-colors ${copied ? "text-term-pass" : "text-term-dim hover:text-term-fg"}`}
          >
            <Glyph name={copied ? "check" : "copy"} size={16} />
          </button>
        </div>
        <pre className="pane-scroll overflow-x-auto px-5 py-4 font-mono text-[12.5px] leading-[1.9] sm:px-6">
          <code>
            <span className="text-term-codex">pipx</span> <span className="text-term-fg">install git+{REPO_URL}</span>
            {"\n"}
            <span className="text-term-dim"># Python 3.9+ · zero dependencies · runs on the logins you have</span>
            {"\n"}
            <span className="text-term-dim"># then: </span>
            <span className="text-term-fg/85">farmout</span> <span className="text-term-agy">doctor --live</span>
            <span className="text-term-dim">  ·  </span>
            <span className="text-term-fg/85">farmout</span> <span className="text-term-copilot">review</span>
          </code>
        </pre>
      </div>
    </div>
  );
}

const ease = [0.16, 1, 0.3, 1] as const;

export default function Hero() {
  return (
    <header id="top" className="hero-wash relative overflow-hidden px-5 pt-36 pb-20 sm:pt-40 sm:pb-24">
      <div aria-hidden className="dot-field pointer-events-none absolute inset-0" />
      <div aria-hidden className="aurora" />

      <div className="relative mx-auto flex max-w-6xl flex-col items-center gap-10 sm:gap-14">
        <div className="flex flex-col items-center gap-7 sm:gap-9">
          <motion.a
            href={blob("CHANGELOG.md")}
            target="_blank"
            rel="noopener noreferrer"
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease }}
            className="group flex items-center gap-2 rounded-full border border-line bg-surface/70 py-1 pl-1 pr-3 text-xs text-dim backdrop-blur transition-colors hover:border-fg/30 hover:text-fg"
          >
            <span className="rounded-full bg-fg/[0.08] px-2 py-0.5 font-mono text-[10.5px] text-fg">v0.1.0</span>
            CLI, MCP server and Claude Code plugin
            <span aria-hidden className="transition-transform group-hover:translate-x-0.5">→</span>
          </motion.a>

          <motion.div
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.6, ease }}
            className="flex flex-col items-center gap-6 text-center"
          >
            <h1 className="display text-balance text-[2.35rem] font-extrabold leading-[1.02] sm:text-6xl lg:text-[4.6rem]">
              Hit your limit less.
              <span className="text-lanes block text-balance">Keep working when you do.</span>
            </h1>
            <p className="max-w-2xl text-base leading-relaxed text-fg/80 sm:text-lg">
              Hand long reads and reviews to the other AI subscriptions you pay for (Codex, Gemini, GitHub
              Copilot, DeepSeek, a second Claude, local models) so your main model&apos;s quota lasts. When one
              hits its limit, the task moves to the next and the conversation goes with it. From your
              terminal, or from inside Claude Code.
            </p>
          </motion.div>

          <motion.ul
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.6, delay: 0.1, ease }}
            className="flex max-w-3xl flex-wrap justify-center gap-2"
          >
            {TAGS.map((t) => (
              <li
                key={t.label}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-full border border-fg/10 bg-fg/[0.04] px-3.5 py-1.5 text-xs text-fg/70 transition-colors hover:border-fg/30 hover:text-fg"
              >
                <Glyph name={t.icon} size={12} />
                {t.label}
              </li>
            ))}
          </motion.ul>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2, ease }}
          className="relative flex w-full justify-center"
        >
          <div className="relative w-full max-w-[640px]">
            <RootLine />
            <InstallCard />
            <FanLanes />
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.94 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5, delay: 0.3, ease }}
          className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row sm:gap-4"
        >
          <a
            href="#get-started"
            className="rounded-md bg-brand px-7 py-3 text-center text-base font-medium text-on-brand transition-colors hover:bg-brand-hover"
          >
            Get started
          </a>
          <a
            href="#demo"
            className="rounded-md border border-fg/25 px-7 py-3 text-center text-base font-medium text-fg transition-colors hover:border-fg/60 hover:bg-fg/5"
          >
            Watch a real run ↓
          </a>
        </motion.div>

        <motion.a
          href={REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 0.45 }}
          className="-mt-4 flex items-center gap-2 text-sm text-dim transition-colors hover:text-fg sm:-mt-8"
        >
          <GitHubIcon size={14} />
          Free and MIT licensed · star it on GitHub
        </motion.a>
      </div>
    </header>
  );
}
