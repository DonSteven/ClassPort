# Row Level Security

The baseline defines per-user RLS policies on owned tables. They reference
the session GUC `app.user_id`, which the app sets via the auth middleware
on every authenticated request.

## Current state

- The app uses `DATABASE_URL`; migrations and initialization use
  `MIGRATION_DATABASE_URL`. See [database setup](DATABASE.md).
- Whether policies apply depends on the configured runtime role's RLS bypass
  and table ownership. Separating credentials does not itself change RLS policy.
- Existing deployments rely on bypass behavior for global OAuth and indexing
  operations. App-side `user_id` filters remain necessary.

## Switching to enforce RLS in prod

### 1. Create a non-bypass role

```sql
CREATE ROLE classport_app NOBYPASSRLS LOGIN PASSWORD '<strong-password>';
GRANT USAGE ON SCHEMA public TO classport_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classport_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classport_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classport_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO classport_app;
```

### 2. Update `.env`

```
DATABASE_URL=postgresql://classport_app:<strong-password>@postgres:5432/classport
```

### 3. Known limitation — signup / email-lookup paths

OAuth code/token exchange and global file indexing access rows before a
per-user context is available. A non-bypass runtime role will require suitable
policies or narrowly scoped `SECURITY DEFINER` functions for these operations.
User/email-token policies also need to be considered if RLS is enabled on those
tables. Do not pass migration credentials to the runtime to work around this.
Credential separation does not automatically enable a complete non-bypass flow.

### 4. Verify post-switch

```bash
curl -f --max-time 3 https://your-domain.tld/api/health/ready  # HTTP 200, {"ok":true,...}
curl -H "Cookie: <session>" https://your-domain.tld/api/courses  # operator's courses only
# Sign up a second user; verify that user's data is isolated from the first.
```

### 5. Rollback

Restore the previous runtime role's RLS access configuration, then restart the
backend. Keep migration credentials out of the runtime `.env`:

```bash
cd /opt/classport && docker compose --env-file .env.docker up -d --force-recreate classport
```

The role's bypass/ownership determines RLS enforcement; service-layer filters remain active.
