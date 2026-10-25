"""Host-only STS rotation. Never mounted into an application container."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


DIRECTORY = Path("/run/classport-storage")


def atomic_write(path, content, mode=0o600):
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def refresh(directory=DIRECTORY):
    result = subprocess.run([
        "aws", "sts", "assume-role", "--role-arn", os.environ["STORAGE_ROLE_ARN"],
        "--role-session-name", "classport-materials", "--duration-seconds", "3600", "--output", "json",
    ], capture_output=True, text=True, check=True)
    grant = json.loads(result.stdout)["Credentials"]
    data = {"Version": 1, **{key: grant[key] for key in (
        "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")}}
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    atomic_write(directory / "config", "[profile classport-storage]\n"
                 "credential_process = /app/.venv/bin/python /app/scripts/storage_credential_process.py\n"
                 f"region = {os.environ['AWS_REGION']}\n", 0o400)
    atomic_write(directory / "credentials.json", json.dumps(data) + "\n", 0o400)


def install():
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DIRECTORY, 0o700)
    target = Path("/usr/local/sbin/classport-storage-credentials")
    shutil.copyfile(__file__, target)
    target.chmod(0o700)
    Path("/etc/systemd/system/classport-storage-credentials.service").write_text("""[Unit]
Description=Rotate restricted ClassPort material credentials
After=network-online.target
[Service]
Type=oneshot
UMask=0077
EnvironmentFile=/etc/classport/host.env
ExecStart=/usr/bin/python3 /usr/local/sbin/classport-storage-credentials refresh
RuntimeDirectory=classport-storage
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
""")
    Path("/etc/systemd/system/classport-storage-credentials.timer").write_text("""[Unit]
Description=Refresh ClassPort storage grant every five minutes
[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
""")
    dropins = Path("/etc/systemd/system/docker.service.d")
    dropins.mkdir(parents=True, exist_ok=True)
    (dropins / "classport-storage.conf").write_text("""[Unit]
Requires=classport-storage-credentials.service
After=classport-storage-credentials.service
""")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    refresh()
    subprocess.run(["systemctl", "enable", "--now", "classport-storage-credentials.timer"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "refresh"))
    args = parser.parse_args()
    try:
        install() if args.action == "install" else refresh()
    except Exception:
        # STS errors/stdout can contain credential material; never print them.
        raise SystemExit("Storage credential rotation failed; details omitted") from None
