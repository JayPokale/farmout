# farmout site

The landing page: Next.js 16, Tailwind 4 and motion. It lives in the repository but ships nowhere else: the Python package only includes `farmout/`, so `pip install` never sees it.

## Dev

```bash
cd site
npm install
npm run dev        # http://localhost:3000
npm run typecheck
npm run build
```

## Deploy (Vercel)

1. vercel.com → New Project → import `JayPokale/farmout`.
2. **Root Directory: `site`** (the one setting that matters).
3. The framework is detected as Next.js. Deploy.
4. Custom domain: Project → Settings → Domains → add it, and point DNS at Vercel (A `76.76.21.21`, or CNAME `cname.vercel-dns.com` for a subdomain such as `farmout.jaypokale.me`).
5. Set `NEXT_PUBLIC_SITE_URL=https://<your domain>` (Project → Settings → Environment Variables) and redeploy. The canonical URL, sitemap, robots and Open Graph tags all read it; it defaults to `https://farmout.jaypokale.me`.

The GitHub star count is fetched on the server with a one-hour cache, so the number is in the HTML and visitors never hit GitHub's rate limits.

## Content rules

- Numbers come from `bench/README.md` and say where they came from. The terminal replays in `src/data/runs.ts` are verbatim output of real runs; to update one, paste the new run's stdout there.
- Commands and flags match the README (install paths, the Safety table, settings). If one changes there, change it here.
- Colour means a worker: codex amber, antigravity teal, copilot violet, claude coral, api bone. farmout itself is bone, or all four lanes at once (`text-lanes`, `bg-lanes`).

SEO: `robots.ts` welcomes GPTBot, ClaudeBot, PerplexityBot and the rest by name; `public/llms.txt` is the llmstxt.org summary; JSON-LD `SoftwareApplication` is in the layout; the Open Graph image is generated at `/opengraph-image`.
