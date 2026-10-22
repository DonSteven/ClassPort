# Changelog

All notable changes to ClassPort will be documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## Unreleased

### 2026-10-22

#### Added

- **GitHub-to-AWS delivery.** Publish or reuse commit-tagged ECR images,
  verify the image pair, and deploy through SSM using short-lived GitHub OIDC
  credentials. Bootstrap roles and migrations in a maintenance container, retain
  structured deployment results, and restore the previous release on failure.
- **Request tracing and monitoring.** Correlate structured HTTP, database,
  storage and external-call logs through `X-Request-ID`, excluding credentials
  and request payloads. Add readiness, disk, backup and HTTP-error monitoring
  with CloudWatch alarms.
- **Consistent backup and recovery.** Pause writers, export PostgreSQL and
  archive course materials under a versioned backup manifest. Verify file
  references and restore the recorded data, image pair and configuration into
  an independent environment, with scheduled backups and recovery smoke checks.
- **File and job schema.** Introduce user-scoped file records, immutable
  revisions, processing jobs, attempt history and a transactional outbox, with
  ownership constraints and uniqueness rules for paths and processing requests.

### 2026-10-21

#### Added

- **Reproducible production releases.** Build and smoke-test backend and
  frontend images in CI, deploy their immutable digests with a dedicated
  production Compose configuration, and retain persistent course storage.
  Serialize migrations, verify readiness plus login/REST/MCP access, and record
  paired releases for automatic recovery and manual rollback.
- **AWS infrastructure.** Add Terraform for an EC2 host, persistent
  encrypted EBS storage, private PostgreSQL RDS, immutable ECR repositories, a
  versioned backup bucket, Secrets Manager and CloudWatch. Configure Caddy HTTPS
  and Systems Manager administration without opening SSH.

### 2026-10-20

#### Added

- **Health probes.** Add `/api/health/live` for process liveness and
  `/api/health/ready` for database and storage readiness. Readiness returns HTTP
  503 on dependency failure within a shared two-second budget; `/api/health`
  remains a readiness alias. Storage probes no longer create activity events.

#### Changed

- **Deployment gates.** Run backend tests, lint, a Docker build, Compose
  validation and the frontend production build before deployment. Resolve one
  commit and deploy the same revision that passed those checks.
- **Database configuration.** Share PostgreSQL URL, TLS, connection-timeout
  and pool settings between the application and maintenance tools. Separate
  runtime `DATABASE_URL` from maintenance-only `MIGRATION_DATABASE_URL`.
- **Local deployment.** Run migrations and operator initialization in a
  dedicated maintenance service, support external PostgreSQL through
  `--external-db`, and save the previous application images before rebuilding.

#### Security

- **TOTP login.** Return HTTP 503 without issuing a session when TOTP state
  cannot be read, the user is missing, or enabled TOTP has no secret. Database
  failures no longer silently disable the second factor.

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
