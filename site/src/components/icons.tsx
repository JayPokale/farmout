// Small stroke icons drawn inline (no icon library), plus the logo marks.

const GLYPHS: Record<string, React.ReactNode> = {
  shield: <path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7zM8.5 12l2.5 2.5 4.5-5" />,
  deps: <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5" />,
  key: <path d="M15 7a4 4 0 1 1-3.9 4.9L4 19v2h3v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7zM16.5 9.5h.01" />,
  branch: <path d="M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9" />,
  plug: <path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5" />,
  open: <path d="M7 11V7a5 5 0 0 1 9.9-1M5 11h14v10H5z" />,
  review: <path d="M4 5h16M4 10h10M4 15h7M15.5 17.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 20l-1.6-1.6" />,
  race: <path d="M4 19h16M6 15l3-8 4 6 3-4 2 6M4 4h3" />,
  session: <path d="M4 5h16v11H9l-5 4zM8 9.5h8M8 12.5h5" />,
  pipe: <path d="M3 12h4M17 12h4M7 8h10v8H7zM10 12h4" />,
  debate: <path d="M3 5h11v8H7l-4 3zM10 16v1h7l4 3V9h-4" />,
  bench: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  pr: <path d="M6 3v12M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 15V8a3 3 0 0 0-3-3h-4M13 2l-2 3 2 3" />,
  limit: <path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 3" />,
  hook: <path d="M12 3v9a4 4 0 1 1-4-4M12 3l3 3M12 3L9 6" />,
  skill: <path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5" />,
  store: <path d="M4 8l2-4h12l2 4M4 8h16v12H4zM9 12h6" />,
  terminal: <path d="M4 5h16v14H4zM7 9l3 3-3 3M12 15h5" />,
  copy: <path d="M9 9h11v11H9zM5 15H4V4h11v1" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
};

export function Glyph({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {GLYPHS[name]}
    </svg>
  );
}

// The logo: one line that fans into four lanes, one per subscription.
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" aria-hidden>
      <rect width="512" height="512" rx="112" fill="#14161f" />
      <g fill="none" strokeWidth="26" strokeLinecap="round">
        <path d="M104 256H196C256 256 262 140 326 140H384" stroke="#f2a65a" />
        <path d="M104 256H196C250 256 262 218 326 218H384" stroke="#5ec4b6" />
        <path d="M104 256H196C250 256 262 294 326 294H384" stroke="#a78bfa" />
        <path d="M104 256H196C256 256 262 372 326 372H384" stroke="#f47c7c" />
      </g>
      <path d="M104 256H204" stroke="#ece8df" strokeWidth="26" strokeLinecap="round" />
      <circle cx="104" cy="256" r="40" fill="#ece8df" />
      <circle cx="404" cy="140" r="30" fill="#f2a65a" />
      <circle cx="404" cy="218" r="30" fill="#5ec4b6" />
      <circle cx="404" cy="294" r="30" fill="#a78bfa" />
      <circle cx="404" cy="372" r="30" fill="#f47c7c" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <Mark size={28} />
      <span className="display text-[1.05rem] font-bold tracking-tight">farmout</span>
    </span>
  );
}

// Four short dashes in the lane colours: the eyebrow marker.
export function LaneMark() {
  return (
    <span aria-hidden className="inline-flex gap-[3px]">
      <span className="h-[3px] w-2.5 rounded-full bg-codex" />
      <span className="h-[3px] w-2.5 rounded-full bg-agy" />
      <span className="h-[3px] w-2.5 rounded-full bg-copilot" />
      <span className="h-[3px] w-2.5 rounded-full bg-claude" />
    </span>
  );
}

export function GitHubIcon({ size = 15 }: { size?: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}
