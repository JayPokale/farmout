"use client";

import { useState } from "react";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";
import { Glyph } from "./icons";

// The three things people come for (the limit story first), each with the commands
// that do it, then the rest of the toolbox. Commands match the README's quick tour.
const MAIN = [
  {
    icon: "limit",
    what: "Keep working at the limit",
    name: "fallback · relay · send -w",
    body: "When a subscription hits its usage limit or its login lapses, the task or conversation moves to the next ready model with the turns so far, instead of failing. In Claude Code itself, keep typing at the limit: another subscription answers there with the whole conversation, and Claude catches up when it's back. With one subscription, its models fill the lanes, and bench tells you which one earns your quota.",
    demo: [
      ["Claude Code out? keep typing", "farmout relay on --write"],
      ["out of Claude? it goes on", 'farmout run claude "Plan the cache layer" -n cache'],
      ["switch on purpose", 'farmout send cache -w codex "What did we decide?"'],
      ["one subscription", "farmout review -w claude:opus,claude:sonnet"],
      ["is Opus worth it?", "farmout bench -w claude:opus,claude:sonnet --apply"],
    ],
  },
  {
    icon: "review",
    what: "Cross-model review",
    name: "farmout review",
    body: "Every worker reviews your uncommitted change, your staged files or a branch, at the same time and read-only. Findings merge across models with agreement counts, and a finding that cites a file or line that doesn't exist is dropped before you see it.",
    demo: [
      ["your change", "farmout review"],
      ["a branch, 2+ agree", "farmout review --base main --min-agree 2"],
      ["as a commit gate", "farmout review --staged --fail-on high"],
      ["follow up", 'farmout send review-1-codex "Show me the input that breaks line 18"'],
    ],
  },
  {
    icon: "session",
    what: "Conversations as a graph",
    name: "run · send · fork · ui",
    body: "Named conversations with any worker. Branch off any turn onto another model and keep both answers; farmout ui draws every conversation as a branching graph in your terminal, where you read any turn, continue it or branch off it. They survive a renamed or moved folder.",
    demo: [
      ["start", 'farmout run codex "Add tests for parse_date()" --isolate -n dates'],
      ["continue", 'farmout send dates "Also cover leap years"'],
      ["branch off turn 1", 'farmout fork dates --at 1 -w claude "Test time zones instead"'],
      ["walk the graph", "farmout ui   # or farmout tree to print it"],
    ],
  },
];

const MORE = [
  { icon: "race", name: "Races your tests judge", cmd: 'farmout race "..." --test "pytest -x"', body: "Every model attempts the same change in its own git worktree. Passing tests win, then the smallest diff; your tree stays untouched until you apply the winner." },
  { icon: "debate", name: "Models that argue", cmd: "farmout ask --debate", body: "Every model answers, reads the others' answers, then answers again." },
  { icon: "bench", name: "Your code, your leaderboard", cmd: "farmout bench -n 3 --share", body: "Recent bug fixes from your own history are undone in a scratch worktree, and each model is scored on finding them. --share makes a card: scores only, never your code." },
  { icon: "pr", name: "Pull request feedback, raced", cmd: "farmout fix-pr 128", body: "Review comments and failing checks (through gh) become a race your tests judge." },
  { icon: "hook", name: "Review on every commit", cmd: "farmout hook install", body: "The commit is blocked when two or more models flag a high-severity bug. FARMOUT_SKIP=1 skips it once." },
  { icon: "terminal", name: "Watch, share, script", cmd: "farmout ls --watch", body: "A live dashboard, status --line for tmux, export --html to share a conversation, --json on every command." },
];

function Icon({ name }: { name: string }) {
  return (
    <span className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-raised text-fg">
      <Glyph name={name} />
    </span>
  );
}

export default function Features() {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section id="features" className="mx-auto max-w-6xl px-5 py-28">
      <SectionHead eyebrow="Features" title="Every way to put them together" center>
        One model&apos;s answer is a guess. Several at once are a review with agreement counts, a race
        your tests settle, or a second opinion you can follow up with.
      </SectionHead>

      <div className="mt-14 grid gap-4 md:grid-cols-3">
        {MAIN.map((a, i) => (
          <Reveal key={a.name} delay={i * 0.08}>
            <div className="lift flex h-full flex-col rounded-xl border border-line bg-surface p-6">
              <Icon name={a.icon} />
              <p className="mono-label mt-5 text-dim">{a.what}</p>
              <h3 className="mt-2 font-mono text-lg font-semibold">{a.name}</h3>
              <p className="mt-3 flex-1 text-sm leading-relaxed text-dim">{a.body}</p>
              <button
                type="button"
                onClick={() => setOpen(open === i ? null : i)}
                aria-expanded={open === i}
                className="link-sweep mt-5 w-fit text-sm font-medium"
              >
                {open === i ? "Hide the commands" : "See the commands →"}
              </button>
              {open === i && (
                <dl className="mt-4 space-y-2.5 rounded-lg border border-term-line bg-term p-3 text-term-fg">
                  {a.demo.map(([k, v]) => (
                    <div key={k}>
                      <dt className="font-mono text-[9.5px] uppercase tracking-wider text-term-dim">{k}</dt>
                      <dd className="mt-0.5 break-words font-mono text-[11px] leading-relaxed">
                        <span className="select-none text-term-dim">$ </span>
                        {v}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </Reveal>
        ))}
        {MORE.map((e, i) => (
          <Reveal key={e.name} delay={0.16 + i * 0.05}>
            <div className="lift h-full rounded-xl border border-line bg-surface p-6">
              <Icon name={e.icon} />
              <h3 className="mt-5 text-base font-semibold">{e.name}</h3>
              <p className="mt-1.5 font-mono text-[11.5px] text-dim">{e.cmd}</p>
              <p className="mt-3 text-sm leading-relaxed text-dim">{e.body}</p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
