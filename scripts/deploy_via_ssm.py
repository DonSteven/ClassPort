"""Send one production release command, wait for it, and retain its result."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time


ACTIVE = {"Pending", "InProgress", "Delayed", "Cancelling"}
MARKER = "CLASSPORT_RELEASE_RESULT="


def aws_json(*args: str) -> dict:
    result = subprocess.run(
        ["aws", "ssm", *args, "--output", "json", "--cli-connect-timeout", "10",
         "--cli-read-timeout", "20"], capture_output=True, text=True, timeout=40,
    )
    if result.returncode:
        if "(InvocationDoesNotExist)" in result.stderr:
            raise LookupError("Command result is not visible yet")
        raise RuntimeError("SSM request failed; check AWS authorization and command state")
    return json.loads(result.stdout)


def read_manifest(path: Path) -> dict:
    manifest = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in manifest:
            raise ValueError("Duplicate release manifest key")
        manifest[key] = value
    if set(manifest) != {"RELEASE_COMMIT", "BACKEND_IMAGE", "FRONTEND_IMAGE"}:
        raise ValueError("Invalid release manifest keys")
    if not re.fullmatch(r"[a-f0-9]{40}", manifest["RELEASE_COMMIT"]):
        raise ValueError("Invalid release commit")
    for name in ("BACKEND_IMAGE", "FRONTEND_IMAGE"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", manifest[name]):
            raise ValueError("Deployment requires immutable image digests")
    return manifest


def remote_command(manifest: dict, run: str) -> str:
    release_id = f"{manifest['RELEASE_COMMIT']}.{run}"
    log = f"/opt/classport/commands/{release_id}.log"
    result = f"/opt/classport/attempts/{release_id}/result.json"
    args = [manifest["BACKEND_IMAGE"], manifest["FRONTEND_IMAGE"], manifest["RELEASE_COMMIT"], run]
    return (
        "set -eu\numask 077\nmkdir -p /opt/classport/commands\nstatus=0\n"
        f"/usr/local/sbin/classport-release {shlex.join(args)} > {shlex.quote(log)} 2>&1 || status=$?\n"
        f"tail -c 12000 {shlex.quote(log)}\nprintf '\\n{MARKER}'\n"
        f"if [ -f {shlex.quote(result)} ]; then cat {shlex.quote(result)}; else printf 'null\\n'; fi\n"
        'exit "$status"\n'
    )


def deploy(manifest: dict, instance: str, run: str, output: Path, timeout: int = 780) -> int:
    if not re.fullmatch(r"i-(?:[a-f0-9]{8}|[a-f0-9]{17})", instance):
        raise ValueError("Invalid target instance ID")
    if not re.fullmatch(r"[0-9]+-[0-9]+", run):
        raise ValueError("Release run must be a numeric release ID and attempt, separated by a hyphen")
    output.mkdir(parents=True, exist_ok=True)
    record = {"manifest": manifest, "run": run, "instance_id": instance,
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "sending"}

    def save():
        (output / "ssm-result.json").write_text(json.dumps(record, indent=2) + "\n")

    save()
    try:
        sent = aws_json(
            "send-command", "--instance-ids", instance, "--document-name", "AWS-RunShellScript",
            "--timeout-seconds", "120", "--comment", f"ClassPort {manifest['RELEASE_COMMIT']} {run}",
            "--parameters", json.dumps({"commands": [remote_command(manifest, run)],
                                         "executionTimeout": ["600"]}),
        )
        command_id = sent["Command"]["CommandId"]
        record.update(command_id=command_id, status="waiting")
        save()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                invocation = aws_json("get-command-invocation", "--command-id", command_id,
                                      "--instance-id", instance)
            except LookupError:
                time.sleep(5)
                continue
            record["invocation"] = invocation
            save()
            if invocation["Status"] in ACTIVE:
                time.sleep(5)
                continue
            stdout = invocation.get("StandardOutputContent", "")
            (output / "ssm-stdout.log").write_text(stdout)
            (output / "ssm-stderr.log").write_text(invocation.get("StandardErrorContent", ""))
            host = None
            for line in stdout.splitlines():
                if line.startswith(MARKER):
                    host = json.loads(line[len(MARKER):])
            record["release"] = host
            expected = {"commit": manifest["RELEASE_COMMIT"], "backend": manifest["BACKEND_IMAGE"],
                        "frontend": manifest["FRONTEND_IMAGE"]}
            success = (
                invocation["Status"] == "Success" and invocation.get("ResponseCode") == 0
                and host is not None and host.get("status") == "succeeded"
                and host.get("run") == run and host.get("manifest") == expected
                and (host.get("database") or {}).get("status") == "succeeded"
                and host["database"].get("steps", {}).get("migration") == "succeeded"
                and (host.get("application") or {}).get("status") == "succeeded"
                and host["application"].get("current_release") == expected
            )
            record["status"] = "succeeded" if success else "failed"
            return 0 if success else 1
        record["status"] = "timed_out"
        record["message"] = "Remote state is unconfirmed; inspect this command before another release."
        return 1
    except (RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as error:
        record["status"] = "unconfirmed" if "command_id" in record else "send_failed"
        record["message"] = str(error)
        return 1
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()


if __name__ == "__main__":
    raise SystemExit(deploy(read_manifest(Path("release/release.env")), os.environ["AWS_INSTANCE_ID"],
                           os.environ["RELEASE_RUN_ID"], Path("release")))
