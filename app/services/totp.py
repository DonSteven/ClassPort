"""TOTP state I/O — per-user storage.

Phase 1: TOTP moved from app_settings.totp_* to users.totp_*.
Phase 2: hardcoded SENTINEL_USER_ID becomes a real user_id parameter.
"""
from typing import Optional
from uuid import UUID

from .. import db


async def get_state(user_id: UUID) -> tuple[bool, Optional[str]]:
    """Return the user's TOTP state; raise if unavailable or inconsistent."""
    row = await db.fetchrow(
        "SELECT totp_enabled, totp_secret FROM users WHERE id = %s LIMIT 1",
        user_id,
    )
    if row is None:
        raise LookupError("User not found when reading TOTP state")
    if row["totp_enabled"] and not row["totp_secret"]:
        raise ValueError("Enabled TOTP requires a secret")
    return bool(row["totp_enabled"]), row["totp_secret"]


async def set_pending(user_id: UUID, secret: str) -> None:
    """Store a fresh secret with totp_enabled=false.

    The users row always exists (created by the users-table migration's
    seed), so a plain UPDATE suffices — no upsert needed.
    """
    await db.execute(
        "UPDATE users SET totp_secret = %s, totp_enabled = false WHERE id = %s",
        secret, user_id,
    )


async def enable(user_id: UUID) -> None:
    await db.execute(
        "UPDATE users SET totp_enabled = true WHERE id = %s",
        user_id,
    )


async def disable(user_id: UUID) -> None:
    await db.execute(
        "UPDATE users SET totp_enabled = false, totp_secret = NULL WHERE id = %s",
        user_id,
    )
