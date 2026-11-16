import { REPO } from "./site";

// Live GitHub data, ISR-cached for an hour and fetched on the server, so the
// number is in the HTML (crawlers see it) and rate limits never reach visitors.
export async function getRepoStats(): Promise<{ stars: number }> {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}`, {
      next: { revalidate: 3600 },
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!res.ok) throw new Error(String(res.status));
    const j = await res.json();
    return { stars: j.stargazers_count ?? 0 };
  } catch {
    return { stars: 0 };
  }
}
