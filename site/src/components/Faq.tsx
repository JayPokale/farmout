"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import SectionHead from "./SectionHead";

const QA = [
  {
    q: "Is this allowed by the providers' terms?",
    a: "farmout runs each vendor's official CLI in its documented non-interactive mode, logged in as you, exactly as you could from a script. It doesn't extract tokens, scrape web apps or proxy APIs. You're still responsible for each provider's terms: don't use it to share accounts or resell access.",
  },
  {
    q: "Will it burn my other quotas?",
    a: "Every task spends the quota of the worker that runs it. review, ask and race send one task per worker; -w picks which. farmout stats shows each subscription's runs, failures, limits, time and tokens, and when one hits its limit, the task can fall back to the next.",
  },
  {
    q: "Does it need Claude Code?",
    a: "No. The CLI needs only the worker CLIs you have. Claude Code is one possible head over MCP; Codex CLI, Copilot CLI or any MCP client works too, and a git hook or a cron job works with no head at all.",
  },
  {
    q: "Does a review save Claude's quota?",
    a: "The CLI's farmout review uses none of it, unless your second Claude account is one of the workers. Inside Claude Code, /farmout:review costs Claude more than reviewing alone, because Claude merges and verifies four reports. Use it for agreement; delegate reading and grunt work to save quota.",
  },
  {
    q: "Is it slower?",
    a: "A parallel call is as slow as its slowest worker: about a minute for a four-model review, a few minutes for a race. --bg returns at once; farmout attach and farmout wait pick it back up.",
  },
  {
    q: "What leaves my machine?",
    a: "Briefs and the files you hand over go to each worker's provider, as they would if you used that CLI yourself. The workers setting keeps a repository away from accounts you choose, redact = true strips credentials from briefs, and the usage log never records prompts or outputs.",
  },
  {
    q: "How is it different from OpenAI's Codex plugin for Claude Code?",
    a: "That plugin drives Codex only, from Claude Code. farmout drives whichever subscriptions you have, several at once, from your terminal or any MCP client, with sessions, pipelines, cross-model review and races.",
  },
  {
    q: "Does it work with DeepSeek?",
    a: "Three ways. The deepseek worker drives DeepSeek Harness (dsh) headless, read-only by default and resumed by session id; it's experimental until it has had a real smoke test. The api worker talks to DeepSeek's API directly (FARMOUT_API_BASE=https://api.deepseek.com, model deepseek-flash). And DeepSeek Harness can load farmout as an MCP server, so a dsh agent can hand work to your other subscriptions.",
  },
  {
    q: "Which platforms?",
    a: "Linux is tested; macOS should work but is untested; Windows isn't supported before v1.0. Python 3.9 or newer, git, and at least one worker CLI.",
  },
];

export default function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section id="faq" className="border-t border-line py-28">
      <div className="mx-auto max-w-3xl px-5">
        <SectionHead eyebrow="FAQ" title="Questions, answered" center />
        <div className="mt-12 space-y-3">
          {QA.map((item, i) => (
            <div
              key={item.q}
              className="rounded-xl border border-line bg-surface px-5 transition-colors has-[[aria-expanded=true]]:border-fg/40"
            >
              <button
                type="button"
                onClick={() => setOpen(open === i ? null : i)}
                className="flex w-full items-center justify-between gap-4 py-4 text-left font-medium"
                aria-expanded={open === i}
              >
                <span>{item.q}</span>
                <span
                  aria-hidden
                  className={`grid h-6 w-6 shrink-0 place-items-center rounded-md bg-raised text-dim transition-transform ${open === i ? "rotate-45" : ""}`}
                >
                  +
                </span>
              </button>
              <AnimatePresence initial={false}>
                {open === i && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                    className="overflow-hidden"
                  >
                    <p className="pb-5 text-sm leading-relaxed text-dim">{item.a}</p>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
