"""Private S3 objects; completed content is always addressed by VersionId."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import os
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .base import ObjectInfo, ObjectRef, StorageConflict, StorageNotFound, StorageUnavailable


class S3Storage:
    def __init__(self, bucket: str, *, region="us-east-2", endpoint=None, profile=None,
                 client=None):
        if not bucket:
            raise StorageUnavailable("S3_BUCKET is not configured")
        self.bucket = bucket
        try:
            self.session = None if client is not None else boto3.Session(profile_name=profile)
            self.client = client or self.session.client(
                "s3", region_name=region, endpoint_url=endpoint,
                config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=5,
                              retries={"total_max_attempts": 1},
                              s3={} if endpoint else {"addressing_style": "virtual"}),
            )
        except BotoCoreError:
            raise StorageUnavailable("S3 credentials are unavailable") from None

    def _params(self, ref):
        if not ref.key or not ref.version or ref.version == "null":
            raise StorageConflict("S3 content requires a fixed object version")
        return {"Bucket": self.bucket, "Key": ref.key, "VersionId": ref.version}

    async def _run(self, action, **kwargs):
        try:
            return await asyncio.to_thread(action, **kwargs)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}:
                raise StorageNotFound("stored content not found") from None
            raise StorageUnavailable("S3 storage is unavailable") from None
        except (BotoCoreError, OSError):
            raise StorageUnavailable("S3 storage is unavailable") from None

    async def read(self, ref: ObjectRef) -> bytes:
        def read(**params):
            response = self.client.get_object(**params)
            with response["Body"] as body:
                return body.read()
        return await self._run(read, **self._params(ref))

    async def write(self, ref: ObjectRef, data: bytes, *,
                    content_type: str = "application/octet-stream") -> ObjectRef:
        if ref.version is not None:
            raise StorageConflict("a fixed S3 version cannot be rewritten")
        response = await self._run(
            self.client.put_object, Bucket=self.bucket, Key=ref.key, Body=data, ContentType=content_type,
            ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
        )
        result = ObjectRef(ref.key, response.get("VersionId"))
        self._params(result)
        return result

    async def stat(self, ref: ObjectRef) -> ObjectInfo:
        response = await self._run(self.client.head_object, **self._params(ref),
                                   ChecksumMode="ENABLED")
        if response.get("VersionId") != ref.version or response.get("DeleteMarker"):
            raise StorageConflict("S3 object version differs from upload")
        checksum = response.get("ChecksumSHA256")
        try:
            digest = base64.b64decode(checksum, validate=True).hex() if checksum else None
        except (ValueError, TypeError):
            raise StorageConflict("S3 checksum is invalid") from None
        return ObjectInfo(ref, response["ContentLength"], digest, response.get("ContentType"))

    async def delete(self, ref: ObjectRef) -> None:
        await self._run(self.client.delete_object, **self._params(ref))

    async def versions(self, key: str, *, include_delete_markers=True) -> list[ObjectRef]:
        """Enumerate exact-key versions and delete markers, including pagination."""
        def listing():
            refs = []
            for page in self.client.get_paginator('list_object_versions').paginate(
                Bucket=self.bucket, Prefix=key,
            ):
                items = page.get('Versions', [])
                if include_delete_markers:
                    items = items + page.get('DeleteMarkers', [])
                for item in items:
                    if item['Key'] == key:
                        refs.append(ObjectRef(key, item['VersionId']))
            return refs
        return await self._run(lambda: listing())

    async def check_readable(self) -> None:
        response = await self._run(self.client.get_bucket_versioning, Bucket=self.bucket)
        if response.get("Status") != "Enabled":
            raise StorageUnavailable("S3 bucket versioning must be enabled")
        # An empty prefix listing works on fresh buckets without downloading content.
        await self._run(self.client.list_objects_v2, Bucket=self.bucket, Prefix="objects/", MaxKeys=1)

    def _ttl(self, requested):
        ttl = min(requested, int(os.environ.get("S3_URL_TTL_SECONDS", "300")))
        if self.session is not None:
            credentials = self.session.get_credentials()
            if credentials is None:
                raise StorageUnavailable("S3 credentials are unavailable")
            credentials.get_frozen_credentials()  # Refresh credential_process before signing.
            expiry = getattr(credentials, "_expiry_time", None)
            if expiry is not None:
                ttl = min(ttl, int((expiry - datetime.now(timezone.utc)).total_seconds()) - 30)
        if ttl < 1:
            raise StorageUnavailable("S3 credentials are expiring")
        return ttl

    def _sign(self, operation, params, requested=3600):
        try:
            ttl = self._ttl(requested)
            return {"url": self.client.generate_presigned_url(
                operation, Params=params, ExpiresIn=ttl), "expires_in": ttl}
        except (BotoCoreError, ClientError):
            raise StorageUnavailable("S3 signing is unavailable") from None

    def upload_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                   content_type: str, sha256: str | None) -> dict:
        if not sha256:
            raise StorageConflict("S3 upload requires a SHA-256 checksum")
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        target = self._sign("put_object", {
            "Bucket": self.bucket, "Key": ref.key, "ContentType": content_type,
            "ChecksumSHA256": checksum,
        })
        return {**target, "headers": {"Content-Type": content_type,
                                      "x-amz-checksum-sha256": checksum}}

    def download_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                     expires_in=3600, filename="", content_type="application/octet-stream") -> dict:
        inline = content_type in {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
        disposition = "inline" if inline else "attachment"
        return self._sign("get_object", {
            **self._params(ref), "ResponseContentType": content_type,
            "ResponseContentDisposition": f"{disposition}; filename*=utf-8''{quote(filename, safe='')}",
            "ResponseCacheControl": "private, no-store",
        }, expires_in)
