import Counter from "./Counter";

// Each figure carries where it comes from, right under it: the numbers are
// meant to survive being checked against the committed benchmark files.
const STATS = [
  {
    value: 32,
    suffix: "/32",
    k: "bugs flagged by at least three of the four models; 27 by all four",
    src: "23 planted + 9 real BugsInPy regressions, 2026-11-17 · bench/README.md",
  },
  {
    value: 59,
    prefix: "−",
    suffix: "%",
    k: "of Claude's own tokens when Antigravity reads the papers instead (−74% cost)",
    src: "three arXiv papers, 43 pages, same answers · bench/results/2026-11-17/head_tokens.md",
  },
  {
    value: 59,
    suffix: "s",
    k: "for four models to review a change, merged and checked against the code",
    src: "files_api case: all 5 planted bugs found, 4 of them by 4/4 · demo-review.md",
  },
  {
    // Hubot Sans draws its zero with a bar, which reads as a theta at this size
    text: "Zero",
    k: "dependencies: standard-library Python you can read before you hand it your logins",
    src: "pyproject.toml · dependencies = []",
  },
];

export default function Stats() {
  return (
    <section className="border-y border-line bg-surface">
      <dl className="mx-auto grid max-w-6xl grid-cols-2 lg:grid-cols-4">
        {STATS.map((s) => (
          <div
            key={s.k}
            className="border-line px-5 py-9 odd:border-r sm:px-6 sm:py-10 lg:border-r lg:last:border-r-0 [&:nth-child(-n+2)]:border-b lg:[&:nth-child(-n+2)]:border-b-0"
          >
            <dt className="sr-only">{s.k}</dt>
            <dd>
              <p className="display text-lanes w-fit text-4xl font-bold">
                {"text" in s ? s.text : <Counter value={s.value} prefix={s.prefix} suffix={s.suffix} />}
              </p>
              <p className="mt-2 text-sm text-fg">{s.k}</p>
              <p className="mt-2 font-mono text-[10.5px] leading-snug text-dim">{s.src}</p>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
