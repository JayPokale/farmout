"use client";

import { useState } from "react";
import { WORKERS } from "@/data/workers";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// The README's architecture diagram as inline SVG: three ways in on the left,
// farmout's moving parts in the middle, one lane per worker on the right.
// Hovering a box (or its button below, for keyboards) explains it.
const PARTS = [
  {
    id: "cli",
    label: "farmout CLI",
    body: "run, send, fork, review, race, ask, pipe, bench and the rest. Every command takes --json and -C <dir>, and its exit code means something to a script: 0 ok, 1 failed or a review gate tripped, 130 stopped.",
  },
  {
    id: "mcp",
    label: "MCP head",
    body: "farmout is also an MCP server. Claude Code, Codex CLI, Copilot CLI or any MCP client becomes the head: it plans, hands self-contained subtasks to the subscription that fits, and keeps its own quota for the hard parts. The routing policy ships as server instructions, so there's no router model.",
  },
  {
    id: "hook",
    label: "git hook",
    body: "farmout hook install adds a pre-commit (or pre-push) hook that runs review --staged --min-agree 2 --fail-on high: the commit is blocked when two or more models flag a high-severity bug.",
  },
  {
    id: "sessions",
    label: "sessions",
    body: "Every session has an inbox and a background runner that holds its lock. That's why send to a busy session queues instead of failing, why attach works from any terminal, and why stop stops exactly one worker.",
  },
  {
    id: "worktrees",
    label: "worktrees",
    body: "--isolate gives a worker its own git worktree in .farmout/worktrees/, with your uncommitted and untracked files copied in. Its work comes back as a patch, and your tree changes only when you apply it.",
  },
  {
    id: "merge",
    label: "merge + check",
    body: "Reviews merge across models by file, line and defect, with how many models flagged each. A finding that cites a file or line that doesn't exist is dropped. --min-agree and --fail-on turn the result into a gate.",
  },
  {
    id: "limits",
    label: "limits",
    body: "When a subscription hits its usage limit, farmout notes the reset time it reports (15 minutes if it doesn't say), skips that worker until then, and runs the task on the next one in your fallback list.",
  },
] as const;

const HEADS = [
  { id: "cli", y: 48, title: "farmout CLI", sub: "you, in a terminal" },
  { id: "mcp", y: 142, title: "MCP head", sub: "Claude Code · Codex · any client" },
  { id: "hook", y: 236, title: "git hook", sub: "review on commit or push" },
];

const ROWS = [
  { id: "sessions", y: 82, title: "sessions", sub: "inbox · runner · resume" },
  { id: "worktrees", y: 142, title: "worktrees", sub: "isolate · patch · apply" },
  { id: "merge", y: 202, title: "merge + check", sub: "agreement · citations" },
  { id: "limits", y: 262, title: "limits", sub: "reset times · fallback" },
];

// one chip per worker, spread down the right-hand side
const CHIP_Y = WORKERS.map((_, i) => 12 + i * 54);

