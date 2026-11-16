// next/og (Satori) can't read the site's self-hosted woff2 variable fonts, so the
// preview card fetches static instances from Google Fonts at build time. An old
// Safari user agent makes Google answer with plain WOFF, which Satori reads.
// (The same approach as jaypokale.me.)
const LEGACY_UA =
  "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/534.54.16 (KHTML, like Gecko) Version/5.1.4 Safari/534.54.16";

async function googleFont(query: string) {
  const css = await fetch(`https://fonts.googleapis.com/css2?family=${query}`, {
    headers: { "User-Agent": LEGACY_UA },
  }).then((r) => r.text());
  const url = css.match(/url\((https:[^)]+)\)/)?.[1];
  if (!url) throw new Error(`Google Fonts returned no file for ${query}`);
  return fetch(url).then((r) => r.arrayBuffer());
}

/** The card's fonts, or undefined (next/og's bundled font) if Google is unreachable. */
export async function ogFonts() {
  try {
    const [display, mono] = await Promise.all([
      googleFont("Hubot+Sans:wdth,wght@116,800"),
      googleFont("Martian+Mono:wght@400"),
    ]);
    return [
      { name: "Hubot", data: display, weight: 800 as const, style: "normal" as const },
      { name: "Martian", data: mono, weight: 400 as const, style: "normal" as const },
    ];
  } catch {
    return undefined;
  }
}
