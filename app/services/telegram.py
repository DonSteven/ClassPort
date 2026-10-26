"""Telegram commands use authenticated account ownership and shared services."""
from typing import Optional
from uuid import UUID

import httpx

from ..logging_config import operation
from . import material_sync


async def send_message(token: str, chat_id: int, text: str) -> None:
    """Send a Telegram message via sendMessage API."""
    async with httpx.AsyncClient(timeout=10) as client:
        with operation("external.telegram"):
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )


async def handle_command(text: str, chat_id: int, user_id: Optional[UUID] = None) -> str:
    """Commands use the same account-scoped workflow contract as the web UI."""
    from fastapi import HTTPException

    cmd = text.split()[0].lower()
    if cmd in ("/start", "/help"):
        return (
            "Available commands:\n"
            "/sync — import course materials now\n"
            "/pause — pause scheduled imports for your account\n"
            "/resume — resume scheduled imports\n"
            "/status — show scheduled import state\n"
            "/help — this message"
        )
    if cmd not in ("/sync", "/pause", "/resume", "/status"):
        return "Unknown command. Try /help"
    if user_id is None:
        return "An authenticated account is required."
    if cmd == "/sync":
        try:
            result = await material_sync.trigger(user_id)
        except HTTPException as exc:
            return f"Sync unavailable: {exc.detail}"
        return f"Import completed: {result.get('written_count', 0)} files."
    if cmd in ("/pause", "/resume"):
        await material_sync.set_paused(user_id, cmd == "/pause")
    state = "paused" if await material_sync.paused(user_id) else "active"
    return f"Scheduled imports: {state}."
