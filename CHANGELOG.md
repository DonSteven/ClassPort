# Changelog

All notable changes to ClassPort will be documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## Unreleased

### 2026-10-20

#### Changed

- **Deployment gates.** Run backend tests, lint, a Docker build, Compose
  validation and the frontend production build before deployment. Resolve one
  commit and deploy the same revision that passed those checks.

### 2026-09-25

#### Added

- **Course archive.** Archive finished semesters without deleting courses
  or their files. Hide archived coursework from active dashboards, navigation,
  tasks and exams; browse or restore it from the courses page. Support the same
  behavior through `include_archived` dashboard queries and MCP course updates.

#### Fixed

- **OAuth client registration.** Stop deleting registered clients that
  have not completed login, preserving client IDs cached by MCP clients. Allow
  loopback redirects to use any port while matching scheme, host, path and query.
- **Zine course tags.** Resize sidebar tags and adjust their font size so
  course codes up to eight characters remain contained.
- **File previews.** Serve PDF, PNG, JPEG, GIF and WebP with inline
  disposition so they open in the viewer. Keep other formats as attachments and
  send `X-Content-Type-Options: nosniff` for file responses.

### 2026-09-17

#### Added

- **Initial application.** React dashboard, courses, weekly schedules, lectures,
  tasks, deliverables, exams, activity and settings, with five themes and
  English/Chinese localization.
- **MCP and multi-user accounts.** OAuth-protected HTTP MCP tools and file
  previews; email signup, verification, password reset and per-user data
  isolation through service filters, composite keys and optional RLS enforcement.
- **Self-hosted stack.** Docker Compose deployment for FastAPI, PostgreSQL
  and the Caddy-served frontend, with local course storage, full-text file search,
  transactional migrations and backend integration tests.
- **Account security and Telegram.** TOTP login, signed OAuth consent state
  and encrypted per-user Telegram credentials configured through Settings.

#### Security

- **OAuth client registration.** Rate-limit `/oauth/register`, validate
  redirect URIs, cap client metadata and show the exact redirect target with an
  unverified-client warning on consent. Access-token lifetime is configurable
  through `OAUTH_TOKEN_TTL_DAYS`, defaulting to 30 days.
