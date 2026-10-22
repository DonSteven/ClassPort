"""Content storage; product paths and authorization belong to file_records."""
from .base import Storage, StorageUnavailable
from .local import LocalStorage


def get_storage(backend: str = "local") -> Storage:
    if backend != "local":
        raise StorageUnavailable("storage backend is not configured")
    return LocalStorage()
