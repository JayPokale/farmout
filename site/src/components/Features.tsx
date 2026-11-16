"use client";

import { useState } from "react";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";
import { Glyph } from "./icons";

// The three things people come for, each with the commands that do it, then the
// rest of the toolbox. Commands match the README's quick tour.
const MAIN = [
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
    icon: "race",
    what: "Races your tests judge",
    name: "farmout race",
    body: "Every model attempts the same change in its own git worktree, with your uncommitted work copied in. Passing tests win, then the smallest diff, then the fastest. Your tree stays untouched until you apply the winner.",
    demo: [
      ["race", 'farmout race "Make the flaky test deterministic" --test "pytest -x"'],
      ["read the patch", "farmout diff race-1-codex"],
      ["land it", "farmout apply race-1-codex   # or --pr for a pull request"],
      ["take it back", "farmout undo race-1-codex"],
    ],
  },
  {
    icon: "session",
    what: "Sessions that remember",
    name: "run · send · fork",
    body: "Named conversations with any worker. Send while it's busy and the message waits its turn; attach from any terminal; fork a side question with the same context; hand one session's reply to another.",
    demo: [
      ["start", 'farmout run codex "Add tests for parse_date()" --isolate -n dates'],
      ["continue", 'farmout send dates "Also cover leap years"'],
      ["hand over", 'farmout send dates --attach review-1-claude "Fix what this found"'],
      ["side question", 'farmout fork review-1-codex "Why does line 42 race?"'],
    ],
  },
];

const MORE = [
  { icon: "debate", name: "Models that argue", cmd: "farmout ask --debate", body: "Every model answers, reads the others' answers, then answers again." },
  { icon: "bench", name: "Your code, your leaderboard", cmd: "farmout bench -n 3", body: "Recent bug fixes from your own history are undone in a scratch worktree, and each model is scored on finding them." },
  { icon: "pr", name: "Pull request feedback, raced", cmd: "farmout fix-pr 128", body: "Review comments and failing checks (through gh) become a race your tests judge." },
  { icon: "limit", name: "Usage limits, handled", cmd: "--fallback codex,copilot", body: "A worker at its limit sits out until the reset it reported, and the task moves to the next. farmout stats shows what each one did." },
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
