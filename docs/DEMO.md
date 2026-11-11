# Interactive demo

The demo reuses the ClassPort frontend with browser-local study data. It needs no
FastAPI server, database, AWS account, or AI API key at runtime. The full
self-hosted application remains available through the normal build.

## Develop and build

From `web/`, with the project's existing dependencies installed:

```sh
pnpm dev:demo
pnpm test
pnpm build:demo
pnpm preview
```

`build:demo` enables `VITE_DEMO_MODE=true` through `.env.demo` and produces
`web/dist/`. Set `VITE_SITE_URL` to the intended public origin before building,
or override it in `web/.env.demo.local`. The frontend and generated sitemap,
manifest, and crawler rules use the same deployment settings.

The ordinary `pnpm build` continues to use the real backend. Demo requests are
handled inside `src/demo/api.ts`; they never fall through to a remote API,
including when `VITE_API_BASE_URL` is set.

## Visitor experience

- `/` introduces the project and links to the interactive demo.
- `/app` opens a sample semester without registration or login.
- Course, schedule, task, deadline, exam, and study-topic edits are saved in this
  browser under `classport:demo`. Reloading preserves them. Different visitors
  have independent data.
- **Reset demo** restores the initial dataset and returns to the dashboard.
- All seven existing themes and English/Chinese interface controls are available.
- Sample files support browsing, search, preview, renaming, folders, and deletion.
  Static sample assets are served by the same host. Uploads and cloud sync are
  unavailable in this demo.
- **MCP example** clearly labels its prepared responses and can add a follow-up
  task to the local demo. It makes no model or MCP server call.

Fixture dates are relative to the first visit or reset, so the sample semester
has upcoming work when a new visitor opens it. Fixture records are defined in
`web/src/demo/data.ts`; original public sample documents live in
`web/src/demo/materials/`. The same document source supplies the searchable text
and its bundled static file. Never copy account records, credentials, personal contacts,
or private course files into these public assets.
