"""Account-scoped contract for workflows importing materials through the file API."""
import os
from uuid import UUID

import httpx
from fastapi import HTTPException

from .. import db
from ..logging_config import operation


async def paused(user_id):
    return bool(await db.fetchval('SELECT paused FROM material_sync_settings WHERE user_id=%s', user_id))


async def set_paused(user_id, value):
    await db.execute(
        """INSERT INTO material_sync_settings(user_id,paused) VALUES (%s,%s)
           ON CONFLICT (user_id) DO UPDATE SET paused=EXCLUDED.paused""", user_id, value,
    )


async def import_user(raw):
    """The internal secret is administrative; every operation names its owner."""
    try:
        user_id = UUID(str(raw))
    except ValueError:
        raise HTTPException(400, 'an explicit user_id is required') from None
    if not await db.fetchval('SELECT id FROM users WHERE id=%s AND deleted_at IS NULL', user_id):
        raise HTTPException(404, 'import account not found')
    return user_id


async def trigger(user_id, course=None):
    url = os.environ.get('N8N_MATERIALS_WEBHOOK_URL', '').strip()
    if not url or os.environ.get('N8N_MATERIALS_PROTOCOL') != 'versioned-v1':
        raise HTTPException(503, 'Material import is not configured for versioned uploads')
    user_id = await import_user(user_id)
    if course and not await db.fetchval(
        'SELECT code FROM courses WHERE user_id=%s AND code=%s', user_id, course.upper(),
    ):
        raise HTTPException(400, 'unknown course')
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with operation('external.materials'):
                response = await client.post(url, json={
                    'protocol': 'versioned-v1', 'user_id': str(user_id), 'course': course,
                })
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Material import could not be confirmed') from None
    if not isinstance(result, dict) or result.get('protocol') != 'versioned-v1':
        raise HTTPException(502, 'Material workflow did not confirm the versioned upload protocol')
    if (type(result.get('written_count')) is not int or result['written_count'] < 0
            or result.get('errors')):
        raise HTTPException(502, 'Material import did not finish all requested files')
    return result
