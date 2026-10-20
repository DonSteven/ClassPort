"""Database configuration shared by the app and standalone maintenance scripts.

Only process environment variables are read here. Compose (or the caller) chooses
which env file to load; importing this module never loads Web application settings.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from psycopg.conninfo import conninfo_to_dict, make_conninfo


def _database_dsn(variable: str) -> str:
    url = os.environ.get(variable, "").strip()
    if not url:
        raise RuntimeError(f"{variable} is required.")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError(f"{variable} must be a PostgreSQL URL.")
    params = conninfo_to_dict(url)

    # Explicit URL parameters win over libpq environment defaults. Both entry
    # points use the same TLS and connection establishment timeout settings.
    for parameter, env_name in (
        ("sslmode", "PGSSLMODE"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("connect_timeout", "PGCONNECT_TIMEOUT"),
    ):
        if parameter not in params and os.environ.get(env_name):
            params[parameter] = os.environ[env_name]
    params.setdefault("connect_timeout", "5")
    return make_conninfo(**params)


def get_runtime_dsn() -> str:
    """Read the required runtime DATABASE_URL."""
    return _database_dsn("DATABASE_URL")


def get_migration_dsn() -> str:
    """Read the required, separate MIGRATION_DATABASE_URL."""
    return _database_dsn("MIGRATION_DATABASE_URL")


@dataclass(frozen=True)
class PoolConfig:
    min_size: int
    max_size: int
    timeout: float


def get_pool_config() -> PoolConfig:
    """Read pool capacity and the maximum wait for an available connection."""
    try:
        config = PoolConfig(
            min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "2")),
            max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
            timeout=float(os.environ.get("DB_POOL_TIMEOUT", "30")),
        )
    except ValueError:
        raise ValueError("DB_POOL sizes must be integers and DB_POOL_TIMEOUT a number.") from None
    if not 0 <= config.min_size <= config.max_size or config.max_size < 1:
        raise ValueError("Require 0 <= DB_POOL_MIN_SIZE <= DB_POOL_MAX_SIZE and max >= 1.")
    if not math.isfinite(config.timeout) or config.timeout <= 0:
        raise ValueError("DB_POOL_TIMEOUT must be a finite positive number of seconds.")
    return config
