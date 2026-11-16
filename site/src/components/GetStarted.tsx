"use client";

import { useState } from "react";
import { INSTALL, REPO, REPO_URL, blob } from "@/lib/site";
import Reveal from "./Reveal";
import CopyCmd from "./CopyCmd";
import { LaneMark } from "./icons";

// Every install path the README documents. If a command changes there, it
// changes here.
const INSTALLS = [
  { id: "pipx", label: "pipx", cmd: INSTALL, note: "Adds the farmout command. Python 3.9+ and nothing else." },
  {
    id: "clone",
    label: "from a checkout",
    cmd: `git clone ${REPO_URL} ~/.local/share/farmout\npipx install ~/.local/share/farmout`,
    note: "The same CLI, from a copy you can read first. python3 server.py <command> works with no install at all.",
  },
  {
    id: "plugin",
    label: "Claude Code",
    cmd: `claude plugin install farmout --marketplace ${REPO}`,
    note: "Adds /farmout:review, /farmout:race and /farmout:ask, the MCP tools and the read guard.",
  },
  {
    id: "mcp",
    label: "any MCP client",
    cmd: "claude mcp add -s user farmout -- farmout mcp\ncodex mcp add farmout -- farmout mcp\ncopilot mcp add farmout -- farmout mcp",
    note: "Any MCP client can be the head and hand subtasks to the others.",
  },
];

// Real `farmout doctor` output from the maintainer's machine, with the install
// paths and the first (version) line left out.
const DOCTOR = [
  ["ready", "antigravity", "agy 1.4.6", "text-term-agy"],
  ["ready", "codex", "codex-cli 0.163.0", "text-term-codex"],
  ["ready", "claude", "2.1.323 (Claude Code)", "text-term-claude"],
  ["ready", "copilot", "GitHub Copilot CLI 1.0.112.", "text-term-copilot"],
  ["missing", "api", "set FARMOUT_API_BASE and FARMOUT_API_MODEL (and FARMOUT_API_KEY if needed)", "text-term-dim"],
] as const;

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="relative grid gap-4 pb-12 pl-14 last:pb-0">
      <span
        aria-hidden
        className="absolute left-0 top-0 grid h-9 w-9 place-items-center rounded-full border border-fg/25 bg-raised font-mono text-sm font-semibold"
      >
        {n}
      </span>
      <h3 className="pt-1 text-lg font-semibold tracking-tight">{title}</h3>
      {children}
    </li>
  );
}

