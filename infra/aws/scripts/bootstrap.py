"""Initialize the AWS installation inside the backend image, using host-only secrets.

The runtime role has DML grants and permissive RLS policies, preserving the app's
current application-level user isolation without RDS superuser or schema ownership.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql


OPERATOR_ID = "00000000-0000-0000-0000-000000000001"
SMOKE_ID = "00000000-0000-0000-0000-000000000002"
REQUIRED_KEYS = {
    "OPERATOR_EMAIL", "OPERATOR_PASSWORD", "SMOKE_EMAIL", "SMOKE_PASSWORD",
    "SMOKE_MCP_TOKEN", "APP_DB_PASSWORD", "MIGRATION_DB_PASSWORD",
    "COORDINATOR_DB_PASSWORD",
    "SESSION_SECRET", "SECRETS_ENCRYPTION_KEY",
}
JOB_DEFAULTS = {"JOB_TIME_LIMIT": "120", "JOB_MAX_ATTEMPTS": "3", "JOB_REDELIVERY_SECONDS": "60"}


def validate_config(config):
    if not REQUIRED_KEYS <= config.keys() or config.keys() - REQUIRED_KEYS - JOB_DEFAULTS.keys() or any(
        not isinstance(value, str) or not value or "\n" in value or "\r" in value or "\0" in value
        for value in config.values()
    ):
        raise ValueError("Invalid application secret keys/values")
    for name, default in JOB_DEFAULTS.items():
        if int(config.get(name, default)) < (2 if name == "JOB_TIME_LIMIT" else 1):
            raise ValueError(f"Invalid {name}")
    if config["OPERATOR_EMAIL"].strip().lower() == config["SMOKE_EMAIL"].strip().lower():
        raise ValueError("The release check account must be separate from the operator")
    if len({config[key] for key in ("APP_DB_PASSWORD", "MIGRATION_DB_PASSWORD", "COORDINATOR_DB_PASSWORD")}) != 3:
        raise ValueError("Runtime, migration and coordinator database passwords must differ")
    Fernet(config["SECRETS_ENCRYPTION_KEY"].encode())


def database_url(user, password):
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ['DB_NAME']}"
    )


def write_env(path, values):
    # Compose's raw env format preserves dollar signs in Argon2 hashes.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write("".join(f"{key}={value}\n" for key, value in values.items()))


def provision_roles(admin_dsn, config):
    with psycopg.connect(admin_dsn) as conn:
        for role, password in (
            ("classport_migrator", config["MIGRATION_DB_PASSWORD"]),
            ("classport_app", config["APP_DB_PASSWORD"]),
            ("classport_jobs", config["COORDINATOR_DB_PASSWORD"]),
        ):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role)))
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        # Administrator can assign schema ownership, but app cannot inherit it.
        admin = conn.execute("SELECT current_user").fetchone()[0]
        conn.execute(sql.SQL("GRANT classport_migrator TO {}").format(sql.Identifier(admin)))
        db = sql.Identifier(os.environ["DB_NAME"])
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        conn.execute(sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO classport_migrator").format(db))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO classport_app").format(db))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO classport_jobs").format(db))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("ALTER SCHEMA public OWNER TO classport_migrator")


def grant_runtime_access(migration_dsn):
    with psycopg.connect(migration_dsn) as conn:
        conn.execute("GRANT USAGE ON SCHEMA public TO classport_app")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classport_app")
        conn.execute("REVOKE ALL ON public._migrations FROM classport_app")
        conn.execute("GRANT USAGE ON SCHEMA public TO classport_jobs")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON public.jobs, public.job_attempts, public.outbox TO classport_jobs")
        conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classport_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classport_app"
        )
        conn.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO classport_app"
        )
        # RDS administrators are not PostgreSQL superusers. A role-specific
        # policy supplies the current app's bypass behavior without BYPASSRLS.
        for (table,) in conn.execute(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relrowsecurity AND c.relkind = 'r'"
        ).fetchall():
            table_name = sql.Identifier("public", table)
            conn.execute(sql.SQL("DROP POLICY IF EXISTS classport_runtime ON {}").format(table_name))
            conn.execute(sql.SQL(
                "CREATE POLICY classport_runtime ON {} TO classport_app USING (true) WITH CHECK (true)"
            ).format(table_name))


def seed_smoke(migration_dsn, config):
    with psycopg.connect(migration_dsn) as conn:
        conn.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, password_hash = EXCLUDED.password_hash",
            (SMOKE_ID, config["SMOKE_EMAIL"].strip().lower(), "Release checks",
             PasswordHasher().hash(config["SMOKE_PASSWORD"])),
        )
        conn.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, token_endpoint_auth_method) "
            "VALUES ('aws-release-check', 'AWS release checks', ARRAY['http://127.0.0.1/callback'], 'none') "
            "ON CONFLICT (client_id) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO oauth_tokens (user_id, token, client_id, scope, expires_at) "
            "VALUES (%s, %s, 'aws-release-check', 'mcp', now() + interval '30 days') "
            "ON CONFLICT (token) DO UPDATE SET expires_at = EXCLUDED.expires_at",
            (SMOKE_ID, config["SMOKE_MCP_TOKEN"]),
        )


def configure_runtime(config, root):
    tls = {"PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/etc/classport/certs/global-bundle.pem", "PGCONNECT_TIMEOUT": "5"}
    write_env(root / ".env", {
        **tls, "DATABASE_URL": database_url("classport_app", config["APP_DB_PASSWORD"]),
        "SESSION_SECRET": config["SESSION_SECRET"],
        "SECRETS_ENCRYPTION_KEY": config["SECRETS_ENCRYPTION_KEY"],
        "PUBLIC_URL": os.environ["PUBLIC_URL"], "CORS_ORIGINS": os.environ["PUBLIC_URL"],
        "OPERATOR_EMAIL": config["OPERATOR_EMAIL"].strip().lower(), "OPERATOR_USER_ID": OPERATOR_ID,
        "SIGNUPS_ENABLED": "false",
    })
    write_env(root / ".env.smoke", {
        "SMOKE_BASE_URL": os.environ["PUBLIC_URL"], "SMOKE_EMAIL": config["SMOKE_EMAIL"].strip().lower(),
        "SMOKE_PASSWORD": config["SMOKE_PASSWORD"], "SMOKE_MCP_TOKEN": config["SMOKE_MCP_TOKEN"],
    })
    write_env(root / ".env.coordinator", {
        **tls, "DATABASE_URL": database_url("classport_jobs", config["COORDINATOR_DB_PASSWORD"]),
        "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "2",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
    })
    write_env(root / ".env.jobs", {name: config.get(name, default) for name, default in JOB_DEFAULTS.items()})
    (root / ".env.migration").unlink(missing_ok=True)


def bootstrap(config, master, root, result_path):
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "steps": {name: "pending" for name in ("validation", "roles", "migration", "runtime_grants", "accounts", "configuration")}}

    def save():
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report) + "\n")
        temporary.replace(result_path)

    def step(name, action):
        report["steps"][name] = "running"
        save()
        try:
            action()
        except Exception:
            report["steps"][name] = "failed"
            report["status"] = "failed"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
            raise
        report["steps"][name] = "succeeded"
        save()

    save()
    step("validation", lambda: validate_config(config))
    # All connections verify both the RDS certificate chain and endpoint hostname.
    tls = {"PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/etc/classport/certs/global-bundle.pem", "PGCONNECT_TIMEOUT": "5"}
    os.environ.update(tls)
    admin_dsn = database_url(master["username"], master["password"])
    migration_dsn = database_url("classport_migrator", config["MIGRATION_DB_PASSWORD"])
    step("roles", lambda: provision_roles(admin_dsn, config))
    maintenance = {
        **tls, "MIGRATION_DATABASE_URL": migration_dsn,
        "OPERATOR_EMAIL": config["OPERATOR_EMAIL"].strip().lower(),
        "OPERATOR_USER_ID": OPERATOR_ID, "OPERATOR_DISPLAY_NAME": "Operator",
        "APP_PASSWORD_HASH": PasswordHasher().hash(config["OPERATOR_PASSWORD"]),
    }
    def run_script(script):
        # Failed SQL/connection exceptions can contain secrets. Only the stage
        # outcome is exported; credentials live in this maintenance process.
        subprocess.run([sys.executable, f"/app/scripts/{script}"],
                       env={**os.environ, **maintenance}, capture_output=True, check=True)

    step("migration", lambda: run_script("run_migrations.py"))
    step("runtime_grants", lambda: grant_runtime_access(migration_dsn))

    def accounts():
        run_script("seed_operator_password.py")
        seed_smoke(migration_dsn, config)

    step("accounts", accounts)

    step("configuration", lambda: configure_runtime(config, root))
    report.update(status="succeeded", finished_at=datetime.now(timezone.utc).isoformat())
    save()
    print("AWS database identities, operator and release checks are ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--recovery-phase", choices=["roles", "configure"])
    arguments = parser.parse_args()
    try:
        config = json.loads(Path("/run/secrets/application.json").read_text())
        master = json.loads(Path("/run/secrets/database.json").read_text())
        if arguments.recovery_phase:
            validate_config(config)
            os.environ.update(PGSSLMODE="verify-full", PGSSLROOTCERT="/etc/classport/certs/global-bundle.pem")
            admin_dsn = database_url(master["username"], master["password"])
            migration_dsn = database_url("classport_migrator", config["MIGRATION_DB_PASSWORD"])
            if arguments.recovery_phase == "roles":
                provision_roles(admin_dsn, config)
            else:
                # Verify the original key against every stored encrypted value.
                with psycopg.connect(migration_dsn) as conn:
                    for row in conn.execute("SELECT telegram_bot_token_enc, telegram_webhook_secret_enc FROM user_secrets"):
                        for value in row:
                            if value is not None:
                                Fernet(config["SECRETS_ENCRYPTION_KEY"].encode()).decrypt(bytes(value))
                grant_runtime_access(migration_dsn)
                configure_runtime(config, Path("/deployment"))
        else:
            if arguments.result_file is None:
                parser.error("--result-file is required for normal database initialization")
            bootstrap(config, master, Path("/deployment"), arguments.result_file)
    except Exception:
        if arguments.result_file and arguments.result_file.exists():
            report = json.loads(arguments.result_file.read_text())
            report.update(status="failed", finished_at=datetime.now(timezone.utc).isoformat())
            temporary = arguments.result_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(report) + "\n")
            temporary.replace(arguments.result_file)
        # Connection errors can include DSNs and SQL can include literal passwords.
        print("AWS bootstrap failed; secret values and exception bodies omitted.", file=sys.stderr)
        raise SystemExit(1) from None
