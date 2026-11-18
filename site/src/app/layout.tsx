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
  "farmout hands work to the AI subscriptions you already pay for (Codex, Gemini through Antigravity, GitHub Copilot, DeepSeek, a second Claude account, local models) so your main model's quota lasts, and when one hits its usage limit, the task and its conversation move to the next. Cross-model review with agreement counts, races your tests judge, conversations you can branch off any turn, and pipelines, from your terminal or inside Claude Code. Read-only by default, standard-library Python, no dependencies.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "farmout: hit your AI limit less, keep working when you do",
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
    title: "farmout: hit your AI limit less, keep working when you do",
    description:
      "Hand long reads and reviews to Codex, Gemini, Copilot or a second Claude; when one hits its limit, the task and its conversation move to the next. Read-only by default, zero dependencies.",
  },
  twitter: {
    card: "summary_large_image",
    title: "farmout: hit your AI limit less, keep working when you do",
    description:
      "Your other AI subscriptions take the long reads and reviews; a task at its limit moves on with its conversation. Parallel review, races judged by your tests, branching conversations. Standard-library Python.",
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
