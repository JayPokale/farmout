"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useInView } from "motion/react";
import { RUNS, type Run } from "@/data/runs";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// The one dark element that is the product itself: a replay of real runs. The
// command types, the wait is shown as a sped-up clock (the CLI prints nothing
// while the workers think), then the output lands line by line, unedited.

const WORKER_CLS: Record<string, string> = {
  antigravity: "text-term-agy",
  codex: "text-term-codex",
  claude: "text-term-claude",
  copilot: "text-term-copilot",
};

function paintWorkers(s: string) {
  return s.split(/(antigravity|codex|claude|copilot)/g).map((part, i) =>
    WORKER_CLS[part] ? (
      <span key={i} className={WORKER_CLS[part]}>{part}</span>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

const AGREE: Record<string, string> = { "4/4": "text-term-pass font-bold", "3/4": "text-term-pass", "2/4": "text-term-fg", "1/4": "text-term-dim" };

// Colour one line of output: agreement and severity on review rows, pass/FAIL
// on race rows, worker names everywhere. Everything else prints as is.
function Line({ text, first }: { text: string; first: boolean }) {
  const row = text.match(/^(\d\/\d)(\s+)(high|medium|low)(.*)$/);
  if (row) {
    const [, agree, gap, sev, rest] = row;
    const sevCls = sev === "high" ? "font-bold text-term-fg" : sev === "medium" ? "text-term-fg" : "text-term-dim";
    return (
      <>
        <span className={AGREE[agree] ?? ""}>{agree}</span>
        {gap}
        <span className={sevCls}>{sev}</span>
        {paintWorkers(rest)}
      </>
    );
  }
  if (/^(agree|worker)\s/.test(text)) return <span className="text-term-dim">{text}</span>;
  if (first || /^(Winner|Follow up|No winner)/.test(text)) {
    return <span className="text-term-fg">{paintWorkers(text)}</span>;
  }
  return (
    <>
      {text.split(/\b(pass|FAIL|FAILED)\b/g).map((part, i) =>
        part === "pass" ? (
          <span key={i} className="text-term-pass">{part}</span>
        ) : part === "FAIL" || part === "FAILED" ? (
          <span key={i} className="text-[#ff6b6b]">{part}</span>
        ) : (
          <Fragment key={i}>{paintWorkers(part)}</Fragment>
        ),
      )}
    </>
  );
}

type Phase = "idle" | "typing" | "waiting" | "output";

function useReplay(run: Run, start: boolean) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [typed, setTyped] = useState(0);
  const [clock, setClock] = useState(0);
  const [shown, setShown] = useState(0);
  const lines = run.out.split("\n");

  useEffect(() => {
    if (!start) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setTyped(run.cmd.length);
      setClock(run.secs);
      setShown(lines.length);
      setPhase("output");
      return;
    }
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const later = (ms: number, fn: () => void) => timers.push(setTimeout(() => !cancelled && fn(), ms));

    setPhase("typing");
    setTyped(0);
    setClock(0);
    setShown(0);
    const typeMs = 28;
    for (let i = 1; i <= run.cmd.length; i++) later(250 + i * typeMs, () => setTyped(i));
    const t0 = 250 + run.cmd.length * typeMs + 250;
    later(t0, () => setPhase("waiting"));
    const ticks = 24;
    for (let i = 1; i <= ticks; i++) later(t0 + i * 70, () => setClock(Math.round((run.secs * i) / ticks)));
    const t1 = t0 + ticks * 70 + 300;
    later(t1, () => setPhase("output"));
    lines.forEach((_, i) => later(t1 + i * 110, () => setShown(i + 1)));

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
    // lines is derived from run; the replay restarts only for a new run or start
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, start]);

  return { phase, typed, clock, shown, lines };
}

export default function Terminal() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-120px" });
  const [runId, setRunId] = useState(RUNS[0].id);
  const run = RUNS.find((r) => r.id === runId)!;
  const { phase, typed, clock, shown, lines } = useReplay(run, inView);

  return (
    <section id="demo" className="mx-auto max-w-6xl px-5 py-28">
      <SectionHead eyebrow="See it work" title="One command. Four subscriptions. One answer.">
        <code className="font-mono text-fg">farmout review</code> hands your uncommitted change to every
        subscription you have, at once and read-only, then merges what they found into one table.{" "}
        <code className="font-mono text-fg">farmout race</code> has each one fix a bug in its own worktree
        and lets your tests pick the winner. Both are the unedited output of real runs; only the wait is
        sped up.
      </SectionHead>

      <Reveal delay={0.1}>
        <div ref={ref} className="mt-10 overflow-hidden rounded-xl border border-term-line bg-term font-mono shadow-[0_40px_120px_-50px_#000]">
          <div className="flex items-center gap-3 border-b border-term-line px-4 py-2.5">
            <span aria-hidden className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-term-claude/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-term-codex/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-term-agy/70" />
            </span>
            {RUNS.length > 1 && (
              <div className="flex gap-1" role="tablist" aria-label="Which run">
                {RUNS.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    role="tab"
                    aria-selected={r.id === runId}
                    onClick={() => setRunId(r.id)}
                    className={`rounded px-2.5 py-0.5 text-[11px] transition-colors ${
                      r.id === runId ? "bg-white/10 text-term-fg" : "text-term-dim hover:text-term-fg"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}
            <span className="ml-auto text-[10.5px] text-term-dim">
              {phase === "waiting" ? (
                <>
                  <span className="spin mr-1.5">◐</span>workers thinking · {clock}s
                </>
              ) : phase === "output" ? (
                `${run.secs}s real time`
              ) : (
                "~/demo"
              )}
            </span>
          </div>
          <div className="pane-scroll h-[22rem] overflow-auto p-4 text-[11px] leading-[1.75] text-term-fg sm:p-5 sm:text-[11.5px]" style={{ fontStretch: "87.5%" }}>
            <p className="whitespace-pre">
              <span className="select-none text-term-dim">$ </span>
              {run.cmd.slice(0, typed)}
              {phase === "typing" && <span className="caret" />}
            </p>
            {phase === "waiting" && <span className="caret" />}
            {phase === "output" &&
              lines.slice(0, shown).map((l, i) => (
                <p key={i} className="whitespace-pre">
                  {l ? <Line text={l} first={i === 0} /> : " "}
                </p>
              ))}
            {phase === "output" && shown >= lines.length && (
              <p className="mt-1 whitespace-pre">
                <span className="select-none text-term-dim">$ </span>
                <span className="caret" />
              </p>
            )}
          </div>
        </div>
      </Reveal>

      <Reveal delay={0.15}>
        <p className="mt-4 max-w-3xl text-xs leading-relaxed text-dim">{run.note}</p>
      </Reveal>
    </section>
  );
}
