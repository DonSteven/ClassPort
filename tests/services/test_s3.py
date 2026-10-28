"""S3 version pinning and direct-upload confirmation; no real AWS calls."""
import base64
import hashlib
from uuid import uuid4
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import boto3
from botocore.stub import Stubber
from moto import mock_aws
from moto.s3.responses import S3Response
from tests.s3_emulator import rest_checksums
import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.services import file_records as files
from app.services.file_index import index_file
from app.storage import _s3, get_storage
from app.storage.base import ObjectRef, StorageConflict, StorageUnavailable
from app.storage.s3 import S3Storage


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr(S3Response, "_get_checksum", rest_checksums())
    for name in ("AWS_PROFILE", "AWS_CONFIG_FILE", "S3_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "classport-test-materials")
    _s3.cache_clear()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(Bucket="classport-test-materials", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        client.put_bucket_versioning(Bucket="classport-test-materials", VersioningConfiguration={"Status": "Enabled"})
        yield client
    _s3.cache_clear()


def put(s3, key, data, content_type="text/plain"):
    return s3.put_object(Bucket="classport-test-materials", Key=key, Body=data,
                         ContentType=content_type,
                         ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode())["VersionId"]


@pytest.mark.parametrize("endpoint,host,path_prefix", [
    (None, "classport-test-materials.s3.us-east-2.amazonaws.com", "/objects/"),
    ("http://127.0.0.1:9000", "127.0.0.1", "/classport-test-materials/objects/"),
])
def test_signed_transfers_use_the_bucket_region_and_support_custom_endpoints(s3, endpoint, host, path_prefix):
    store = S3Storage("classport-test-materials", region="us-east-2", endpoint=endpoint)
    ref = ObjectRef("objects/presign-check")
    upload = store.upload_url(ref, file_id="file", version_id="version", content_type="text/plain",
                              sha256=hashlib.sha256(b"content").hexdigest())
    download = store.download_url(ObjectRef(ref.key, "fixed-version"), file_id="file", version_id="version")
    for target in (upload, download):
        url = urlsplit(target["url"])
        assert url.hostname == host
        assert url.path.startswith(path_prefix)


async def direct(s3, data=b"quantum mechanics", path="notes.txt", **metadata):
    target = await files.begin_upload(USER, path, size=metadata.get("size", len(data)),
        sha256=metadata.get("sha256", hashlib.sha256(data).hexdigest()), content_type="text/plain")
    row = await db.fetchrow("SELECT * FROM file_versions WHERE id=%s", target["version_id"])
    version = put(s3, row["object_key"], data)
    return target, row, version


async def test_storage_reads_checks_and_deletes_the_exact_version(s3):
    store = get_storage()
    first = await store.write(ObjectRef("objects/item"), b"first")
    latest = await store.write(ObjectRef("objects/item"), b"latest")
    assert await store.read(first) == b"first"
    info = await store.stat(first)
    assert info.ref == first and info.size == 5
    assert info.sha256 == hashlib.sha256(b"first").hexdigest()
    await store.delete(first)
    assert await store.read(latest) == b"latest"
    for method in (store.read, store.stat, store.delete):
        with pytest.raises(StorageConflict, match="fixed object version"):
            await method(ObjectRef("objects/item"))
    await store.check_readable()
    s3.put_bucket_versioning(Bucket=store.bucket, VersioningConfiguration={"Status": "Suspended"})
    with pytest.raises(StorageUnavailable, match="versioning"):
        await store.check_readable()


async def test_s3_access_denied_is_not_reported_as_missing_content(s3):
    store = S3Storage("classport-test-materials", client=s3)
    with Stubber(s3) as stub:
        stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
        with pytest.raises(StorageUnavailable):
            await store.stat(ObjectRef("objects/item", "version"))


async def test_direct_confirmation_pins_content_and_never_downloads_it(client, s3, monkeypatch):
    target, row, version = await direct(s3)
    reader = AsyncMock(side_effect=AssertionError("confirmation must not download content"))
    with monkeypatch.context() as patch:
        patch.setattr(S3Storage, "read", reader)
        first = await files.complete_upload(USER, target["file_id"], target["version_id"], version)
        repeated = await files.complete_upload(USER, target["file_id"], target["version_id"], version)
    assert first["job_id"] == repeated["job_id"]
    assert await db.fetchval("SELECT count(*) FROM jobs") == 1
    assert await db.fetchval("SELECT count(*) FROM outbox") == 1
    assert await db.fetchval("SELECT object_version FROM file_versions WHERE id=%s", target["version_id"]) == version
    replacement = put(s3, row["object_key"], b"unaccepted replacement")
    with pytest.raises(StorageConflict, match="different content"):
        await files.complete_upload(USER, target["file_id"], target["version_id"], replacement)
    assert await files.download(USER, "notes.txt") == b"quantum mechanics"
    await index_file(USER, target["file_id"], target["version_id"])
    assert "quantum mechanics" in await db.fetchval("SELECT text_content FROM file_index")
    url = await files.signed_url(USER, "notes.txt")
    assert f"versionId={version}" in url["url"]
    assert url["expires_in"] <= 300


@pytest.mark.parametrize("bad", ["size", "checksum", "version", "missing"])
async def test_confirmation_rejects_unverified_objects_without_creating_jobs(client, s3, bad):
    metadata = {"size": 99} if bad == "size" else {"sha256": "0" * 64} if bad == "checksum" else {}
    target, _, version = await direct(s3, **metadata)
    if bad == "version":
        version = put(s3, "objects/another-file", b"quantum mechanics")
    if bad == "missing":
        version = None
    with pytest.raises(StorageConflict):
        await files.complete_upload(USER, target["file_id"], target["version_id"], version)
    assert await db.fetchval("SELECT count(*) FROM jobs") == 0
    assert await db.fetchval("SELECT current_version_id FROM files WHERE id=%s", target["file_id"]) is None


async def test_direct_api_scopes_users_and_redirects_fixed_versions(client, s3):
    from app.auth import _sentinel_user, require_user
    client._transport.app.dependency_overrides[require_user] = lambda: _sentinel_user()
    data = b"quantum mechanics"
    target, row, version = await direct(s3, data)
    assert (await client.put("/api/files/upload-target", params={"file_id": target["file_id"],
        "version_id": target["version_id"]}, content=data)).status_code == 409
    done = await client.post(f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete",
                             json={"object_version": version})
    assert done.status_code == 200, done.text
    with pytest.raises(FileNotFoundError):
        await files.complete_upload(uuid4(), target["file_id"], target["version_id"], version)
    response = await client.get("/api/files/raw", params={"path": "notes.txt"}, follow_redirects=False)
    assert response.status_code == 307
    assert f"versionId={version}" in response.headers["location"]
    assert response.headers["cache-control"] == "private, no-store"
    await files.delete(USER, ["notes.txt"])
    assert (await client.get("/api/files/raw", params={"file_id": target["file_id"],
        "version_id": target["version_id"]})).status_code == 404


async def test_server_upload_also_records_the_s3_version(client, s3):
    result = await files.upload(USER, "server.txt", b"server uploaded text", "text/plain")
    assert (await files.get_version(USER, result["file_id"], result["version_id"]))["object_version"]
    assert await files.download(USER, "server.txt") == b"server uploaded text"
