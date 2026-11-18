import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// farmout ui, recorded on real runs (18 Nov 2026): branch off turn 1 of a conversation onto Copilot, then read the
// three answers. Only Copilot's wait is sped up, with a badge giving its real length.
const KEYS = [
  ["farmout ui", "every conversation, full-screen in your terminal"],
  ["b", "branch off the turn you're on, onto any worker or model"],
  ["c", "continue from the newest turn"],
  ["e", "ask that turn again, a different way"],
  ["y", "copy an answer; d shows an isolated session's patch"],
  ["farmout tree", "the same graph, printed"],
];

export default function Graph() {
  return (
    <section id="graph" className="mx-auto max-w-6xl px-5 py-28">
      <SectionHead eyebrow="farmout ui" title="Every conversation is a graph.">
        Branch off any turn onto another model and keep both answers. <code className="font-mono text-fg">farmout
        ui</code> draws every conversation in your terminal, a lane per branch coloured by its worker, the way{" "}
        <code className="font-mono text-fg">git log --graph</code> draws branches, with the turn you&apos;re on beside
        it to read, continue or branch off. A worker at its limit or logged out shows at the bottom.
      </SectionHead>

      <Reveal delay={0.1}>
        <div className="mt-10 overflow-hidden rounded-xl border border-term-line bg-term shadow-[0_40px_120px_-50px_#000]">
          <video
            className="block h-auto w-full"
            src="/graph-demo.mp4"
            poster="/graph-demo.jpg"
            width={1280}
            height={886}
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            aria-label="A real run of farmout ui: a conversation that began on Claude and went on on Codex; the user branches off turn 1 onto Copilot, and its answer lands in a lane of its own beside the others"
          />
        </div>
      </Reveal>

      <Reveal delay={0.15}>
        <dl className="mt-8 grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {KEYS.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-3 text-sm">
              <dt className="shrink-0 font-mono text-[12.5px] font-semibold text-fg">{k}</dt>
              <dd className="text-dim">{v}</dd>
            </div>
          ))}
        </dl>
      </Reveal>
    </section>
  );
}
