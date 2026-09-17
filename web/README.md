# ClassPort frontend

The ClassPort web interface uses React, TypeScript, and Vite. It includes the
study dashboard, course and file management, settings, and the public landing page.

## Development

Start the backend using the [installation guide](../INSTALL.md), then run from
this directory:

```bash
pnpm install --frozen-lockfile
VITE_API_BASE_URL=http://localhost:8000 pnpm dev
```

The development server runs at `http://localhost:5173`. Set
`VITE_SHOW_LANDING=true` to show the public landing page at `/`.

## Validate and build

```bash
pnpm test
pnpm build
```

The build checks TypeScript, creates `dist/`, and generates the deployment's
manifest, sitemap, and crawler rules. Set `VITE_SITE_URL` and `VITE_SITE_NAME`
for your deployment. The [project README](../README.md) covers the full stack.

Brand artwork and dashboard previews are in `public/brand/` and
`public/screenshots/`; their source exports are in
[`docs/brand/`](../docs/brand/README.md) and `docs/screenshots/`.
