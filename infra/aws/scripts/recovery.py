"""Host-side, versioned backup/restore/monitor commands (Python standard library).

Cloud commands are deliberately separate from application release. Run as root;
credentials exist only in a private temporary directory and maintenance containers.
"""
import argparse
import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4


PG_IMAGE = "postgres:16-alpine"
CURRENT_STAGE = "preflight"
RELEASE_FILES = ("release.env", "docker-compose.prod.yml", "deploy-prod.sh", "bootstrap.py", "recovery.py", "storage_credentials.py")
WRITERS = ("classport", "frontend", "worker", "dispatcher", "job-recovery", "storage-maintenance")
STATE_SQL = """SELECT json_build_object(
 'migrations', (SELECT json_agg(m ORDER BY filename) FROM public._migrations m),
 'users', (SELECT count(*) FROM users), 'courses', (SELECT count(*) FROM courses),
 'tasks', (SELECT count(*) FROM tasks), 'file_index', (SELECT count(*) FROM file_index),
 'files', (SELECT count(*) FROM files), 'file_versions', (SELECT count(*) FROM file_versions),
 'jobs', (SELECT count(*) FROM jobs), 'job_attempts', (SELECT count(*) FROM job_attempts),
 'references', (SELECT coalesce(json_agg(object_key ORDER BY object_key), '[]')
                FROM file_versions WHERE storage_backend = 'local' AND upload_status = 'completed'
                    AND content_state = 'available'),
 's3_references', (SELECT coalesce(json_agg(json_build_object(
       'id', id, 'key', object_key, 'version_id', object_version, 'bytes', size,
       'sha256', sha256, 'content_type', content_type) ORDER BY id), '[]')
       FROM file_versions WHERE storage_backend = 's3' AND upload_status = 'completed'
           AND content_state = 'available'))"""

# Source-environment physical targets cannot be replayed in a restored bucket.
RESET_STORAGE_SQL = """
DELETE FROM jobs WHERE job_type='storage_cleanup';
DELETE FROM storage_cleanup;
DELETE FROM storage_migrations;
UPDATE file_versions SET content_state='purged' WHERE content_state='retiring';
"""


