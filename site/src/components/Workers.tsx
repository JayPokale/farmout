import { WORKERS } from "@/data/workers";
import { blob } from "@/lib/site";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

export default function Workers() {
  return (
    <section id="workers" className="border-y border-line bg-surface py-28">
      <div className="mx-auto max-w-6xl px-5">
        <SectionHead eyebrow="Workers" title="Your subscriptions, one interface">
          Each worker is the vendor&apos;s own CLI in its documented non-interactive mode, logged in as
          you. farmout resumes every conversation through that CLI&apos;s own session id, so a model
          really remembers earlier turns instead of rereading a summary.
        </SectionHead>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {WORKERS.map((w, i) => (
            <Reveal key={w.id} delay={0.04 * i}>
              <article className="lift relative flex h-full flex-col overflow-hidden rounded-xl border border-line bg-bg p-5">
                <span aria-hidden className={`absolute inset-x-0 top-0 h-[3px] ${w.bg}`} />
                <div className="flex items-center justify-between gap-3">
                  <p className={`flex items-center gap-2 font-mono text-sm font-semibold ${w.text}`}>
                    <span aria-hidden className={`h-2 w-2 rounded-full ${w.bg}`} />
                    {w.id}
                  </p>
                  <span className="rounded border border-line px-1.5 py-0.5 font-mono text-[10px] text-dim">{w.cli}</span>
                </div>
                <h3 className="mt-3 text-base font-semibold">{w.product}</h3>
                <p className="text-sm text-dim">{w.account}</p>
                <p className="mt-3 flex-1 text-sm leading-relaxed text-fg/85">{w.good}</p>
                <dl className="mt-5 grid gap-2 font-mono text-[10.5px]">
                  {[
                    ["read-only", w.readOnly],
                    ["to write", w.write],
                    ["resumes", w.resume],
                  ].map(([k, v]) => (
                    <div key={k} className="grid grid-cols-[4.7rem_1fr] gap-2 rounded-md border border-line bg-surface px-2.5 py-1.5">
                      <dt className="uppercase tracking-wider text-dim">{k}</dt>
                      <dd className="break-words">{v}</dd>
                    </div>
                  ))}
                </dl>
              </article>
            </Reveal>
          ))}
          <Reveal delay={0.22}>
            <a
              href={`${blob("CONTRIBUTING.md")}#adding-a-worker`}
              target="_blank"
              rel="noopener noreferrer"
              className="lift flex h-full flex-col justify-center gap-3 rounded-xl border border-dashed border-line p-6"
            >
              <p className="font-mono text-sm text-dim">+ the next one</p>
              <p className="text-sm leading-relaxed text-dim">
                A new worker is one function in <code className="font-mono text-fg">farmout/workers.py</code>{" "}
                and one table entry, plus proof that its read-only mode really can&apos;t write.
              </p>
              <p className="text-sm font-medium">Adding a worker →</p>
            </a>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
