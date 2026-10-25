"""Content storage; product paths and authorization belong to file_records."""
import os
from functools import lru_cache

from .base import Storage, StorageUnavailable
from .local import LocalStorage


@lru_cache
def _s3(bucket, region, endpoint, profile, config_file):
    from .s3 import S3Storage
    return S3Storage(bucket, region=region, endpoint=endpoint, profile=profile)


def get_storage(backend: str | None = None) -> Storage:
    backend = backend or os.environ.get("STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalStorage()
    if backend == "s3":
        return _s3(os.environ.get("S3_BUCKET", ""), os.environ.get("AWS_REGION", "us-east-2"),
                   os.environ.get("S3_ENDPOINT_URL"), os.environ.get("AWS_PROFILE"),
                   os.environ.get("AWS_CONFIG_FILE"))
    raise StorageUnavailable("storage backend is not configured")
