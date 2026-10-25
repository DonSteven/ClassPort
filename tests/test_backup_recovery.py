"""Backup publication and recovery boundaries, without AWS access."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recovery", REPO / "infra/aws/scripts/recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "source"
    release = root / "releases/version"
    release.mkdir(parents=True)
    (root / "releases/current").symlink_to(release)
    (root / "data/courses/user/empty").mkdir(parents=True)
    (root / "data/courses/user/empty/.keep").touch()
    (root / "data/courses/user/sample.pdf").write_bytes(b"PDF data")
    for name in recovery.RELEASE_FILES:
        (release / name).write_text("release helper")
    (release / "release.env").write_text("RELEASE_COMMIT=" + "a" * 40 + "\nBACKEND_IMAGE=backend@sha256:" + "b" * 64 + "\nFRONTEND_IMAGE=frontend@sha256:" + "c" * 64 + "\n")
    settings = {"CONFIG_SECRET_ARN": "application", "DB_SECRET_ARN": "source-admin", "BACKUP_BUCKET": "backups",
                "DB_HOST": "source-db", "DB_NAME": "study", "PUBLIC_URL": "https://study.example.test"}
    state = {"migrations": [{"filename": "baseline", "checksum": "existing"}], "users": 1, "courses": 1,
             "tasks": 1, "file_index": 1, "references": ["user/sample.pdf"]}
    objects = {}
    calls = []
    config = {"MIGRATION_DB_PASSWORD": "not-for-logs", "SMOKE_MCP_TOKEN": "verification-token"}

    def aws(*args):
        calls.append(args)
        if args[0] == "secretsmanager":
            return {"VersionId": "original-secret-version", "SecretString": json.dumps(config)}
        if args[1] == "put-object":
            objects[args[args.index("--key") + 1]] = Path(args[args.index("--body") + 1]).read_bytes()
            return {"VersionId": "manifest-version"}
        raise AssertionError(args)

    def upload(path, bucket, key):
        calls.append(("upload", path.name))
        objects[key] = path.read_bytes()
        return {"key": key, "version_id": "object-version", "bytes": path.stat().st_size}

    def download(bucket, record, path):
        path.write_bytes(objects[record["key"]])

    def run(args):
        calls.append(tuple(str(x) for x in args))
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{"State": {"Running": False, "ExitCode": 0}}] * 2)
        return ""

    def compose(root, release, *args):
        calls.append(args)
        if args[0] == "ps":
            return args[-1]
        return ""

    def pg(work, root, config, settings, *args, **kwargs):
        calls.append(args)
        if args[0] == "pg_dump":
            (work / "database.dump").write_bytes(b"consistent database dump")
        elif args[0] == "psql":
            return "0"

    monkeypatch.setattr(recovery, "aws", aws)
    monkeypatch.setattr(recovery, "upload", upload)
    monkeypatch.setattr(recovery, "download", download)
    monkeypatch.setattr(recovery, "run", run)
    monkeypatch.setattr(recovery, "compose", compose)
    monkeypatch.setattr(recovery, "pg", pg)
    monkeypatch.setattr(recovery, "database_state", lambda *args: state)
    monkeypatch.setattr(recovery, "reject_other_writers", lambda *args: None)
    monkeypatch.setattr(recovery, "login_images", lambda *args: None)
    monkeypatch.setattr(recovery, "phase", lambda *args: calls.append(("phase", args[-1])))
    return root, settings, objects, calls


def target_args(tmp_path, success):
    root = tmp_path / "target"
    (root / "data").mkdir(parents=True)
    (root / "certs").mkdir()
    (root / "certs/global-bundle.pem").touch()
    return argparse.Namespace(target_root=root, target_host="isolated-db", target_database="study",
                              target_db_secret_arn="isolated-admin", public_url="https://restore.example.test",
                              backup_bucket="backups", manifest_key=success["key"],
                              manifest_version=success["version_id"], pdf_path="sample.pdf")


def test_archive_round_trip_keeps_hidden_files_versions_and_original_secret(host, tmp_path):
    root, settings, objects, calls = host
    result = recovery.backup(root, settings)
    manifest = json.loads(objects[result["key"]])
    assert manifest["configuration"]["version_id"] == "original-secret-version"
    assert manifest["files"] == {"user/empty/.keep": 0, "user/sample.pdf": 8}
    assert "not-for-logs" not in json.dumps(manifest)
    assert calls.index(("stop", "--timeout", "300", *recovery.WRITERS)) < next(i for i, call in enumerate(calls) if call[0] == "pg_dump")
    assert calls[-1][:2] == ("up", "-d")
    args = target_args(tmp_path, result)
    report = recovery.restore(root, settings, args)
    assert report["status"] == "passed"
    assert (args.target_root / "data/courses/user/sample.pdf").read_bytes() == b"PDF data"
    assert (args.target_root / "data/courses/user/empty/.keep").is_file()
    assert ("phase", "roles") in calls and ("phase", "configure") in calls
    assert "mcp" in report["checks"]
    assert report["potential_data_loss_window"]["after"] == result["consistent_at"]


def test_failed_upload_resumes_services_without_publishing_manifest(host, monkeypatch):
    root, settings, objects, calls = host
    monkeypatch.setattr(recovery, "upload", lambda *args: (_ for _ in ()).throw(OSError("upload failed")))
    with pytest.raises(OSError):
        recovery.backup(root, settings)
    assert calls[-1][:2] == ("up", "-d")
    assert not objects and not (root / "last-backup.json").exists()


@pytest.mark.parametrize("failure", ["source", "nonempty", "database"])
def test_restore_refuses_source_or_nonempty_targets(host, tmp_path, monkeypatch, failure):
    root, settings, objects, calls = host
    success = recovery.backup(root, settings)
    args = target_args(tmp_path, success)
    if failure == "source":
        args.target_host = "SOURCE-DB"
    elif failure == "nonempty":
        (args.target_root / "data/important.pdf").write_text("preserve me")
    else:
        monkeypatch.setattr(recovery, "pg", lambda *args, **kwargs: "1")
    calls.clear()
    with pytest.raises(ValueError):
        recovery.restore(root, settings, args)
    assert not any(call[0] == "phase" for call in calls)
    assert not (args.target_root / "releases").exists()


def test_missing_reference_and_unsafe_archive_fail_before_overwriting(tmp_path):
    with pytest.raises(ValueError, match="reference"):
        recovery.check_files(tmp_path, {"references": ["missing.pdf"]})
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as output:
        item = tarfile.TarInfo("../outside")
        item.size = 1
        output.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(tarfile.FilterError):
        recovery.extract(archive, tmp_path / "restore")
    assert not (tmp_path / "outside").exists()


def test_monitor_reports_backup_failure_and_age(host, monkeypatch):
    root, settings, objects, calls = host
    settings["ENVIRONMENT"] = "test"
    recovery.save_json(root / "last-backup-attempt.json", {"status": "failed"})
    monkeypatch.setattr(recovery, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(recovery, "aws", lambda *args: calls.append(args))
    recovery.monitor(root, settings)
    metrics = {item["MetricName"]: item["Value"] for item in json.loads(calls[-1][-1])}
    assert metrics["APIReady"] == 0 and metrics["BackupFailed"] == 1


def test_monitor_reports_task_metrics_through_the_coordinator(host, monkeypatch):
    root, settings, objects, calls = host
    settings["ENVIRONMENT"] = "test"
    monkeypatch.setattr(recovery, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(recovery, "compose", lambda *a: json.dumps({"JobWaitingAgeSeconds": 42, "JobExpiredLeases": 1}))
    monkeypatch.setattr(recovery, "aws", lambda *a: calls.append(a))
    recovery.monitor(root, settings)
    metrics = {item["MetricName"]: item["Value"] for item in json.loads(calls[-1][-1])}
    assert metrics["JobMetricsReady"] == 1 and metrics["JobWaitingAgeSeconds"] == 42
    assert metrics["JobExpiredLeases"] == 1
    assert metrics["BackupAgeSeconds"] > 93600


def test_s3_backup_keeps_independent_copy_and_restore_uses_new_version(host, tmp_path, monkeypatch):
    import hashlib
    root, settings, objects, calls = host
    data = b"confirmed remote PDF bytes"
    reference = {"id": "00000000-0000-0000-0000-000000000123", "key": "objects/user/file/version/content",
                 "version_id": "source-version", "bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest(), "content_type": "application/pdf"}
    settings.update(S3_BUCKET="source-materials")
    state = {"s3_references": [reference]}
    operations = []

    def material(*args):
        operations.append(args)
        if args[0] == "get-object":
            assert args[args.index("--version-id") + 1] == "source-version"
            Path(args[-1]).write_bytes(data)
            return {}
        assert args[0] == "put-object"
        assert args[args.index("--bucket") + 1] == "target-materials"
        assert Path(args[args.index("--body") + 1]).read_bytes() == data
        return {"VersionId": "new-target-version"}

    monkeypatch.setattr(recovery, "material_aws", material)
    records = recovery.backup_materials(tmp_path, settings, state, "backups/snapshot/")
    assert records[0]["backup"]["version_id"] == "object-version"
    assert objects[records[0]["backup"]["key"]] == data
    recovery.restore_materials(tmp_path, root, {}, {**settings, "S3_BUCKET": "target-materials"}, records)
    mappings = json.loads((tmp_path / "material-mappings.json").read_text())
    assert mappings == [{"id": reference["id"], "object_version": "new-target-version"}]
    assert "\\copy material_mapping" in (tmp_path / "material-mappings.sql").read_text()
    assert not (tmp_path / "material-content").exists()


def test_s3_backup_rejects_missing_or_changed_confirmed_content(host, tmp_path, monkeypatch):
    root, settings, objects, calls = host
    reference = {"id": "file", "key": "objects/content", "version_id": "fixed", "bytes": 3,
                 "sha256": "0" * 64, "content_type": "text/plain"}
    monkeypatch.setattr(recovery, "material_aws", lambda *args: Path(args[-1]).write_bytes(b"bad"))
    with pytest.raises(ValueError, match="confirmed version"):
        recovery.backup_materials(tmp_path, {**settings, "S3_BUCKET": "materials"},
                                  {"s3_references": [reference]}, "backups/snapshot/")
    assert not objects


def test_restore_remaps_s3_version_with_real_psql(pg_url, tmp_path, monkeypatch):
    """Execute the restore mapping script against a real migrated database."""
    import hashlib
    import subprocess
    from uuid import uuid4
    import psycopg

    file_id, version_id = uuid4(), uuid4()
    owner = "00000000-0000-0000-0000-000000000001"
    with psycopg.connect(pg_url) as connection:
        connection.execute("INSERT INTO files(id,user_id,logical_path) VALUES (%s,%s,%s)",
                           (file_id, owner, f"restore-{file_id}.txt"))
        connection.execute("""INSERT INTO file_versions(id,user_id,file_id,revision,object_key,
                           storage_backend,object_version,upload_status)
                           VALUES (%s,%s,%s,1,'objects/restore','s3','source-version','completed')""",
                           (version_id, owner, file_id))
    content = b"independent backup copy"
    record = {"id": str(version_id), "key": "objects/restore", "version_id": "source-version",
              "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
              "content_type": "text/plain", "backup": {"key": "backups/copy", "version_id": "backup-version"}}
    monkeypatch.setattr(recovery, "download", lambda bucket, reference, path: path.write_bytes(content))
    monkeypatch.setattr(recovery, "material_aws", lambda *args: {"VersionId": "new-target-version"})

    def psql(work, root, config, settings, *args):
        subprocess.run(["docker", "run", "--rm", "--network", "host", "-v", f"{work}:/backup:ro",
                        recovery.PG_IMAGE, "psql", pg_url, *args[1:]],
                       check=True, capture_output=True, text=True, timeout=30)
    monkeypatch.setattr(recovery, "pg", psql)
    try:
        recovery.restore_materials(tmp_path, tmp_path, {}, {"S3_BUCKET": "target", "BACKUP_BUCKET": "backups"}, [record])
        with psycopg.connect(pg_url) as connection:
            assert connection.execute("SELECT object_version FROM file_versions WHERE id=%s", (version_id,)).fetchone()[0] == "new-target-version"
    finally:
        with psycopg.connect(pg_url) as connection:
            connection.execute("DELETE FROM files WHERE id=%s", (file_id,))
