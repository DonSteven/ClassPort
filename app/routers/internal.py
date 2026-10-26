"""Internal endpoints not meant for end users.

Protected by a shared secret in the `X-Internal-Secret` header (configured
via `INTERNAL_API_SECRET`). Used by background jobs (n8n workflows, cron
scripts) to trigger server-side actions like reindexing or to deliver
inbound webhooks.

The `/telegram` endpoint is special: it's authenticated by per-user
`X-Telegram-Bot-Api-Secret-Token` headers (stored in user_secrets), not
the shared secret. Each user runs their own bot.
"""
import logging
import os
from uuid import UUID

from fastapi import APIRouter, Body, Header, HTTPException, Request, status

from ..auth import set_current_user_id
from .. import db
from ..services import jobs as jobs_svc
from ..services import telegram as telegram_svc
from ..services import user_secrets as user_secrets_svc
from ..services import file_records, material_sync
from ..storage.base import StorageNotFound

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


def _check_secret(provided: str | None) -> None:
    expected = os.environ.get("INTERNAL_API_SECRET", "").strip()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="INTERNAL_API_SECRET not configured on server")
    if not provided or provided.strip() != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bad secret")


@router.post('/materials/upload-url')
async def material_upload(body: dict = Body(...),
                          x_internal_secret: str | None = Header(default=None, alias='X-Internal-Secret')):
    _check_secret(x_internal_secret)
    owner = await material_sync.import_user(body.get('user_id'))
    if body.get('scheduled') and await material_sync.paused(owner):
        raise HTTPException(409, 'scheduled material import is paused')
    path = body.get('path', '')
    if not isinstance(path, str):
        raise HTTPException(400, 'invalid path')
    try:
        current = await file_records.current_version(owner, path)
    except StorageNotFound:
        current = None
    if current and (current['size'], current['sha256']) == (body.get('size'), body.get('sha256')):
        return {'reused': True, 'file_id': current['file_id'], 'version_id': current['id']}
    return await file_records.begin_upload(owner, path, size=body.get('size'),
                                           sha256=body.get('sha256'), content_type=body.get('content_type'))


@router.put('/materials/{file_id}/versions/{version_id}/content')
async def material_content(file_id: UUID, version_id: UUID, user_id: UUID, request: Request,
                           x_internal_secret: str | None = Header(default=None, alias='X-Internal-Secret')):
    _check_secret(x_internal_secret)
    owner = await material_sync.import_user(user_id)
    row = await db.fetchrow('SELECT storage_backend FROM file_versions WHERE user_id=%s AND file_id=%s AND id=%s',
                            owner, file_id, version_id)
    if not row:
        raise HTTPException(404, 'file version not found')
    if row['storage_backend'] != 'local':
        raise HTTPException(409, 'send content directly to the issued S3 URL')
    return await file_records.accept_upload(owner, file_id, version_id, await request.body(),
                                            request.headers.get('content-type'))


@router.post('/materials/{file_id}/versions/{version_id}/complete')
async def material_complete(file_id: UUID, version_id: UUID, body: dict = Body(...),
                            x_internal_secret: str | None = Header(default=None, alias='X-Internal-Secret')):
    _check_secret(x_internal_secret)
    owner = await material_sync.import_user(body.get('user_id'))
    return await file_records.complete_upload(owner, file_id, version_id, body.get('object_version'))


@router.get('/materials/schedule')
async def material_schedule(user_id: UUID,
                            x_internal_secret: str | None = Header(default=None, alias='X-Internal-Secret')):
    _check_secret(x_internal_secret)
    owner = await material_sync.import_user(user_id)
    return {'user_id': str(owner), 'paused': await material_sync.paused(owner)}


@router.post("/sync")
async def trigger_sync(
    user_id: UUID,
    mode: str = "sync",
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
):
    """Persist per-file rebuild requests for one explicitly selected user."""
    _check_secret(x_internal_secret)
    return {"ok": True, "mode": mode, **await jobs_svc.enqueue_user(user_id)}


@router.post("/index-files")
async def trigger_index(
    user_id: UUID,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
):
    """Persist per-file rebuild requests without executing them in the API."""
    _check_secret(x_internal_secret)
    return {"ok": True, **await jobs_svc.enqueue_user(user_id)}


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    """Receive incoming messages from Telegram and respond to bot commands.

    Authenticated by Telegram's own `X-Telegram-Bot-Api-Secret-Token` header,
    which Telegram includes on every webhook delivery if we set it via setWebhook.

    Multi-tenant: each user runs their own bot with their own webhook secret.
    We extract the chat.id from the payload, resolve it to a user via
    `user_secrets.telegram_chat_id`, then verify the inbound header against
    that user's `telegram_webhook_secret`. No env fallback for any of the
    credentials — operator and user secrets are isolated.
    """
    body = await request.json()
    msg = body.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    # Resolve chat_id -> user_id BEFORE trusting anything in the payload.
    user_id = await user_secrets_svc.get_user_id_by_chat_id(str(chat_id))
    if user_id is None:
        log.warning("telegram webhook from unrecognised chat_id=%s", chat_id)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown telegram chat")

    # Verify the inbound secret token against THIS user's stored secret.
    secrets = await user_secrets_svc.get_secrets(user_id)
    expected = (secrets.telegram_webhook_secret or "").strip()
    if not expected or x_telegram_bot_api_secret_token != expected:
        log.warning("telegram webhook secret mismatch for user_id=%s", user_id)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="bad webhook token")

    # Stamp the resolved user onto the per-request contextvar so GUC / RLS
    # applies to any DB calls inside handle_command.
    set_current_user_id(user_id)

    reply = await telegram_svc.handle_command(text, chat_id=chat_id, user_id=user_id)

    # Send the reply using the user's own bot token. If they haven't set
    # one, the command may have completed but the user won't see a reply —
    # log a warning and move on rather than borrow another tenant's bot.
    if reply:
        token = (secrets.telegram_bot_token or "").strip()
        if token:
            await telegram_svc.send_message(token, chat_id, reply)
        else:
            log.warning("no telegram bot token for user_id=%s; dropping reply", user_id)

    return {"ok": True}
