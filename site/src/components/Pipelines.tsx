"use client";

import { useEffect, useRef, useState } from "react";
import { useInView } from "motion/react";
import { blob } from "@/lib/site";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// The README's example pipeline, as written, beside a schematic of a run moving
// through it: each stage starts when the one before it ends, gates decide
// whether the next one starts, and an approval pauses the run until you say so.
// It is a diagram of the mechanics, not a recorded run, so it shows no timings.
const TOML = `description = "Plan with Claude, build with Codex in a worktree, ship after I approve"

[[stage]]
name = "plan"
worker = "claude"
prompt = "Write a short plan for: {args}"

[[stage]]
name = "build"
worker = "codex"
isolate = true
prompt = "Implement the plan in {plan}. Run the tests."
test = "pytest -x"
gate = "tests"        # only if the tests pass

[[stage]]
name = "review"
skill = "review"
in = "build"          # review the build's worktree
gate = "high == 0"

[[stage]]
name = "notes"
worker = "copilot"
approve = true        # waits for: farmout pipe approve
prompt = "Release notes for {build.patch}"`;

const STAGES = [
  { name: "plan", who: "claude", cls: "text-claude", dot: "bg-claude", meta: "reads {args}", done: "plan.md" },
  { name: "build", who: "codex", cls: "text-codex", dot: "bg-codex", meta: "own worktree · pytest -x", done: "gate: tests ✓" },
  { name: "review", who: "every worker", cls: "text-fg", dot: "bg-lanes", meta: "skill review · in build", done: "gate: high == 0 ✓" },
  { name: "notes", who: "copilot", cls: "text-copilot", dot: "bg-copilot", meta: "approve = true", done: "approved ✓" },
];

// 0..3 running stage i; 3.5 waiting for approval; 4 finished.
const SCRIPT: [number, number][] = [
  [0, 1400], [1, 2000], [2, 1800], [3.5, 2200], [3, 1200], [4, 2600],
];

