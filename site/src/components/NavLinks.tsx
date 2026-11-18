"use client";

import { useEffect, useState } from "react";

// The nav says where you are, not only where you can go: the link for the
// section in view is marked, and a reading-progress rail in the four lane
// colours runs under the bar.
const SECTIONS = [
  { id: "demo", label: "Demo" },
  { id: "graph", label: "UI" },
  { id: "bench", label: "Bench" },
  { id: "how", label: "How it works" },
  { id: "get-started", label: "Get started" },
  { id: "numbers", label: "Benchmarks" },
  { id: "faq", label: "FAQ" },
];

export default function NavLinks() {
  const [active, setActive] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      setProgress(max > 0 ? (h.scrollTop / max) * 100 : 0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    // A section is current once its top passes the header, not when it first peeks in.
    const obs = new IntersectionObserver(
      (entries) => {
        const seen = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (seen) setActive(seen.target.id);
      },
      { rootMargin: "-20% 0px -70% 0px" },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) obs.observe(el);
    });

    return () => {
      window.removeEventListener("scroll", onScroll);
      obs.disconnect();
    };
  }, []);

  return (
    <>
      {SECTIONS.map((s) => (
        <a
          key={s.id}
          href={`#${s.id}`}
          aria-current={active === s.id ? "location" : undefined}
          className={`relative hidden transition-colors lg:block ${active === s.id ? "text-fg" : "hover:text-fg"}`}
        >
          {s.label}
          <span
            aria-hidden
            className={`bg-lanes absolute -bottom-1 left-0 h-px transition-all duration-300 ${
              active === s.id ? "w-full opacity-100" : "w-0 opacity-0"
            }`}
          />
        </a>
      ))}
      {/* full-width gradient, clipped: each lane colour keeps its place as the rail grows */}
      <span
        aria-hidden
        className="bg-lanes pointer-events-none absolute inset-x-0 bottom-0 h-px transition-[clip-path] duration-150"
        style={{ clipPath: `inset(0 ${100 - progress}% 0 0)` }}
      />
    </>
  );
}
