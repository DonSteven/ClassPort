#!/usr/bin/env node
/**
 * Generate deployment-specific SEO files after `vite build`:
 *   web/dist/robots.txt
 *   web/dist/sitemap.xml
 *   web/dist/manifest.webmanifest
 *
 * Env priority: process.env > web/.env.local > web/.env
 * Public source files use placeholders; deployment URLs stay in dist/.
 *
 * Self-hosters set VITE_SITE_URL (and optionally VITE_SITE_NAME) in their own
 * env before building so canonical URLs / sitemap / manifest match their domain.
 */
import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(__dirname, "..", "web");
const OUTPUT = join(WEB, "dist");

function loadEnvFile(path) {
  if (!existsSync(path)) return {};
  const out = {};
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (line.trim().startsWith("#") || !line.trim()) continue;
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/i);
    if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  return out;
}

const env = {
  ...loadEnvFile(join(WEB, ".env")),
  ...loadEnvFile(join(WEB, ".env.local")),
  ...process.env,
};

const SITE_URL = (env.VITE_SITE_URL || "https://your-domain.tld").replace(/\/$/, "");
const SITE_NAME = env.VITE_SITE_NAME || "ClassPort";
const today = new Date().toISOString().slice(0, 10);

// ── robots.txt ────────────────────────────────────────────────────────────
writeFileSync(
  join(OUTPUT, "robots.txt"),
  `# ${SITE_NAME} — ${SITE_URL}
# Authenticated app lives behind /login; only the landing page, static brand
# assets, and the OG card are meant to be crawlable.

User-agent: *
Allow: /
Allow: /.well-known/security.txt
Disallow: /api/
Disallow: /mcp/
Disallow: /oauth/
Disallow: /.well-known/oauth-
Disallow: /login

Sitemap: ${SITE_URL}/sitemap.xml
`,
);

// ── sitemap.xml ───────────────────────────────────────────────────────────
writeFileSync(
  join(OUTPUT, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url>
    <loc>${SITE_URL}/</loc>
    <lastmod>${today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
    <xhtml:link rel="alternate" hreflang="en" href="${SITE_URL}/" />
    <xhtml:link rel="alternate" hreflang="zh" href="${SITE_URL}/zh" />
    <xhtml:link rel="alternate" hreflang="x-default" href="${SITE_URL}/" />
  </url>
</urlset>
`,
);

// ── manifest.webmanifest ──────────────────────────────────────────────────
const manifest = {
  name: SITE_NAME,
  short_name: SITE_NAME,
  description: "Self-hostable study dashboard with ChatGPT MCP integration.",
  start_url: "/",
  scope: "/",
  display: "standalone",
  background_color: "#1a1512",
  theme_color: "#1a1512",
  icons: [
    { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
    { src: "/icon-256.png", sizes: "256x256", type: "image/png", purpose: "any" },
    { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
    { src: "/apple-touch-icon.png", sizes: "180x180", type: "image/png", purpose: "any" },
  ],
};
writeFileSync(join(OUTPUT, "manifest.webmanifest"), JSON.stringify(manifest, null, 2) + "\n");

// Apply the deployment origin to the copied contact file, leaving public/ unchanged.
const securityPath = join(OUTPUT, ".well-known", "security.txt");
if (existsSync(securityPath)) {
  const security = readFileSync(securityPath, "utf8").replace(
    /^Canonical:.*$/m,
    `Canonical: ${SITE_URL}/.well-known/security.txt`,
  );
  writeFileSync(securityPath, security);
}

console.log(
  `[build-seo] robots.txt, sitemap.xml, manifest.webmanifest regenerated (SITE_URL=${SITE_URL}, SITE_NAME=${SITE_NAME})`,
);
