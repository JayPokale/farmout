import type { Metadata, Viewport } from "next";
import { Hubot_Sans, Mona_Sans, Martian_Mono } from "next/font/google";
import { AUTHOR_URL, REPO_URL, SITE_URL } from "@/lib/site";
import "./globals.css";

// Self-hosted at build time: visitors' browsers never call Google. Hubot Sans
// for headlines (opened up on its width axis), its sibling Mona Sans for text,
// Martian Mono for everything a terminal would print.
const hubot = Hubot_Sans({ subsets: ["latin"], axes: ["wdth"], variable: "--font-hubot" });
const mona = Mona_Sans({ subsets: ["latin"], variable: "--font-mona" });
const martian = Martian_Mono({ subsets: ["latin"], axes: ["wdth"], variable: "--font-martian" });

const DESCRIPTION =
  "farmout puts every AI subscription you already pay for to work: Codex, Gemini through Antigravity, GitHub Copilot, a second Claude account and local models. Sessions that remember, cross-model review with agreement counts, races your tests judge, and pipelines, from your terminal or inside Claude Code. Read-only by default, standard-library Python, no dependencies.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "farmout: put every AI subscription you pay for to work",
    template: "%s · farmout",
  },
  description: DESCRIPTION,
  keywords: [
    "farmout",
    "multi-model code review",
    "claude code mcp server",
    "codex cli",
    "github copilot cli",
    "antigravity cli",
    "gemini",
    "multi-agent cli",
    "ai code review",
    "git worktree agents",
    "agent pipelines",
    "claude code plugin",
  ],
  authors: [{ name: "Jay Pokale", url: AUTHOR_URL }],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "farmout",
    title: "farmout: every AI subscription you pay for, on one job",
    description:
      "Codex, Gemini, Copilot and a second Claude review your change in parallel, race on fixes your tests judge, and take turns in pipelines. Read-only by default, zero dependencies.",
  },
  twitter: {
    card: "summary_large_image",
    title: "farmout: put every AI subscription you pay for to work",
    description:
      "Parallel review with agreement counts, races judged by your tests, sessions that remember, pipelines. A CLI and an MCP server in standard-library Python.",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0c11" },
    { media: "(prefers-color-scheme: light)", color: "#f7f5f0" },
  ],
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "farmout",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Linux, macOS",
  softwareRequirements: "Python 3.9+, git, and at least one of: Antigravity CLI, Codex CLI, GitHub Copilot CLI, Claude Code, an OpenAI-compatible endpoint",
  description: DESCRIPTION,
  url: SITE_URL,
  downloadUrl: REPO_URL,
  codeRepository: REPO_URL,
  license: "https://opensource.org/licenses/MIT",
  author: { "@type": "Person", name: "Jay Pokale", url: AUTHOR_URL },
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={`${hubot.variable} ${mona.variable} ${martian.variable}`}
    >
      <body className="antialiased">
        {/* set the theme before paint, so no flash: dark by default, a saved choice wins */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("farmout-theme")||"dark";document.documentElement.dataset.theme=t}catch(e){}`,
          }}
        />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
        {children}
      </body>
    </html>
  );
}
