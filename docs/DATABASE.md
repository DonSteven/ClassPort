# Database configuration and deployment identities

The REST API and MCP tools share the runtime database pool. SQL migrations and
operator initialization use a separate maintenance connection. Within an
environment, both identities point to the same database, with different roles.

## Initial schema

`migrations/00000000000000_baseline.sql` creates the initial multi-user
schema, including user ownership, authentication attempt kinds, OAuth,
email verification, password reset, encrypted user secrets and RLS policies.
It also seeds the operator account. Later timestamped SQL files extend this
baseline and run once through `scripts/run_migrations.py`.

## Connection settings

`app/db_config.py` reads process environment variables only. It does not import
Web settings or load an env file. Compose loads the appropriate file for each
service. Standalone callers must supply their own environment.

| Caller | Connection selection |
|---|---|
| App / standalone file indexer | Required `DATABASE_URL` |
| SQL migrations / operator seed | Required `MIGRATION_DATABASE_URL` |
| Tests calling `init_pool(dsn)` | The explicitly supplied DSN |

Both connection variables require PostgreSQL URLs and never substitute for one
another. A missing URL is an error. Each URL contains the login credentials,
host, port, and database name. URL-encode reserved characters in credentials
(for example, `@` becomes `%40`). Treat a complete URL as a secret.

The `POSTGRES_*` settings in `.env.docker` initialize the local database
container; application and maintenance connections use only their required URLs.

| Variable | Default | Meaning |
|---|---|---|
| `DB_POOL_MIN_SIZE` | `2` | Minimum runtime pool size; `0` is allowed |
| `DB_POOL_MAX_SIZE` | `10` | Maximum runtime pool size |
| `DB_POOL_TIMEOUT` | `30` | Seconds waiting for an available pooled connection |
| `PGCONNECT_TIMEOUT` | `5` | Seconds establishing a database connection |
| `PGSSLMODE` | Driver default | TLS / server identity verification mode |
| `PGSSLROOTCERT` | Driver default | Trusted database CA certificate file |

Explicit `connect_timeout`, `sslmode`, and `sslrootcert` parameters in a DSN
take precedence over their environment variables. Pool wait time and connection
establishment time are separate settings. SQL migrations use a single connection,
not the runtime pool.

## Separate credential files

```bash
cp .env.example .env
cp .env.migration.example .env.migration
cp .env.docker.example .env.docker
chmod 600 .env .env.migration .env.docker
```

- `.env`: runtime `DATABASE_URL`, pool settings, and Web application settings.
- `.env.migration`: `MIGRATION_DATABASE_URL`, TLS settings, `OPERATOR_*`, and
  `APP_PASSWORD_HASH` for operator initialization. No session secret is needed.
- `.env.docker`: local PostgreSQL bootstrap credentials and public frontend
  build settings. The runtime container does not load this file.

Do not put the migration URL or administrator password in `.env`. Do not mount
maintenance credential files into the runtime container. Both env files use
Compose's raw format, preserving literal dollar signs in password hashes.
The migration service is a one-shot use of the same image as the backend.

## First local deployment

Set different passwords for `classport_app` in `.env` and
`classport_migrator` in `.env.migration`. Match the latter role/password in
`.env.docker`. The database name should match in all three files.

On a fresh local installation, prepare the schema before granting runtime access:

```bash
docker compose --env-file .env.docker --profile local-db build
docker compose --env-file .env.docker --profile local-db up -d --wait postgres
docker compose --env-file .env.docker run --rm --no-deps migrate
docker compose --env-file .env.docker exec postgres psql -U classport_migrator -d classport
```

In that administrator session, create the runtime role and set its password to
the runtime password in `.env` using the interactive password prompt:

```sql
CREATE ROLE classport_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
\password classport_app
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO classport_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classport_app;
REVOKE ALL ON public._migrations FROM classport_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classport_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classport_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classport_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classport_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO classport_app;
```

This local example preserves the existing RLS bypass behavior while keeping
schema ownership and migration credentials out of the app. The runtime role
must not own application tables or inherit the migration role. Enforcing
per-user RLS also requires work on global OAuth / indexing paths; see [RLS.md](RLS.md).
Database account provisioning is an explicit administrator operation, not an
automatic side effect of starting the app or running SQL migrations.

Exit psql, fill the operator email and password hash in `.env.migration`, and run
`./deploy.sh`. Later deployments run pending migrations and initialization before
starting the new backend. Default privileges cover new tables created by the
migration role.

## Existing installations

Keep the existing database volume and existing PostgreSQL login credentials.
Changing `POSTGRES_USER` / `POSTGRES_PASSWORD` does not change a database that
has already been initialized. Put that existing login in
`MIGRATION_DATABASE_URL`, provision a separate runtime role with the required
grants, and put only the runtime login in `.env`.

Move `APP_PASSWORD_HASH` and the operator initialization values from `.env` to
`.env.migration`. If you use a custom `OPERATOR_USER_ID`, keep the application
setting and initialization value consistent. Recreating the backend container
then removes the old bootstrap credentials from its environment.

## RDS / external PostgreSQL

Have the database administrator provision a database and separate migration and
runtime roles. The migration role needs permission to apply the schema and
install the `citext` extension. Runtime table grants and default privileges must
be made for the role that owns/creates those tables. Preserve the deployment's
RLS access requirements; do not grant `rds_superuser` or membership in the
migration role to the app to work around missing permissions.

Use the RDS endpoint in both URLs, with different credentials. Obtain the public
RDS CA bundle from AWS and place it in the certificate directory:

```bash
mkdir -p certs
curl -fsS https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
  -o certs/global-bundle.pem
```

Set these in **both** `.env` and `.env.migration`:

```dotenv
PGSSLMODE=verify-full
PGSSLROOTCERT=/etc/classport/certs/global-bundle.pem
```

Compose mounts `./certs` read-only into both services. `verify-full` verifies
the server certificate and hostname; use the actual RDS endpoint. The CA file is
public trust material, not an account password. It is excluded from image build
contexts and local downloaded PEM files are ignored by Git. See the
[AWS TLS instructions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html).

Keep `.env.docker` for public frontend build settings; its local `POSTGRES_*`
entries are unnecessary with RDS. Allow database network access from the
deployment host, then deploy with:

```bash
./deploy.sh --external-db
```

Use `--external-db` on subsequent deployments as well. This skips starting and
waiting for local PostgreSQL; the migration runner checks the external server.
For direct Compose use, enable `--profile local-db` only when a local database
is wanted. Avoid local Compose volume overrides that remove the CA mount.

## Validation

Tests cover required URLs, separate identities, TLS options, pool settings, standalone
scripts without Web settings, runtime DML without DDL, and resolved Compose
credential isolation. `init_pool(dsn)` still works without deployment credentials.

This assertion checks the running backend without printing any secret values:

```bash
docker compose --env-file .env.docker exec classport uv run --no-sync python -c \
  'import os; assert "MIGRATION_DATABASE_URL" not in os.environ; assert "POSTGRES_PASSWORD" not in os.environ'
```
