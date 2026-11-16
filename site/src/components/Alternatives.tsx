import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// The README's Alternatives table: good tools exist, and picking the right one
// matters more than picking this one.
const ROWS = [
  { name: "Codex plugin for Claude Code", href: "https://github.com/openai/codex-plugin-cc", when: "you only need Codex, and want OpenAI's official rescue, session-transfer and background-job commands" },
  { name: "Claude Octopus", href: "https://github.com/nyldn/claude-octopus", when: "you want structured multi-model councils, many providers and workflow gates, and don't mind a large plugin" },
  { name: "PAL MCP Server (formerly Zen)", href: "https://github.com/BeehiveInnovations/pal-mcp-server", when: "you want many workflow tools (consensus, code review, planning) and API-key models, plus its clink CLI bridge" },
  { name: "claude-squad, Conductor, vibe-kanban", href: "https://github.com/smtg-ai/claude-squad", when: "you want a TUI, desktop app or board to run several interactive agents side by side" },
  { name: "claude-code-router", href: "https://github.com/musistudio/claude-code-router", when: "you want to swap the model behind Claude Code rather than use other CLIs" },
];

export default function Alternatives() {
  return (
    <section id="compare" className="mx-auto max-w-6xl px-5 py-28">
      <SectionHead eyebrow="Alternatives" title="Pick the right tool">
        Good tools exist in this space. Choose by what you need.
      </SectionHead>
      <Reveal delay={0.08}>
        <div className="pane-scroll mt-10 overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-line text-xs uppercase tracking-wide text-dim">
              <tr>
                <th className="px-5 py-3 font-medium">project</th>
                <th className="px-5 py-3 font-medium">choose it when</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {ROWS.map((r) => (
                <tr key={r.name} className="align-top transition-colors hover:bg-raised/60">
                  <td className="px-5 py-3.5 font-medium">
                    <a href={r.href} target="_blank" rel="noopener noreferrer" className="link-sweep">
                      {r.name}
                    </a>
                  </td>
                  <td className="px-5 py-3.5 text-dim">{r.when}</td>
                </tr>
              ))}
              <tr className="align-top bg-raised/50">
                <td className="px-5 py-4 font-semibold">
                  <span className="text-lanes">farmout</span>
                </td>
                <td className="px-5 py-4 text-fg">
                  you want the subscriptions you already have working together, read-only by default, from a
                  terminal or an MCP client, with sessions, cross-model review, races, pipelines, skills, hooks and a
                  hash-pinned store, in standard-library Python you can audit
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Reveal>
    </section>
  );
}
