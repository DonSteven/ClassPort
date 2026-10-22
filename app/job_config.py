"""Execution limits shared by workers and database recovery."""
import os

TIME_LIMIT = int(os.environ.get("JOB_TIME_LIMIT", "120"))
MAX_ATTEMPTS = int(os.environ.get("JOB_MAX_ATTEMPTS", "3"))
LEASE_SECONDS = TIME_LIMIT + 30
REDELIVERY_SECONDS = int(os.environ.get("JOB_REDELIVERY_SECONDS", "60"))
if TIME_LIMIT < 2 or MAX_ATTEMPTS < 1 or REDELIVERY_SECONDS < 1:
    raise ValueError("Job time limit must be >= 2 and attempt/redelivery limits positive")
