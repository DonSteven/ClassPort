"""A separate ordinary worker process reads confirmed S3 bytes with no data root."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from moto.server import ThreadedMotoServer
from moto.s3.responses import S3Response
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app import db
from app.services import file_records as files
from app.storage import _s3, get_storage
from tests.s3_emulator import rest_checksums
from tests.test_worker_processes import roles  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


async def test_worker_process_uses_fixed_remote_version(pg_url, roles, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(S3Response, "_get_checksum", rest_checksums())
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    settings = {"STORAGE_BACKEND": "s3", "S3_BUCKET": "classport-worker-test",
        "S3_ENDPOINT_URL": f"http://{host}:{port}", "AWS_REGION": "us-east-2",
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_EC2_METADATA_DISABLED": "true"}
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    for key in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path / "missing-material-directory"))
    _s3.cache_clear()
    user = uuid4()
    try:
        store = get_storage()
        store.client.create_bucket(Bucket=store.bucket, CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        store.client.put_bucket_versioning(Bucket=store.bucket, VersioningConfiguration={"Status": "Enabled"})
        async with AsyncConnectionPool(pg_url, min_size=1, max_size=2, open=False,
                kwargs={"row_factory": dict_row}, configure=db._configure_connection) as pool:
            monkeypatch.setattr(db, "_pool", pool)
            await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", user, f"{user}@test")
            result = await files.upload(user, "notes.txt", b"confirmed quantum mechanics", "text/plain")
            row = await files.get_version(user, result["file_id"], result["version_id"])
            store.client.put_object(Bucket=store.bucket, Key=row["object_key"], Body=b"unconfirmed replacement")
            environment = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT), **settings,
                "DATABASE_URL": roles["worker"], "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "2",
                "STUDY_ROOT": str(tmp_path / "missing-material-directory")}
            execution = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-c", "from app.worker import child_start,process_job,child_stop; "
                 f"child_start(); process_job.run({str(result['job_id'])!r}); child_stop()"],
                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
            assert execution.returncode == 0, execution.stderr
            assert await db.fetchval("SELECT status FROM jobs WHERE id=%s", result["job_id"]) == "succeeded"
            assert await db.fetchval("SELECT text_content FROM file_index WHERE file_id=%s", result["file_id"]) == "confirmed quantum mechanics"
            assert not (tmp_path / "missing-material-directory").exists()
            await db.execute("DELETE FROM users WHERE id=%s", user)
    finally:
        server.stop()
        _s3.cache_clear()
