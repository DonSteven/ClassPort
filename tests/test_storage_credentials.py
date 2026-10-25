"""Host publication, restricted credential_process and SDK refresh without restart."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import boto3
import pytest

from scripts.storage_credential_process import credentials

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("storage_rotation", ROOT / "infra/aws/scripts/storage_credentials.py")
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)


def grant(key, expires):
    return {"Version": 1, "AccessKeyId": key, "SecretAccessKey": "test-secret",
            "SessionToken": "test-token", "Expiration": expires.isoformat()}


def test_host_publishes_only_storage_grant_atomically(tmp_path, monkeypatch):
    data = grant("first", datetime.now(timezone.utc) + timedelta(hours=1))
    monkeypatch.setenv("STORAGE_ROLE_ARN", "arn:aws:iam::123456789012:role/materials")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    result = {"Credentials": {k: v for k, v in data.items() if k != "Version"}}
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(result)))
    monkeypatch.setattr(rotation.subprocess, "run", run)
    directory = tmp_path / "grant"
    rotation.refresh(directory)
    assert credentials(directory / "credentials.json") == data
    assert (directory / "credentials.json").stat().st_mode & 0o777 == 0o400
    assert {p.name for p in directory.iterdir()} == {"config", "credentials.json"}
    assert "assume-role" in run.call_args.args[0]
    run.side_effect = OSError("temporary failure")
    with pytest.raises(OSError):
        rotation.refresh(directory)
    assert credentials(directory / "credentials.json") == data


def test_sdk_rereads_rotated_directory_without_recreating_client(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    path = tmp_path / "credentials.json"
    rotation.atomic_write(path, json.dumps(grant("first", now + timedelta(hours=1))))
    # This process is the same reader used in the application container.
    reader = tmp_path / "reader.py"
    reader.write_text(f"import sys,json; sys.path.insert(0,{str(ROOT)!r}); "
                      f"from scripts.storage_credential_process import credentials; "
                      f"from pathlib import Path; print(json.dumps(credentials(Path({str(path)!r}))))")
    config = tmp_path / "config"
    config.write_text(f"[profile storage-test]\ncredential_process = {sys.executable} {reader}\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    session = boto3.Session(profile_name="storage-test")
    sdk_credentials = session.get_credentials()
    assert sdk_credentials.method == "custom-process"
    assert sdk_credentials.get_frozen_credentials().access_key == "first"
    rotation.atomic_write(path, json.dumps(grant("second", now + timedelta(hours=2))))
    # Advance only the SDK's refresh clock; no wall-clock wait or process restart.
    sdk_credentials._time_fetcher = lambda: now + timedelta(minutes=55)
    assert session.get_credentials() is sdk_credentials
    assert sdk_credentials.get_frozen_credentials().access_key == "second"
