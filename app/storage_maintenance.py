"""Reconcile material cleanup in bounded batches using the application identity."""
import argparse
import asyncio
import logging
from pathlib import Path
import signal

from . import db
from .logging_config import configure_logging
from .services.storage_cleanup import scan_user

log = logging.getLogger(__name__)


async def main(*, once=False):
    await db.init_pool()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            users = await db.fetch('SELECT id FROM users WHERE deleted_at IS NULL ORDER BY id')
            for user in users:
                if stop.is_set():
                    break
                try:
                    await scan_user(user['id'])
                except Exception:
                    log.exception('storage.reconciliation.failed', extra={'user_id': user['id']})
                    if once:
                        raise
            Path('/tmp/classport-storage-maintenance.heartbeat').touch()
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), 60)
            except TimeoutError:
                pass
    finally:
        await db.close_pool()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-users', action='store_true', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    configure_logging()
    asyncio.run(main(once=args.once))
