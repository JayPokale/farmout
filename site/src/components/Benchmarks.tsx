"use client";

import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { blob, tree } from "@/lib/site";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// Every number here is in bench/README.md, with the raw outputs committed under
// bench/results/. Misses and the unflattering rows stay in.

// Bugs found of 32: 23 planted (sets 1 and 2) + 9 real BugsInPy regressions (set 3).
const RECALL = [
  { name: "antigravity", found: 32, bar: "bg-agy" },
  { name: "claude (Sonnet)", found: 32, bar: "bg-claude" },
  { name: "copilot", found: 30, bar: "bg-copilot" },
  { name: "codex", found: 29, bar: "bg-codex" },
  { name: "all four, in parallel", found: 32, bar: "bg-lanes", self: true },
];

const HEAD = [
  { name: "Claude reads the PDFs itself", tokens: 48097, cost: "$0.148", time: "13s" },
  { name: "Claude + farmout (Antigravity reads)", tokens: 19747, cost: "$0.039", time: "62s", self: true },
];

const COSTS = [
  { q: "Does delegating save Claude's quota?", a: "Yes: −59% head tokens, −74% head cost on three papers. It took 62s instead of 13s." },
  { q: "Do four reviewers beat the best one?", a: "Not when the best one has room: Claude (Sonnet) alone found all 23 planted bugs when allowed 30 findings. Four helped under a short cap (17 vs 10 of 23)." },
  { q: "Does /farmout:review save Claude's quota?", a: "No. Claude merges and verifies four reports: $0.122 and 59s, against $0.033 and 13s reviewing alone." },
  { q: "Does the read guard pay off?", a: "On a 16-page deep read Claude's cost halved ($0.139 → $0.069), but it took 240s instead of 20s." },
];

export default function Benchmarks() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });
  const max = Math.max(...HEAD.map((h) => h.tokens));

  return (
    <section id="numbers" className="border-y border-line bg-surface py-28">
      <div className="mx-auto max-w-6xl px-5">
        <SectionHead eyebrow="Benchmarks" title="Measured, misses included">
          Planted bugs and real regressions, the exact review brief, every worker&apos;s raw output committed.{" "}
          <a href={blob("bench/README.md")} className="text-fg underline underline-offset-4" target="_blank" rel="noopener noreferrer">
            The method and the data
          </a>{" "}
          ship in the repo, so you can rerun any of it on your own subscriptions.
        </SectionHead>

        <div ref={ref} className="mt-12 grid gap-6 lg:grid-cols-[1.25fr_1fr]">
          <div className="rounded-xl border border-line bg-bg p-6 sm:p-8">
            <p className="text-sm font-semibold">Bugs found, of 32</p>
            <p className="mt-1 text-xs text-dim">23 planted in five realistic changes + 9 real regressions from BugsInPy</p>
            <div className="mt-6 space-y-4">
              {RECALL.map((r, i) => (
                <div key={r.name} className="grid grid-cols-[8.5rem_1fr] items-center gap-3 text-sm sm:grid-cols-[10rem_1fr]">
                  <span className={r.self ? "font-semibold" : "text-dim"}>{r.name}</span>
                  <div className="h-8 overflow-hidden rounded-md bg-raised">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={inView ? { width: `${(r.found / 32) * 100}%` } : {}}
                      transition={{ duration: 1, delay: i * 0.12, ease: [0.16, 1, 0.3, 1] }}
                      className={`flex h-full items-center justify-end rounded-md pr-2.5 font-mono text-xs font-semibold text-on-brand ${r.bar} ${r.self ? "" : "opacity-85"}`}
                    >
                      {r.found}
                    </motion.div>
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-6 text-xs leading-relaxed text-dim">
              Every one of the 32 was flagged by at least three of the four models, and 27 by all four.
              In these runs, a finding flagged 4/4 was a real bug every time.
            </p>
          </div>

          <div className="flex flex-col gap-6">
            <div className="rounded-xl border border-line bg-bg p-6">
              <p className="text-sm font-semibold">Claude&apos;s own tokens, reading three papers</p>
              <p className="mt-1 text-xs text-dim">Transformer, BERT, ResNet · 43 pages · same correct answers</p>
              <div className="mt-5 space-y-4">
                {HEAD.map((h, i) => (
                  <div key={h.name}>
                    <div className="flex items-baseline justify-between gap-3 text-xs">
                      <span className={h.self ? "font-semibold" : "text-dim"}>{h.name}</span>
                      <span className="font-mono tabular-nums text-dim">
                        {h.cost} · {h.time}
                      </span>
                    </div>
                    <div className="mt-1.5 h-7 overflow-hidden rounded-md bg-raised">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={inView ? { width: `${(h.tokens / max) * 100}%` } : {}}
                        transition={{ duration: 1, delay: 0.3 + i * 0.15, ease: [0.16, 1, 0.3, 1] }}
                        className={`flex h-full items-center justify-end rounded-md pr-2.5 font-mono text-[11px] font-semibold ${
                          h.self ? "bg-lanes text-on-brand" : "bg-dim/35 text-fg"
                        }`}
                      >
                        {h.tokens.toLocaleString("en-US")}
                      </motion.div>
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-5 text-xs text-dim">
                <span className="text-lanes font-semibold">−59% tokens, −74% cost</span> for the head; the gap grows with
                every page it no longer reads.
              </p>
            </div>

            <div className="rounded-xl border border-line bg-bg p-6">
              <p className="text-sm font-semibold">Four reviews in about a minute</p>
              <p className="mt-2 text-xs leading-relaxed text-dim">
                A parallel call is as slow as its slowest worker. With Antigravity on Flash low, a four-model review
                of a real change took 59s end to end; on its default model, 220s.
              </p>
            </div>
          </div>
        </div>

        <Reveal delay={0.1}>
          <h3 className="mt-16 text-lg font-semibold tracking-tight">What it costs, honestly</h3>
          <div className="pane-scroll mt-5 overflow-x-auto rounded-xl border border-line bg-bg px-5 py-2">
            <table className="w-full text-left text-sm">
              <tbody className="divide-y divide-line">
                {COSTS.map((c) => (
                  <tr key={c.q} className="align-top">
                    <td className="w-[38%] py-3 pr-6 font-medium">{c.q}</td>
                    <td className="py-3 text-dim">{c.a}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-4 text-xs leading-relaxed text-dim">
            Small samples: treat these as a sanity check, not a leaderboard. Better yet, run{" "}
            <code className="font-mono text-fg">farmout bench</code> on your own history. Raw outputs:{" "}
            <a href={tree("bench/results")} target="_blank" rel="noopener noreferrer" className="text-fg underline-offset-4 hover:underline">
              bench/results
            </a>
            .
          </p>
        </Reveal>
      </div>
    </section>
  );
}
