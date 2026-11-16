// One place for the URLs the page links to. NEXT_PUBLIC_SITE_URL overrides the
// canonical URL (sitemap, robots, Open Graph) once the site has its own domain.
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://farmout.jaypokale.me";

export const REPO = "JayPokale/farmout";
export const REPO_URL = `https://github.com/${REPO}`;
export const AUTHOR_URL = "https://github.com/JayPokale";
export const COFFEE_URL = "https://buymeacoffee.com/jaypokale3b";

export const blob = (path: string) => `${REPO_URL}/blob/main/${path}`;
export const tree = (path: string) => `${REPO_URL}/tree/main/${path}`;

export const INSTALL = `pipx install git+${REPO_URL}`;