export default function GetStarted() {
  const [active, setActive] = useState(INSTALLS[0].id);
  const current = INSTALLS.find((i) => i.id === active)!;

  return (
    <section id="get-started" className="mx-auto max-w-6xl px-5 py-28">
      <div className="grid gap-14 lg:grid-cols-[1fr_1.4fr]">
        <Reveal>
          <p className="mono-label flex items-center gap-2.5 text-dim">
            <LaneMark />
            Get started
          </p>
          <h2 className="display mt-4 text-3xl font-bold leading-[1.05] sm:text-5xl">Running in under a minute</h2>
          <p className="mt-5 max-w-md leading-relaxed text-dim">
            farmout drives the official CLIs you already use, logged in as you. No API keys, no proxy,
            no extra bill: each task spends the quota of the subscription that runs it.
          </p>
          <div className="mt-6 max-w-md rounded-lg border border-line bg-surface p-4 text-sm text-dim">
            <p className="font-medium text-fg">You need</p>
            <p className="mt-1.5 leading-relaxed">
              Python 3.9+, git, Linux (tested) or macOS, and at least one of: Antigravity CLI, Codex CLI,
              GitHub Copilot CLI, a second Claude Code account, or an OpenAI-compatible endpoint.
            </p>
          </div>
          <a
            href={`${blob("README.md")}#install`}
            target="_blank"
            rel="noopener noreferrer"
            className="link-sweep mt-6 inline-block text-sm font-medium"
          >
            Full install guide →
          </a>
        </Reveal>

        <Reveal delay={0.08}>
          <ol className="relative before:absolute before:bottom-10 before:left-[17px] before:top-10 before:w-px before:bg-line">
            <Step n={1} title="Install">
              <div className="flex w-fit flex-wrap gap-1 rounded-lg border border-line bg-surface p-1" role="tablist" aria-label="Installation method">
                {INSTALLS.map((i) => (
                  <button
                    key={i.id}
                    type="button"
                    role="tab"
                    aria-selected={i.id === active}
                    onClick={() => setActive(i.id)}
                    className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                      i.id === active ? "bg-raised text-fg shadow-sm" : "text-dim hover:text-fg"
                    }`}
                  >
                    {i.label}
                  </button>
                ))}
              </div>
              <CopyCmd cmd={current.cmd} label={current.label} />
              <p className="-mt-2 text-xs text-dim">{current.note}</p>
            </Step>

            <Step n={2} title="Log in to the CLIs you have, once">
              <p className="text-sm leading-relaxed text-dim">
                Whatever you already pay for. A second Claude account logs in to its own config folder; a
                local model is two environment variables. Then ask farmout what it found:
              </p>
              <div className="grid gap-2 font-mono text-[11.5px]">
                <CopyCmd
                  cmd={"# a second Claude account\nCLAUDE_CONFIG_DIR=~/.claude-2 claude\nexport FARMOUT_CLAUDE_DIR=~/.claude-2"}
                  label="second Claude account"
                />
                <CopyCmd
                  cmd={"# any OpenAI-compatible model\nexport FARMOUT_API_BASE=http://localhost:11434/v1 FARMOUT_API_MODEL=qwen3:32b"}
                  label="local model"
                />
              </div>
              <div className="overflow-hidden rounded-lg border border-term-line bg-term font-mono text-[11px] leading-[1.9] text-term-fg">
                <p className="border-b border-term-line px-4 py-2 text-term-dim">
                  <span className="select-none">$ </span>
                  <span className="text-term-fg">farmout doctor</span>
                </p>
                <div className="pane-scroll overflow-x-auto px-4 py-3">
                  {DOCTOR.map(([state, w, v, cls]) => (
                    <p key={w} className="whitespace-pre">
                      <span className={state === "ready" ? "text-term-pass" : "text-term-dim"}>{state.padEnd(9)}</span>
                      <span className={cls}>{w.padEnd(13)}</span>
                      <span className="text-term-dim">{v}</span>
                    </p>
                  ))}
                  <p className="mt-1 whitespace-pre text-term-dim">
                    Logins are not checked. `farmout doctor --live` sends each ready worker one tiny prompt.
                  </p>
                </div>
              </div>
            </Step>

            <Step n={3} title="Hand it work">
              <div className="grid gap-2 font-mono text-xs">
                {[
                  ["every model reviews your change", "farmout review"],
                  ["one question, every answer", 'farmout ask "Is this migration safe?"'],
                  ["your tests pick the winner", 'farmout race "Fix the flaky test" --test "pytest -x"'],
                  ["a conversation that remembers", 'farmout run codex "Add tests for parse_date()" -n dates'],
                ].map(([k, v]) => (
                  <div
                    key={k}
                    className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-md border border-line bg-surface px-3 py-2"
                  >
                    <p className="text-[10px] uppercase tracking-wider text-dim">{k}</p>
                    <p className="[overflow-wrap:anywhere]">{v}</p>
                  </div>
                ))}
              </div>
              <p className="text-xs text-dim">
                Every command takes <code className="font-mono">--json</code> for scripts. Workers are
                read-only unless you pass <code className="font-mono">--write</code> or{" "}
                <code className="font-mono">--isolate</code>.
              </p>
            </Step>
          </ol>
        </Reveal>
      </div>
    </section>
  );
}
