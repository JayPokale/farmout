"use client";

import { useState } from "react";

// A shell command with a copy button. Long commands scroll sideways rather than
// break mid-word; lines starting with # are comments and are left out of the copy.
export default function CopyCmd({ cmd, label }: { cmd: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const lines = cmd.split("\n");
  return (
    <div className="flex w-full items-start gap-3 rounded-lg border border-line bg-surface/90 py-3 pl-4 pr-3 text-left backdrop-blur transition-colors hover:border-fg/40">
      <pre className="pane-scroll min-w-0 flex-1 overflow-x-auto font-mono text-[12.5px] leading-relaxed">
        {lines.map((l, i) =>
          l.startsWith("#") ? (
            <span key={i} className="block text-dim">{l}</span>
          ) : (
            <span key={i} className="block">
              <span className="select-none text-dim">$ </span>
              {l}
            </span>
          ),
        )}
      </pre>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard.writeText(lines.filter((l) => !l.startsWith("#")).join("\n"));
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        }}
        aria-label={copied ? "Copied" : `Copy the ${label} command`}
        className="shrink-0 rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-dim transition-colors hover:border-fg/40 hover:text-fg"
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