export default function Pipelines() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: "-100px" });
  const [step, setStep] = useState(-1);

  useEffect(() => {
    if (!inView) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStep(4);
      return;
    }
    let i = 0;
    let t: ReturnType<typeof setTimeout>;
    const next = () => {
      const [s, ms] = SCRIPT[i % SCRIPT.length];
      setStep(s);
      i++;
      t = setTimeout(next, ms);
    };
    next();
    return () => clearTimeout(t);
  }, [inView]);

  const state = (i: number) =>
    step === 3.5 && i === 3 ? "waiting" : step >= 4 || i < Math.floor(step) || (step === 3.5 && i < 3) ? "done" : i === step ? "running" : "pending";

  return (
    <section id="pipelines" className="border-y border-line bg-surface py-28">
      <div className="mx-auto max-w-6xl px-5">
        <SectionHead eyebrow="Pipelines" title="When one finishes, the next one starts">
          Chain workers into stages in a TOML or JSON file. Gates on tests, agreement or severity decide
          whether the next stage runs; <code className="font-mono text-fg">foreach</code> fans a list out
          in parallel; <code className="font-mono text-fg">approve</code> waits for you. A failed or
          stopped run picks up where it stopped with <code className="font-mono text-fg">farmout pipe resume</code>.
        </SectionHead>

        <div className="mt-12 grid gap-6 lg:grid-cols-[1.05fr_1fr]">
          <Reveal>
            <div className="h-full overflow-hidden rounded-xl border border-term-line bg-term">
              <p className="border-b border-term-line px-4 py-2.5 font-mono text-[11px] text-term-dim">
                .farmout/pipelines/ship.toml
              </p>
              <pre className="pane-scroll overflow-x-auto p-4 font-mono text-[11px] leading-[1.7] text-term-fg" style={{ fontStretch: "87.5%" }}>
                {TOML.split("\n").map((l, i) => {
                  const comment = l.indexOf("#");
                  const code = comment >= 0 ? l.slice(0, comment) : l;
                  const kv = code.match(/^(\s*)([a-z]+)(\s*=\s*)(.*)$/);
                  return (
                    <span key={i} className="block">
                      {l.startsWith("[[") ? (
                        <span className="text-term-agy">{l}</span>
                      ) : kv ? (
                        <>
                          {kv[1]}
                          <span className="text-term-copilot">{kv[2]}</span>
                          <span className="text-term-dim">{kv[3]}</span>
                          <span className={/^"/.test(kv[4]) ? "text-term-codex" : "text-term-fg"}>{kv[4]}</span>
                        </>
                      ) : (
                        <span className="text-term-codex">{code}</span>
                      )}
                      {comment >= 0 && <span className="text-term-dim">{l.slice(comment)}</span>}
                      {l === "" && " "}
                    </span>
                  );
                })}
              </pre>
            </div>
          </Reveal>

          <Reveal delay={0.08}>
            <div ref={ref} className="flex h-full flex-col rounded-xl border border-line bg-bg p-5 sm:p-6">
              <p className="font-mono text-xs text-dim">
                <span className="select-none">$ </span>
                <span className="text-fg">farmout pipe run ship &quot;Add CSV export to the reports page&quot;</span>
              </p>
              <ol className="relative my-auto space-y-3 pt-6">
                {STAGES.map((s, i) => {
                  const st = state(i);
                  return (
                    <li
                      key={s.name}
                      className={`relative flex items-center gap-4 rounded-lg border px-4 py-3 transition-all duration-500 ${
                        st === "running" || st === "waiting"
                          ? "border-fg/50 bg-raised"
                          : st === "done"
                            ? "border-line bg-surface"
                            : "border-line/60 bg-transparent opacity-50"
                      }`}
                    >
                      <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${s.dot}`} />
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-sm font-semibold">
                          {s.name} <span className={`font-normal ${s.cls}`}>· {s.who}</span>
                        </p>
                        <p className="font-mono text-[10.5px] text-dim">{s.meta}</p>
                      </div>
                      <span className="shrink-0 font-mono text-[11px]">
                        {st === "running" && (
                          <span className="text-fg">
                            <span className="spin mr-1">◐</span>running
                          </span>
                        )}
                        {st === "waiting" && <span className="text-codex">⏸ waiting for you</span>}
                        {st === "done" && <span className="text-pass">{s.done}</span>}
                        {st === "pending" && <span className="text-dim">pending</span>}
                      </span>
                    </li>
                  );
                })}
              </ol>
              <p className="mt-5 min-h-[1.5rem] text-xs text-dim">
                {step === 3.5 ? (
                  <span className="font-mono text-[11px]">
                    <span className="select-none">$ </span>
                    <span className="text-fg">farmout pipe approve ship-1</span>
                  </span>
                ) : step >= 4 ? (
                  "Done: every gate passed, and the notes waited for your approval."
                ) : (
                  "Every stage is a session: attach to it, or send it a follow-up."
                )}
              </p>
            </div>
          </Reveal>
        </div>

        <Reveal delay={0.1}>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-line bg-bg p-5">
              <p className="font-mono text-sm font-semibold">fix <span className="font-normal text-dim">· built in</span></p>
              <p className="mt-2 text-sm leading-relaxed text-dim">
                Review your change, fix what two or more models agree on in a worktree, then review the fix.
              </p>
              <p className="mt-3 break-all font-mono text-[11.5px]">
                <span className="select-none text-dim">$ </span>farmout pipe run fix --set test=&quot;pytest -x&quot;
              </p>
            </div>
            <div className="rounded-xl border border-line bg-bg p-5">
              <p className="font-mono text-sm font-semibold">spec <span className="font-normal text-dim">· built in</span></p>
              <p className="mt-2 text-sm leading-relaxed text-dim">
                Requirements, then a design, then independent tasks, each built in its own worktree in
                parallel, then one check of all the patches together.
              </p>
              <p className="mt-3 break-all font-mono text-[11.5px]">
                <span className="select-none text-dim">$ </span>farmout pipe run spec &quot;Add CSV export to the reports page&quot;
              </p>
            </div>
          </div>
          <p className="mt-4 text-xs text-dim">
            Placeholders: <code className="font-mono">{"{stage}"}</code> is a stage&apos;s output,{" "}
            <code className="font-mono">{"{stage.patch}"}</code> its patch, <code className="font-mono">{"{args}"}</code>{" "}
            your text, <code className="font-mono">{"{item}"}</code> a foreach item. Gates: <code className="font-mono">ok</code>,{" "}
            <code className="font-mono">tests</code>, <code className="font-mono">agree &gt;= N</code>,{" "}
            <code className="font-mono">high == 0</code>, <code className="font-mono">contains &quot;TEXT&quot;</code>.{" "}
            <a href={`${blob("README.md")}#pipelines`} target="_blank" rel="noopener noreferrer" className="link-sweep text-fg">
              Pipeline reference →
            </a>
          </p>
        </Reveal>
      </div>
    </section>
  );
}
