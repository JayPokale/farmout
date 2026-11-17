import Reveal from "./Reveal";
import SectionHead from "./SectionHead";
import CopyCmd from "./CopyCmd";

// `farmout bench`: the one-command answer to "which AI is best on my code?", and the card people share.
// The card below is the real output of a run on two tqdm bug fixes (bench/README.md), not a mock-up.
const STEPS = [
  {
    t: "Finds your bug fixes",
    b: "Recent commits whose subject says fix, bug, crash or regression, small enough to judge: at most 4 source files and 80 changed lines, plus any tests and docs.",
  },
  {
    t: "Puts each bug back",
    b: "In a scratch git worktree the fix's code is undone, so the bug is an uncommitted change in its real codebase. Its tests and changelog stay, and the copy's history doesn't name the fix, so nothing gives the bug away. Your own tree isn't touched.",
  },
  {
    t: "Asks every subscription",
    b: "Each one reviews the change with farmout's brief and scores when a finding lands within 3 lines of the bug.",
  },
];

export default function Bench() {
  return (
    <section id="bench" className="border-y border-line bg-surface py-28">
      <div className="mx-auto grid max-w-6xl items-center gap-12 px-5 lg:grid-cols-[1fr_1.15fr]">
        <div>
          <SectionHead eyebrow="Bench" title="Which AI is best on your code?">
            Everyone argues about Claude, GPT and Gemini. <code className="font-mono text-fg">farmout bench</code>{" "}
            settles it on your own git history, with the subscriptions you already pay for.
          </SectionHead>
          <Reveal delay={0.06}>
            <ol className="mt-8 space-y-4">
              {STEPS.map((s, i) => (
                <li key={s.t} className="grid grid-cols-[2rem_1fr] gap-3">
                  <span className="grid h-7 w-7 place-items-center rounded-full border border-fg/25 bg-raised font-mono text-xs font-semibold">
                    {i + 1}
                  </span>
                  <div>
                    <p className="font-semibold">{s.t}</p>
                    <p className="mt-0.5 text-sm leading-relaxed text-dim">{s.b}</p>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-8 max-w-md">
              <CopyCmd cmd="farmout bench --share" label="bench" />
            </div>
            <p className="mt-3 max-w-md text-xs leading-relaxed text-dim">
              <code className="font-mono">--share</code> writes a post that fits on X, an image card and a one-click post
              link. The card carries scores only: no repository name, file names or code.
            </p>
          </Reveal>
        </div>
        <Reveal delay={0.1}>
          <figure>
            {/* eslint-disable-next-line @next/next/no-img-element -- a static card, no optimisation needed */}
            <img
              src="/bench-card.png"
              width={1200}
              height={630}
              alt="A farmout bench card: Which AI reviews my code best? Two real bug fixes replayed. Claude 2/2, Codex 2/2, Copilot 2/2, Gemini 1/2."
              className="h-auto w-full rounded-xl border border-line shadow-[0_40px_120px_-50px_#000]"
            />
            <figcaption className="mt-3 text-xs leading-relaxed text-dim">
              A real card: two bug fixes from tqdm&apos;s history, 2026-11-17. Claude, Codex and Copilot found both;
              Antigravity&apos;s fast model found one.
            </figcaption>
          </figure>
        </Reveal>
      </div>
    </section>
  );
}
