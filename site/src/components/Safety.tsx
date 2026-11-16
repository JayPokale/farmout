import { blob } from "@/lib/site";
import Reveal from "./Reveal";
import SectionHead from "./SectionHead";

// The README's Safety and Settings sections, including the parts that are
// limits rather than guarantees.
const POINTS = [
  {
    t: "Read-only unless you say otherwise",
    b: "Every worker starts in its CLI's read-only mode: plan mode for agy, -s read-only for codex, an allowlist of reading tools for claude and copilot. Writing needs --write, --isolate or write: true.",
  },
  {
    t: "Worktrees, then patches",
    b: "An isolated worker edits its own git worktree, and in-repo paths in its brief point there. Your tree changes only when you apply a patch. It's not a filesystem sandbox: a tracked symlink out of the repo can be written through.",
  },
  {
    t: "Settings only ever narrow",
    b: "Your settings and a repo's both apply, and neither can loosen the other: worker lists intersect, write and web are off if either says so, the shorter timeout wins. Keep work code off personal accounts with one line.",
  },
  {
    t: "Code runs only if you vouch for it",
    b: "Hooks and tools come from you or from the hash-pinned store. A project's own hooks run after farmout trust, pinned to the file's SHA-256, and stop if it changes.",
  },
  {
    t: "Credentials can stay home",
    b: "redact = true strips API keys, tokens, private keys, JWTs and password=… values from every brief and from piped input before anything leaves your machine.",
  },
  {
    t: "Prompt injection is real",
    b: "Workers read whatever you hand them, and a hostile document can try to instruct them. Read-only mode limits the damage; it can't make injection impossible. web = false closes the web tools.",
  },
];

const SETTINGS = `# <repo>/.farmout/settings.toml
workers = ["codex", "claude"]   # who may see this repo
write = false                   # no write, no isolate here
web = false                     # no web tools
fallback = ["codex", "copilot"] # who takes over at a limit
redact = true                   # strip credentials first`;

export default function Safety() {
  return (
    <section className="mx-auto max-w-6xl px-5 py-28">
      <div className="grid gap-12 lg:grid-cols-[1fr_1.15fr]">
        <div>
          <SectionHead eyebrow="Safety" title="Your accounts, your rules">
            farmout runs each vendor&apos;s official CLI in its documented non-interactive mode, logged in as
            you. It doesn&apos;t extract tokens, scrape web apps or proxy APIs, and it&apos;s small enough to read
            before you trust it with your logins.
          </SectionHead>
          <Reveal delay={0.08}>
            <div className="mt-8 overflow-hidden rounded-xl border border-term-line bg-term">
              <pre className="pane-scroll overflow-x-auto p-4 font-mono text-[11px] leading-[1.8] text-term-fg" style={{ fontStretch: "87.5%" }}>
                {SETTINGS.split("\n").map((l, i) => {
                  const c = l.indexOf("#", 1);
                  return (
                    <span key={i} className="block">
                      {l.startsWith("#") ? (
                        <span className="text-term-dim">{l}</span>
                      ) : (
                        <>
                          <span className="text-term-copilot">{l.slice(0, l.indexOf("="))}</span>
                          <span className="text-term-fg">{l.slice(l.indexOf("="), c)}</span>
                          <span className="text-term-dim">{l.slice(c)}</span>
                        </>
                      )}
                    </span>
                  );
                })}
              </pre>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-dim">
              Data still leaves your machine: briefs and files go to each worker&apos;s provider. You remain
              responsible for each provider&apos;s terms; don&apos;t use it to share accounts or resell access.{" "}
              <a href={`${blob("README.md")}#safety`} target="_blank" rel="noopener noreferrer" className="link-sweep text-fg">
                Every flag, per worker →
              </a>
            </p>
          </Reveal>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {POINTS.map((p, i) => (
            <Reveal key={p.t} delay={0.04 * i}>
              <div className="lift h-full rounded-xl border border-line bg-surface p-5">
                <h3 className="text-[15px] font-semibold leading-snug">{p.t}</h3>
                <p className="mt-2 text-sm leading-relaxed text-dim">{p.b}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
