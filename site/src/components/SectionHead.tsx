import Reveal from "./Reveal";
import { LaneMark } from "./icons";

// Shared section header: lane-marked eyebrow, heading, lede.
export default function SectionHead({
  eyebrow,
  title,
  children,
  center = false,
}: {
  eyebrow: string;
  title: React.ReactNode;
  children?: React.ReactNode;
  center?: boolean;
}) {
  return (
    <Reveal>
      <div className={center ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}>
        <p className={`mono-label flex items-center gap-2.5 text-dim ${center ? "justify-center" : ""}`}>
          <LaneMark />
          {eyebrow}
        </p>
        <h2 className="display mt-4 text-3xl font-bold leading-[1.05] sm:text-5xl">{title}</h2>
        {children && <p className="mt-5 leading-relaxed text-dim">{children}</p>}
      </div>
    </Reveal>
  );
}