def progress(stage):
    global CURRENT_STAGE
    CURRENT_STAGE = stage
    print(json.dumps({"event": "maintenance.stage", "stage": stage}), flush=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def run(args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if result.returncode:
        # CLI diagnostics may include connection strings, passwords or signed URLs.
        raise RuntimeError("Maintenance command failed; command output omitted")
    return result.stdout.strip()


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def aws(*args):
    return json.loads(run(["aws", *args, "--output", "json"]) or "{}")


def material_aws(*args):
    # Read the current atomic grant for each operation; only the child gets it.
    grant = json.loads(Path("/run/classport-storage/credentials.json").read_text())
    result = subprocess.run(["aws", "s3api", *map(str, args), "--output", "json"],
        env={**os.environ, "AWS_ACCESS_KEY_ID": grant["AccessKeyId"],
             "AWS_SECRET_ACCESS_KEY": grant["SecretAccessKey"], "AWS_SESSION_TOKEN": grant["SessionToken"]},
        capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Material backup/restore operation failed; output omitted")
    return json.loads(result.stdout or "{}")


def verify_material(path, reference):
    with path.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    if path.stat().st_size != reference["bytes"] or digest != reference["sha256"]:
        raise ValueError("Material backup differs from its confirmed version")


def backup_materials(work, settings, state, prefix):
    records = []
    for reference in state.get("s3_references", []):
        path = work / "material-content"
        material_aws("get-object", "--bucket", settings["S3_BUCKET"], "--key", reference["key"],
                     "--version-id", reference["version_id"], path)
        verify_material(path, reference)
        copied = upload(path, settings["BACKUP_BUCKET"], prefix + "materials/" + reference["id"])
        records.append({**reference, "backup": copied})
        path.unlink()
    return records


def restore_materials(work, root, config, settings, records):
    mappings = []
    for record in records:
        path = work / "material-content"
        download(settings["BACKUP_BUCKET"], record["backup"], path)
        verify_material(path, record)
        result = material_aws("put-object", "--bucket", settings["S3_BUCKET"], "--key", record["key"],
            "--body", path, "--content-type", record["content_type"], "--checksum-algorithm", "SHA256",
            "--checksum-sha256", base64.b64encode(bytes.fromhex(record["sha256"])).decode())
        version = result.get("VersionId")
        if not version or version == "null":
            raise ValueError("Restore target must have S3 versioning enabled")
        mappings.append({"id": record["id"], "object_version": version})
        path.unlink()
    if mappings:
        (work / "material-mappings.json").write_text(json.dumps(mappings) + "\n")
        # psql reads the JSON inside its mounted /backup directory, not on RDS.
        (work / "material-mappings.sql").write_text(r"""BEGIN;
CREATE TEMP TABLE material_mapping (payload jsonb);
\copy material_mapping FROM '/backup/material-mappings.json' WITH (FORMAT csv, DELIMITER E'\t', QUOTE E'\x01')
UPDATE file_versions v SET object_version = m.object_version
FROM material_mapping, jsonb_to_recordset(payload) AS m(id uuid, object_version text)
WHERE v.id = m.id AND v.storage_backend = 's3';
COMMIT;
""")
        pg(work, root, config, settings, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-f", "/backup/material-mappings.sql")


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def write_private(path, values):
    if any("\n" in str(value) or "\r" in str(value) for value in values.values()):
        raise ValueError("Multiline configuration is unsupported")
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


@contextmanager
def locks(root):
    # Same order as release.sh then deploy-prod.sh, including manual rollback.
    with (root / "aws-release.lock").open("a") as outer, (root / "releases/deploy.lock").open("a") as inner:
        fcntl.flock(outer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(inner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def compose(root, release, *args):
    return run(["docker", "compose", "--project-name", "classport-prod", "--project-directory", root,
                "--env-file", release / "release.env", "-f", release / "docker-compose.prod.yml", *args])


def pg(work, root, config, settings, *args, admin=None):
    write_private(work / "pg.env", {
        "PGHOST": settings["DB_HOST"], "PGPORT": settings.get("DB_PORT", "5432"),
        "PGDATABASE": settings["DB_NAME"],
        "PGUSER": admin["username"] if admin else "classport_migrator",
        "PGPASSWORD": admin["password"] if admin else config["MIGRATION_DB_PASSWORD"],
        "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/certs/global-bundle.pem",
        "PGCONNECT_TIMEOUT": "10",
    })
    container = "classport-pg-" + uuid4().hex
    try:
        return run(["docker", "run", "--rm", "--network", "host", "--log-driver", "local",
                "--name", container, "--env-file", work / "pg.env", "-v", f"{work}:/backup",
                "-v", f"{root / 'certs'}:/certs:ro", PG_IMAGE, *args])
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)



def database_state(work, root, config, settings):
    return json.loads(pg(work, root, config, settings, "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-c", STATE_SQL))


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Course backups require regular files/directories, without symbolic links")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path.stat().st_size
        elif not path.is_dir():
            raise ValueError("Unsupported file type in course directory")
    return files


def check_files(directory, state, expected=None):
    actual = inventory(directory)
    if expected is not None and actual != expected:
        raise ValueError("Restored course tree differs from the backup inventory")
    for relative in state["references"]:
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("A database file reference is missing from the course tree")
    return actual


def upload(path, bucket, key):
    # Multipart uploads also support archives larger than S3's single-PUT limit.
    transfer = aws("s3api", "create-multipart-upload", "--bucket", bucket, "--key", key)["UploadId"]
    parts = []
    try:
        with path.open("rb") as source, tempfile.TemporaryDirectory(dir=path.parent) as temporary:
            part = Path(temporary) / "part"
            while chunk := source.read(64 * 1024 * 1024):
                part.write_bytes(chunk)
                number = len(parts) + 1
                result = aws("s3api", "upload-part", "--bucket", bucket, "--key", key,
                             "--upload-id", transfer, "--part-number", str(number), "--body", str(part))
                parts.append({"ETag": result["ETag"], "PartNumber": number})
            listing = Path(temporary) / "parts.json"
            listing.write_text(json.dumps({"Parts": parts}))
            result = aws("s3api", "complete-multipart-upload", "--bucket", bucket, "--key", key,
                         "--upload-id", transfer, "--multipart-upload", f"file://{listing}")
        return {"key": key, "version_id": result["VersionId"], "bytes": path.stat().st_size}
    except BaseException:
        aws("s3api", "abort-multipart-upload", "--bucket", bucket, "--key", key, "--upload-id", transfer)
        raise


def download(bucket, record, path):
    aws("s3api", "get-object", "--bucket", bucket, "--key", record["key"],
        "--version-id", record["version_id"], str(path))
    if "bytes" in record and path.stat().st_size != record["bytes"]:
        raise ValueError("Backup object size differs from its manifest")


def extract(archive, target):
    with tarfile.open(archive) as source:
        source.extractall(target, filter="data")


def reject_other_writers(root, allowed):
    containers = run(["docker", "ps", "-q"]).split()
    if not containers:
        return
    course_root = (root / "data/courses").resolve()
    for container in json.loads(run(["docker", "inspect", *containers])):
        if container["Id"] in allowed:
            continue
        for mount in container.get("Mounts", []):
            source = Path(mount["Source"]).resolve()
            if mount.get("RW") and (source.is_relative_to(course_root) or course_root.is_relative_to(source)):
                raise ValueError("Another running container can write course files; stop it before backup")


def backup(root, settings):
    global CURRENT_STAGE
    with locks(root), tempfile.TemporaryDirectory(prefix="classport-backup-", dir=root / "data") as temporary:
        work = Path(temporary)
        work.chmod(0o700)
        release = (root / "releases/current").resolve(strict=True)
        version = read_env(release / "release.env")
        if not all((release / name).is_file() for name in RELEASE_FILES):
            raise ValueError("Current release lacks recovery helpers; publish the A8 release first")
        run(["mountpoint", "-q", root / "data"])
        run(["docker", "pull", PG_IMAGE])
        secret = aws("secretsmanager", "get-secret-value", "--secret-id", settings["CONFIG_SECRET_ARN"])
        config = json.loads(secret["SecretString"])
        services = [compose(root, release, "ps", "-q", service) for service in WRITERS]
        if not all(services):
            raise ValueError("Application and background services must be running before scheduled backup")
        reject_other_writers(root, set(services))
        manifest = {"format": 1, "backup_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex,
                    "started_at": now(), "release": version,
                    "database": {"host": settings["DB_HOST"], "name": settings["DB_NAME"], "major": 16, "admin_secret_arn": settings["DB_SECRET_ARN"]},
                    "configuration": {"secret_arn": settings["CONFIG_SECRET_ARN"], "version_id": secret["VersionId"]}}
        stopped = False
        try:
            progress("backup.drain")
            stopped = True
            compose(root, release, "stop", "--timeout", "300", *WRITERS)
            states = json.loads(run(["docker", "inspect", *services]))
            for item in states:
                state = item["State"]
                service = item.get("Config", {}).get("Labels", {}).get("com.docker.compose.service")
                # Uvicorn re-raises SIGTERM after completing graceful shutdown.
                expected_exits = {0, 143} if service == "classport" else {0}
                if state["Running"] or state["ExitCode"] not in expected_exits:
                    raise ValueError("Application did not drain cleanly; no consistent backup will be published")
            manifest["consistent_at"] = now()
            progress("backup.validate")
            manifest["database_state"] = database_state(work, root, config, settings)
            manifest["files"] = check_files(root / "data/courses", manifest["database_state"])
            progress("backup.export")
            pg(work, root, config, settings, "pg_dump", "--format=custom", "--file=/backup/database.dump")
            with tarfile.open(work / "courses.tar.gz", "w:gz") as archive:
                archive.add(root / "data/courses", arcname=".")
            with tarfile.open(work / "release.tar.gz", "w:gz") as archive:
                for name in RELEASE_FILES:
                    archive.add(release / name, arcname=name)
            progress("backup.upload")
            prefix = "backups/" + manifest["backup_id"] + "/"
            manifest["materials"] = backup_materials(work, settings, manifest["database_state"], prefix)
            manifest["objects"] = {name: upload(work / name, settings["BACKUP_BUCKET"], prefix + name)
                                   for name in ("database.dump", "courses.tar.gz", "release.tar.gz")}
            manifest["finished_at"] = now()
            (work / "manifest.json").write_text(json.dumps(manifest))
            record = aws("s3api", "put-object", "--bucket", settings["BACKUP_BUCKET"],
                         "--key", prefix + "manifest.json", "--body", str(work / "manifest.json"))
            success = {"completed_at": now(), "consistent_at": manifest["consistent_at"],
                       "key": prefix + "manifest.json", "version_id": record["VersionId"]}
            save_json(root / "last-backup.json", success)
            return success
        finally:
            if stopped:
                failed_stage = CURRENT_STAGE
                progress("backup.resume")
                compose(root, release, "up", "-d", "--wait", "--wait-timeout", "90", *WRITERS)
                CURRENT_STAGE = failed_stage


def phase(work, root, config, master, settings, backend, name):
    (work / "application.json").write_text(json.dumps(config))
    (work / "database.json").write_text(json.dumps(master))
    write_private(work / "maintenance.env", {key: settings[key] for key in ("DB_HOST", "DB_NAME", "PUBLIC_URL")}
                  | {"DB_PORT": settings.get("DB_PORT", "5432"),
                     **{key: settings[key] for key in ("S3_BUCKET", "AWS_REGION") if key in settings}})
    run(["docker", "run", "--rm", "--network", "host", "--log-driver", "local",
         "--env-file", work / "maintenance.env", "-v", f"{work}:/run/secrets:ro",
         "-v", f"{root}:/deployment", "-v", f"{root / 'certs'}:/etc/classport/certs:ro",
         backend, "uv", "run", "--no-sync", "python", "/app/release/bootstrap.py", "--recovery-phase", name])


def login_images(version):
    registries = {version[key].split("/")[0] for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE")}
    for registry in registries:
        password = run(["aws", "ecr", "get-login-password", "--region", registry.split(".")[3]])
        result = subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry],
                                input=password, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("Recovery image registry login failed")


def restore(root, settings, args):
    started = now()
    root = args.target_root.resolve()
    if root.exists() and any(item.name not in {"data", "certs", ".host-ready"} for item in root.iterdir()):
        raise ValueError("Restore requires an unused target deployment directory")
    if (root / "data").exists() and any(p.is_file() or p.is_symlink() for p in (root / "data").rglob("*")):
        raise ValueError("Restore requires an empty target data directory")
    # The target host is provisioned separately, with its own mounted EBS and CA.
    run(["mountpoint", "-q", root / "data"])
    if not (root / "certs/global-bundle.pem").is_file():
        raise ValueError("Target RDS CA bundle is missing")
    with tempfile.TemporaryDirectory(prefix="classport-restore-") as temporary:
        work = Path(temporary)
        download(args.backup_bucket, {"key": args.manifest_key, "version_id": args.manifest_version}, work / "manifest.json")
        manifest = json.loads((work / "manifest.json").read_text())
        if (manifest["format"] != 1 or args.target_host.lower() == manifest["database"]["host"].lower()
                or args.target_db_secret_arn == manifest["database"]["admin_secret_arn"]):
            raise ValueError("Restore requires a separate PostgreSQL instance")
        target = {**settings, "DB_HOST": args.target_host, "DB_NAME": args.target_database,
                  "PUBLIC_URL": args.public_url, "BACKUP_BUCKET": args.backup_bucket}
        secret = manifest["configuration"]
        config = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", secret["secret_arn"],
                               "--version-id", secret["version_id"])["SecretString"])
        master = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", args.target_db_secret_arn)["SecretString"])
        run(["docker", "pull", PG_IMAGE])
        empty = pg(work, root, config, target, "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-c",
                   "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                   "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
                   "AND c.relkind IN ('r','p','S','v','m')", admin=master)
        if empty != "0":
            raise ValueError("Restore requires an empty target database")
        for name in ("database.dump", "courses.tar.gz", "release.tar.gz"):
            download(args.backup_bucket, manifest["objects"][name], work / name)
        (root / ".restore-started").touch(exist_ok=False)
        release = root / "releases" / manifest["backup_id"]
        release.mkdir(parents=True)
        extract(work / "release.tar.gz", release)
        if read_env(release / "release.env") != manifest["release"]:
            raise ValueError("Release archive does not match the backup manifest")
        version = manifest["release"]
        for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE"):
            if "@sha256:" not in version[key]:
                raise ValueError("Recovery requires pinned image digests")
        progress("restore.images")
        login_images(version)
        for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE"):
            run(["docker", "pull", version[key]])
        progress("restore.database")
        phase(work, root, config, master, target, version["BACKEND_IMAGE"], "roles")
        pg(work, root, config, target, "pg_restore", "--exit-on-error", "--single-transaction",
           "--no-owner", "--clean", "--if-exists", "--dbname", args.target_database, "/backup/database.dump")
        progress("restore.files")
        courses = root / "data/courses"
        courses.mkdir(exist_ok=True)
        extract(work / "courses.tar.gz", courses)
        state = database_state(work, root, config, target)
        if state != manifest["database_state"]:
            raise ValueError("Restored database counts, references or migration records differ")
        check_files(courses, state, manifest["files"])
        if manifest.get("materials"):
            if not target.get("S3_BUCKET") or not target.get("STORAGE_ROLE_ARN"):
                raise ValueError("S3 restore requires the target material bucket and restricted role")
            subprocess.run(["python3", str(release / "storage_credentials.py"), "install"],
                           env={**os.environ, **target}, check=True)
            restore_materials(work, root, config, target, manifest["materials"])
        # Physical deletion targets belong to the source environment. Reconcile
        # fresh targets after restore rather than replaying source VersionIds.
        pg(work, root, config, target, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-c",
           'BEGIN;\n' + RESET_STORAGE_SQL + '\nCOMMIT;')
        progress("restore.configuration")
        phase(work, root, config, master, target, version["BACKEND_IMAGE"], "configure")
        (root / "releases/current").symlink_to(release)
        (root / "data/redis").mkdir(exist_ok=False)
        # This is a new target with a fresh Redis directory. Recover database
        # executions and recreate notifications before any background consumer starts.
        compose(root, release, "run", "--rm", "--no-deps", "job-recovery",
                "uv", "run", "--no-sync", "python", "-m", "app.job_coordination", "restore", "--once")
        compose(root, release, "up", "-d", "--wait", "--wait-timeout", "90", "redis", *WRITERS)
        progress("restore.verify")
        checks = dict(config)
        credentials_path = getattr(args, "check_credentials_file", None)
        if credentials_path:
            supplied = json.loads(credentials_path.read_text())
            if set(supplied) - {"OPERATOR_EMAIL", "OPERATOR_PASSWORD", "SMOKE_EMAIL", "SMOKE_PASSWORD", "SMOKE_MCP_TOKEN"}:
                raise ValueError("Check credentials may only override verification account credentials")
            checks.update(supplied)
        (work / "application.json").write_text(json.dumps(checks))
        write_private(work / "verify.env", {"RESTORE_PDF_PATH": args.pdf_path, "SMOKE_MCP_TOKEN": checks["SMOKE_MCP_TOKEN"]})
        run(["docker", "run", "--rm", "--network", "host", "--log-driver", "local",
             "--env-file", root / ".env", "--env-file", root / ".env.smoke", "--env-file", work / "verify.env",
             "-v", f"{work}:/run/secrets:ro", "-v", f"{courses}:/opt/courses:ro",
             *(["-v", "/run/classport-storage:/run/classport-storage:ro"] if manifest.get("materials") else []),
             "-v", f"{root / 'certs'}:/etc/classport/certs:ro", version["BACKEND_IMAGE"],
             "uv", "run", "--no-sync", "python", "scripts/smoke_restore.py"])
        report = {"status": "passed", "backup_id": manifest["backup_id"], "consistent_at": manifest["consistent_at"],
                  "restore_started_at": started, "restore_finished_at": now(),
                  "backup_age_seconds": (datetime.fromisoformat(started) - datetime.fromisoformat(manifest["consistent_at"])).total_seconds(),
                  "restore_duration_seconds": (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds(),
                  "actual_data_loss": "not_assessed",
                  "potential_data_loss_window": {"after": manifest["consistent_at"], "until": started},
                  "checks": ["schema", "counts", "file_references", "file_inventory", "secret_decryption", "login", "courses", "tasks", "pdf", "mcp"]}
        (root / "recovery-result.json").write_text(json.dumps(report, indent=2))
        return report


def monitor(root, settings):
    usage = shutil.disk_usage(root / "data")
    ready = 0
    try:
        with urlopen("http://127.0.0.1:8080/api/health/ready", timeout=3) as response:
            ready = int(response.status == 200 and json.load(response).get("ok") is True)
    except Exception:
        pass
    last = root / "last-backup.json"
    age = time.time() - datetime.fromisoformat(json.loads(last.read_text())["consistent_at"]).timestamp() if last.exists() else 1e9
    attempt = root / "last-backup-attempt.json"
    failed = int(attempt.exists() and json.loads(attempt.read_text())["status"] == "failed")
    job_metrics = {"JobMetricsReady": 0}
    for service, metric in (("dispatcher", "JobDispatcherReady"), ("job-recovery", "JobRecoveryReady")):
        job_metrics[metric] = 0
        try:
            release = (root / "releases/current").resolve(strict=True)
            state = json.loads(compose(root, release, "ps", "--all", "--format", "json", service))
            job_metrics[metric] = int(state.get("State") == "running" and state.get("Health") == "healthy")
        except Exception:
            pass
    try:
        release = (root / "releases/current").resolve(strict=True)
        output = compose(root, release, "run", "--rm", "--no-deps", "job-recovery",
                         "uv", "run", "--no-sync", "python", "-m", "app.job_coordination", "metrics", "--once")
        job_metrics.update(json.loads(output.splitlines()[-1]), JobMetricsReady=1)
    except Exception:
        pass
    metrics = [{"MetricName": name, "Value": value,
                "Dimensions": [{"Name": "Environment", "Value": settings["ENVIRONMENT"]}]}
               for name, value in {"APIReady": ready, "DiskUsedPercent": usage.used / usage.total * 100,
                                   "BackupAgeSeconds": age, "BackupFailed": failed, **job_metrics}.items()]
    aws("cloudwatch", "put-metric-data", "--namespace", "ClassPort/Operations", "--metric-data", json.dumps(metrics))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup")
    commands.add_parser("monitor")
    commands.add_parser("list").add_argument("--backup-bucket", required=True)
    recovery = commands.add_parser("restore")
    for name in ("backup-bucket", "manifest-key", "manifest-version", "target-host", "target-database", "target-db-secret-arn", "public-url", "pdf-path"):
        recovery.add_argument("--" + name, required=True)
    recovery.add_argument("--target-root", type=Path, required=True)
    recovery.add_argument("--check-credentials-file", type=Path)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError("Maintenance interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    os.umask(0o077)
    settings = read_env(Path(os.environ.get("CLASSPORT_HOST_ENV", "/etc/classport/host.env")))
    os.environ.update({key: value for key, value in settings.items() if key.startswith("AWS_")})
    root = Path(os.environ.get("CLASSPORT_DEPLOY_ROOT", "/opt/classport"))
    try:
        if args.command == "backup":
            save_json(root / "last-backup-attempt.json", {"status": "running", "started_at": now()})
            result = backup(root, settings)
            save_json(root / "last-backup-attempt.json", {"status": "succeeded", "finished_at": now()})
        elif args.command == "restore":
            result = restore(root, settings, args)
        elif args.command == "list":
            listing = aws("s3api", "list-object-versions", "--bucket", args.backup_bucket, "--prefix", "backups/")
            result = [{"key": item["Key"], "version_id": item["VersionId"], "uploaded_at": item["LastModified"]}
                      for item in listing.get("Versions", []) if item["Key"].endswith("/manifest.json")]
            result.sort(key=lambda item: item["uploaded_at"], reverse=True)
        else:
            result = monitor(root, settings)
        print(json.dumps({"event": args.command + ".completed", "result": result}))
    except Exception as error:
        if args.command == "backup":
            save_json(root / "last-backup-attempt.json", {"status": "failed", "finished_at": now(), "error_type": type(error).__name__, "stage": CURRENT_STAGE})
        print(json.dumps({"event": args.command + ".failed", "error_type": type(error).__name__, "stage": CURRENT_STAGE}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
