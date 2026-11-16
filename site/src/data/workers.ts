// The five workers, as the README's Install and Safety tables describe them.
// If a flag changes in farmout/workers.py, it changes here.
//
// Class names are spelled out per worker because Tailwind only generates the
// classes it finds written in full.
export const WORKERS = [
  {
    id: "antigravity",
    product: "Antigravity CLI",
    cli: "agy",
    account: "your Google account",
    good: "Long PDFs, papers, many-file reading, web research. Gemini, plus the other models agy lists.",
    readOnly: "agy -p … --mode plan",
    write: "--mode accept-edits --sandbox",
    resume: "agy --conversation <id>",
    color: "var(--color-agy)",
    text: "text-agy",
    bg: "bg-agy",
    soft: "bg-agy/10",
    border: "border-agy/40",
  },
  {
    id: "codex",
    product: "Codex CLI",
    cli: "codex",
    account: "your ChatGPT account",
    good: "Well-specified coding: implement a function, write and fix tests, focused refactors, reviews.",
    readOnly: "codex exec -s read-only",
    write: "-s workspace-write",
    resume: "codex exec resume <id>",
    color: "var(--color-codex)",
    text: "text-codex",
    bg: "bg-codex",
    soft: "bg-codex/10",
    border: "border-codex/40",
  },
  {
    id: "claude",
    product: "Claude Code",
    cli: "claude",
    account: "a second Claude account",
    good: "General work on separate quota, so your main session keeps its own.",
    readOnly: "claude -p --tools Read,Grep,Glob,WebSearch",
    write: "--permission-mode acceptEdits",
    resume: "claude --resume <id>",
    color: "var(--color-claude)",
    text: "text-claude",
    bg: "bg-claude",
    soft: "bg-claude/10",
    border: "border-claude/40",
  },
  {
    id: "copilot",
    product: "GitHub Copilot CLI",
    cli: "copilot",
    account: "your Copilot plan",
    good: "Code review, focused coding questions, second opinions, with models from several vendors.",
    readOnly: "copilot -p … --available-tools view rg glob web_fetch",
    write: "adds apply_patch edit create; never a shell",
    resume: "copilot --session-id <id>",
    color: "var(--color-copilot)",
    text: "text-copilot",
    bg: "bg-copilot",
    soft: "bg-copilot/10",
    border: "border-copilot/40",
  },
  {
    id: "api",
    product: "OpenAI-compatible API",
    cli: "api",
    account: "Ollama, vLLM, OpenRouter, …",
    good: "Bulk or simple work on any open or local model. No tools: file text goes into the prompt.",
    readOnly: "no tools at all",
    write: "n/a",
    resume: "farmout keeps the history",
    color: "var(--color-api)",
    text: "text-api",
    bg: "bg-api",
    soft: "bg-api/10",
    border: "border-api/40",
  },
] as const;

export type Worker = (typeof WORKERS)[number];
export type WorkerId = Worker["id"];

export const worker = (id: string) => WORKERS.find((w) => w.id === id);
