"""Retention boundaries for versioned materials, expressed in seconds."""
import os


def retention_seconds() -> int:
    value = int(os.environ.get('STORAGE_RETENTION_SECONDS', '604800'))
    if value < 0:
        raise ValueError('STORAGE_RETENTION_SECONDS must be nonnegative')
    return value


def upload_grace_seconds() -> int:
    # A PUT accepted before its signature expires may still be transferring.
    value = int(os.environ.get('STORAGE_UPLOAD_GRACE_SECONDS', '3600'))
    if value < 1:
        raise ValueError('STORAGE_UPLOAD_GRACE_SECONDS must be positive')
    return value
