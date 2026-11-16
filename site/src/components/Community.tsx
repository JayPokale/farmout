import { getRepoStats } from "@/lib/github";
import { AUTHOR_URL, COFFEE_URL, INSTALL, REPO_URL } from "@/lib/site";
import Reveal from "./Reveal";
import CopyCmd from "./CopyCmd";
import { GitHubIcon } from "./icons";

export default async function Community() {
  const stats = await getRepoStats();

  return (
    <section className="px-5 pb-28">
      <Reveal>
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-2xl border border-line bg-surface px-6 py-20 text-center">
          <div aria-hidden className="dot-field pointer-events-none absolute inset-0" />
          <div aria-hidden className="aurora !bottom-[-40%] !h-[90%] opacity-40" />
          <div className="relative">
            <h2 className="display mx-auto max-w-4xl text-balance text-3xl font-bold leading-[1.05] sm:text-5xl">
              Stop paying for idle subscriptions. <span className="text-lanes">Put them to work.</span>
            </h2>
            <p className="mx-auto mt-5 max-w-md text-dim">
              MIT licensed, built in the open, no dependencies. Land a PR, or file an issue good enough to fix
              itself, and you&apos;re credited in the repo.
            </p>
            <div className="mx-auto mt-8 max-w-xl">
              <CopyCmd cmd={INSTALL} label="pipx install" />
            </div>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-md bg-brand px-5 py-2.5 text-sm font-medium text-on-brand transition-colors hover:bg-brand-hover"
              >
                <GitHubIcon size={15} />
                Star on GitHub{stats.stars > 0 ? ` · ${stats.stars.toLocaleString()}` : ""}
              </a>
              <a
                href={AUTHOR_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-md border border-line bg-bg/60 px-5 py-2.5 text-sm font-medium transition-colors hover:border-fg/40"
              >
                <GitHubIcon size={15} />
                Follow @JayPokale
              </a>
              <a
                href={COFFEE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-md bg-[#FFDD00] px-5 py-2.5 text-sm font-semibold text-black transition-transform hover:-translate-y-0.5"
              >
                <span aria-hidden>☕</span>
                Buy me a coffee
              </a>
            </div>
            <p className="mt-6 text-xs text-dim">
              If farmout saves you quota, a star costs none. Its own code is reviewed by the four models it drives.
            </p>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