export default function HowItWorks() {
  const [hover, setHover] = useState<string | null>(null);
  const on = (id: string) => hover === id;
  const box = (id: string) => ({
    onMouseEnter: () => setHover(id),
    onMouseLeave: () => setHover(null),
    style: { cursor: "pointer" as const },
  });
  const flowing = hover === "sessions" || hover === "worktrees" || hover === "limits" || hover === "merge";
  const text = (() => {
    const part = PARTS.find((p) => p.id === hover);
    if (part) return part.body;
    const w = WORKERS.find((x) => x.id === hover);
    if (w) return `${w.product}, on ${w.account}. Read-only: ${w.readOnly}. To write: ${w.write}. Resumes with ${w.resume}.`;
    return "Hover a box, in the diagram or below it.";
  })();

  return (
    <section id="how" className="py-28">
      <div className="mx-auto max-w-6xl px-5">
        <SectionHead eyebrow="How it works" title="Three ways in, one lane per subscription" center>
          You, a coding agent or a git hook hands farmout the work. farmout fans it out to the CLIs you
          have, keeps every conversation, and brings the answers back merged.
        </SectionHead>

        <Reveal delay={0.08}>
          <div className="pane-scroll mt-10 overflow-x-auto">
            <svg
              viewBox="0 0 760 340"
              className="mx-auto block w-full min-w-[640px] max-w-[880px]"
              role="img"
              aria-label="Diagram. On the left, three ways in: the farmout CLI, an MCP head such as Claude Code, and a git hook. They all feed farmout, which holds sessions, worktrees, the review merge and usage limits. From farmout one lane runs to each worker: antigravity, codex, claude, copilot, deepseek and an OpenAI-compatible api."
            >
              <defs>
                <marker id="hw-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-dim)" />
                </marker>
                <marker id="hw-ar-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-fg)" />
                </marker>
              </defs>

              {/* ways in */}
              {HEADS.map((h) => (
                <g key={h.id} {...box(h.id)}>
                  <rect x="8" y={h.y} width="184" height="56" rx="8" fill="var(--color-raised)"
                    stroke={on(h.id) ? "var(--color-fg)" : "var(--color-line)"} strokeWidth={on(h.id) ? 1.6 : 1} />
                  <text x="100" y={h.y + 24} textAnchor="middle" fontSize="12" fontWeight="600" fill="currentColor">{h.title}</text>
                  <text x="100" y={h.y + 41} textAnchor="middle" fontSize="10.5" fill="var(--color-dim)">{h.sub}</text>
                </g>
              ))}
              {[
                { id: "cli", d: "M192 76 C222 76 222 150 248 150" },
                { id: "mcp", d: "M192 170 L248 170" },
                { id: "hook", d: "M192 264 C222 264 222 190 248 190" },
              ].map((a) => (
                <path key={a.id} d={a.d} fill="none" stroke={on(a.id) ? "var(--color-fg)" : "var(--color-dim)"}
                  strokeWidth={on(a.id) ? 1.6 : 1} markerEnd={on(a.id) ? "url(#hw-ar-on)" : "url(#hw-ar)"} />
              ))}

              {/* farmout */}
              <rect x="252" y="24" width="220" height="302" rx="14" fill="var(--color-bg)" stroke="var(--color-fg)" strokeOpacity="0.55" strokeWidth="1.4" />
              <text x="362" y="56" textAnchor="middle" fontSize="15" fontWeight="700" fill="currentColor" style={{ fontFamily: "var(--font-hubot)" }}>farmout</text>
              <text x="362" y="72" textAnchor="middle" fontSize="9.5" fill="var(--color-dim)">standard-library Python</text>
              {ROWS.map((r) => (
                <g key={r.id} {...box(r.id)}>
                  <rect x="268" y={r.y} width="188" height="50" rx="7" fill={on(r.id) ? "var(--color-raised)" : "var(--color-surface)"}
                    stroke={on(r.id) ? "var(--color-fg)" : "var(--color-line)"} strokeWidth={on(r.id) ? 1.6 : 1} />
                  <text x="362" y={r.y + 21} textAnchor="middle" fontSize="11.5" fontWeight="600" fill="currentColor">{r.title}</text>
                  <text x="362" y={r.y + 37} textAnchor="middle" fontSize="10" fill="var(--color-dim)">{r.sub}</text>
                </g>
              ))}

              {/* one lane per worker */}
              {WORKERS.map((w, i) => {
                const mid = CHIP_Y[i] + 22;
                const lit = on(w.id);
                const dim = hover !== null && WORKERS.some((x) => x.id === hover) && !lit;
                return (
                  <g key={w.id} {...box(w.id)}>
                    <path
                      d={`M472 175 H500 C548 175 548 ${mid} 592 ${mid}`}
                      fill="none"
                      stroke={w.color}
                      strokeWidth={lit ? 3 : 1.8}
                      strokeOpacity={dim ? 0.25 : 0.9}
                      strokeDasharray={flowing ? "6 5" : undefined}
                    >
                      {flowing && (
                        <animate attributeName="stroke-dashoffset" from={hover === "merge" ? "0" : "22"} to={hover === "merge" ? "22" : "0"} dur="0.8s" repeatCount="indefinite" />
                      )}
                    </path>
                    <rect x="596" y={CHIP_Y[i]} width="156" height="44" rx="8" fill="var(--color-raised)"
                      stroke={lit ? w.color : "var(--color-line)"} strokeWidth={lit ? 1.8 : 1} opacity={dim ? 0.5 : 1} />
                    <circle cx="612" cy={mid} r="4" fill={w.color} opacity={dim ? 0.5 : 1} />
                    <text x="624" y={mid - 3} fontSize="12" fontWeight="600" fill={w.color} opacity={dim ? 0.5 : 1} style={{ fontFamily: "var(--font-martian)" }}>
                      {w.id}
                    </text>
                    <text x="624" y={mid + 12} fontSize="9.5" fill="var(--color-dim)" opacity={dim ? 0.5 : 1}>
                      {w.cli === "api" ? "OpenAI-compatible" : `${w.cli} · read-only`}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </Reveal>

        <div className="mx-auto mt-6 flex max-w-3xl flex-wrap justify-center gap-2">
          {PARTS.map((p) => (
            <button
              key={p.id}
              type="button"
              onMouseEnter={() => setHover(p.id)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(p.id)}
              onBlur={() => setHover(null)}
              aria-pressed={hover === p.id}
              className={`rounded border px-3 py-1 font-mono text-[11px] transition-all ${
                hover === p.id ? "-translate-y-0.5 border-fg/60 bg-raised text-fg shadow-sm" : "border-line text-dim hover:border-fg/40 hover:text-fg"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <p aria-live="polite" className="mx-auto mt-4 min-h-[4.5rem] max-w-2xl text-center text-sm leading-relaxed text-dim">
          {text}
        </p>
      </div>
    </section>
  );
}
