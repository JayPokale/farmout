import { AUTHOR_URL, COFFEE_URL, REPO_URL, blob, tree } from "@/lib/site";
import { Logo } from "./icons";

const COLS = [
  {
    title: "Product",
    links: [
      ["Demo", "#demo"],
      ["Bench", "#bench"],
      ["How it works", "#how"],
      ["Get started", "#get-started"],
      ["Workers", "#workers"],
      ["Features", "#features"],
      ["Pipelines", "#pipelines"],
      ["Benchmarks", "#numbers"],
      ["FAQ", "#faq"],
    ],
  },
  {
    title: "Resources",
    links: [
      ["README", blob("README.md")],
      ["Benchmarks and raw data", tree("bench")],
      ["The store", tree("store")],
      ["Changelog", blob("CHANGELOG.md")],
      ["Security", blob("SECURITY.md")],
    ],
  },
  {
    title: "Community",
    links: [
      ["GitHub", REPO_URL],
      ["Issues", `${REPO_URL}/issues`],
      ["Contributing", blob("CONTRIBUTING.md")],
      ["Follow @JayPokale", AUTHOR_URL],
      ["Buy me a coffee", COFFEE_URL],
    ],
  },
];

export default function Footer() {
  return (
    <footer className="border-t border-line bg-surface">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-14 sm:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-3 max-w-xs text-sm text-dim">
            Put every AI subscription you pay for to work. MIT · Jay Pokale.
          </p>
          <p className="mt-4 max-w-xs font-mono text-[10.5px] leading-relaxed text-faint">
            No trackers, no cookies. Not affiliated with Anthropic, OpenAI, Google or GitHub.
          </p>
        </div>
        {COLS.map((c) => (
          <nav key={c.title} aria-label={c.title}>
            <p className="text-sm font-semibold">{c.title}</p>
            <ul className="mt-3 space-y-2 text-sm text-dim">
              {c.links.map(([label, href]) => (
                <li key={label}>
                  <a
                    href={href}
                    {...(href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                    className="transition-colors hover:text-fg"
                  >
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
    </footer>
  );
}
