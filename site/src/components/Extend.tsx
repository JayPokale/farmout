import { tree } from "@/lib/site";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";
import { Glyph } from "./icons";

const SKILL = `---
name: security-review
description: Security problems only, every worker
mode: review        # ask | review | race
workers: [codex, claude, copilot]
diff: true          # hand over your change
---
Look only for problems an attacker
could trigger: ...
$ARGUMENTS`;

const HOOK = `[[hooks]]
event = "TaskEnd"
command = "notify-send farmout done"
matcher = "codex|claude"   # optional regex
timeout = 30`;

const STORE = [
  { name: "security-review", kind: "skill", body: "every worker hunts security bugs only, merged with agreement counts" },
  { name: "test-gaps", kind: "skill", body: "behaviours in your change no test covers, and the test to add" },
  { name: "notify", kind: "hook", body: "a desktop notification, or a phone push through ntfy, when a long turn ends" },
  { name: "guard-secrets", kind: "hook", body: "refuses a task that would hand a worker .env files or keys" },
  { name: "prune", kind: "tool", body: "deletes sessions idle for N days, with their worktrees" },
];

function Code({ title, children }: { title: string; children: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-term-line bg-term">
      <p className="border-b border-term-line px-3 py-2 font-mono text-[10.5px] text-term-dim">{title}</p>
      <pre className="pane-scroll overflow-x-auto p-3 font-mono text-[10.5px] leading-[1.7] text-term-fg" style={{ fontStretch: "87.5%" }}>
        {children.split("\n").map((l, i) => {
          const c = l.indexOf("#");
          return (
            <span key={i} className="block">
              {c >= 0 ? (
                <>
                  {l.slice(0, c)}
                  <span className="text-term-dim">{l.slice(c)}</span>
                </>
              ) : (
                l || " "
              )}
            </span>
          );
        })}
      </pre>
    </div>
  );
}

export default function Extend() {
  return (
    <section className="mx-auto max-w-6xl px-5 py-28">
      <SectionHead eyebrow="Skills, hooks, the store" title="Extend it the way you extend your agent">
        Skills in the SKILL.md format Claude Code, Codex and Copilot already share. Hooks on farmout&apos;s own
        events. And a store where every file is pinned by its SHA-256, because hooks and tools are code.
      </SectionHead>

      <div className="mt-12 grid gap-4 lg:grid-cols-3">
        <Reveal>
          <article className="flex h-full flex-col gap-4 rounded-xl border border-line bg-surface p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-raised"><Glyph name="skill" /></span>
              <h3 className="text-lg font-semibold">Skills</h3>
            </div>
            <p className="text-sm leading-relaxed text-dim">
              Run one on any worker with <code className="font-mono text-fg">farmout security-review</code>. Install
              from a folder, a git repo or a URL; a fetched skill that contains code or symlinks is refused.
            </p>
            <Code title="skills/security-review/SKILL.md">{SKILL}</Code>
          </article>
        </Reveal>

        <Reveal delay={0.06}>
          <article className="flex h-full flex-col gap-4 rounded-xl border border-line bg-surface p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-raised"><Glyph name="hook" /></span>
              <h3 className="text-lg font-semibold">Hooks</h3>
            </div>
            <p className="text-sm leading-relaxed text-dim">
              Six events, from SessionStart to PipelineEnd. A TaskStart hook can rewrite a brief or block it
              before it reaches a worker. A project&apos;s own hooks run only after{" "}
              <code className="font-mono text-fg">farmout trust</code>, pinned to the file&apos;s hash.
            </p>
            <Code title="~/.config/farmout/settings.toml">{HOOK}</Code>
            <p className="font-mono text-[10.5px] leading-relaxed text-dim">
              SessionStart · MessageReceived · TaskStart · TaskEnd · StageEnd · PipelineEnd
            </p>
          </article>
        </Reveal>

        <Reveal delay={0.12}>
          <article className="flex h-full flex-col gap-4 rounded-xl border border-line bg-surface p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-raised"><Glyph name="store" /></span>
              <h3 className="text-lg font-semibold">The store</h3>
            </div>
            <p className="text-sm leading-relaxed text-dim">
              <code className="font-mono text-fg">farmout store show</code> prints an entry&apos;s files so you can read
              them first; <code className="font-mono text-fg">store add</code> checks each one against the index and
              installs nothing if a single hash differs.
            </p>
            <ul className="divide-y divide-line rounded-lg border border-line bg-bg">
              {STORE.map((s) => (
                <li key={s.name} className="px-3 py-2.5">
                  <p className="flex items-center justify-between gap-2 font-mono text-[11.5px]">
                    <span className="font-semibold">{s.name}</span>
                    <span className="rounded bg-raised px-1.5 py-px text-[9.5px] uppercase tracking-wider text-dim">{s.kind}</span>
                  </p>
                  <p className="mt-0.5 text-xs leading-snug text-dim">{s.body}</p>
                </li>
              ))}
            </ul>
            <a href={tree("store")} target="_blank" rel="noopener noreferrer" className="link-sweep w-fit text-sm font-medium">
              Browse the store →
            </a>
          </article>
        </Reveal>
      </div>
    </section>
  );
}
