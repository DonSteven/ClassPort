"""A separate ordinary worker process reads confirmed S3 bytes with no data root."""
from moto.server import ThreadedMotoServer
from moto.s3.responses import S3Response

from app import db
from app.services import file_records as files
from app.storage import _s3, get_storage
from tests.s3_emulator import rest_checksums
from tests.test_worker_processes import (  # noqa: F401
    roles, broker_container, broker, environment, coordinate, wait_status,
)

async def test_worker_process_uses_fixed_remote_version(environment, tmp_path, monkeypatch):  # noqa: F811
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
    user, process = environment
    try:
        store = get_storage()
        store.client.create_bucket(Bucket=store.bucket, CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        store.client.put_bucket_versioning(Bucket=store.bucket, VersioningConfiguration={"Status": "Enabled"})
        result = await files.upload(user, "notes.txt", b"confirmed quantum mechanics", "text/plain")
        row = await files.get_version(user, result["file_id"], result["version_id"])
        store.client.put_object(Bucket=store.bucket, Key=row["object_key"], Body=b"unconfirmed replacement")
        await coordinate(process, "dispatch")
        with process("worker", settings={**settings, "STUDY_ROOT": str(tmp_path / "missing-material-directory")}):
            await wait_status(user, result["job_id"])
        assert await db.fetchval("SELECT text_content FROM file_index WHERE file_id=%s", result["file_id"]) == "confirmed quantum mechanics"
        assert not (tmp_path / "missing-material-directory").exists()
    finally:
        server.stop()
        _s3.cache_clear()
