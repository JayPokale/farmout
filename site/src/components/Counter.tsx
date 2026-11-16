"use client";

import { animate, useInView } from "motion/react";
import { useEffect, useRef } from "react";

const format = (n: number, prefix: string, suffix: string) =>
  `${prefix}${Math.round(n).toLocaleString("en-US")}${suffix}`;

// Renders the real number first (crawlers and link previews see it), then on
// the client counts up from zero once it scrolls into view.
export default function Counter({ value, prefix = "", suffix = "" }: { value: number; prefix?: string; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });

  useEffect(() => {
    const node = ref.current;
    if (!node || value === 0) return;
    if (!inView) {
      node.textContent = format(0, prefix, suffix);
      return;
    }
    const controls = animate(0, value, {
      duration: 1.4,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => {
        node.textContent = format(v, prefix, suffix);
      },
    });
    return () => controls.stop();
  }, [inView, value, prefix, suffix]);

  return (
    <span ref={ref}>
      {format(value, prefix, suffix)}
    </span>
  );
}
