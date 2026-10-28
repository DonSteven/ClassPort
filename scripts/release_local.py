"""Publish and verify a committed checkout, then deploy using a local AWS profile."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts import deploy_via_ssm, publish_release


ROOT = Path(__file__).resolve().parents[1]


def release(profile: str) -> int:
    os.chdir(ROOT)
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Commit all source changes before publishing a release")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    # The selected login profile must be the only source of caller credentials.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                "AWS_SECURITY_TOKEN", "AWS_DEFAULT_PROFILE"):
        os.environ.pop(key, None)
    os.environ.update(AWS_PROFILE=profile, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
    outputs = json.loads(subprocess.check_output(
        ["terraform", "-chdir=infra/aws", "output", "-json"], text=True,
    ))
    settings = {key: outputs[key]["value"] for key in (
        "aws_region", "instance_id", "public_url", "backend_repository", "frontend_repository",
    )}
    os.environ.update(AWS_REGION=settings["aws_region"],
                      AWS_DEFAULT_REGION=settings["aws_region"],
                      PUBLIC_SITE_URL=settings["public_url"])
    if not settings["public_url"].startswith("https://"):
        raise RuntimeError("Configure the production HTTPS domain before publishing")
    identity = json.loads(subprocess.check_output(
        ["aws", "sts", "get-caller-identity", "--output", "json"], text=True,
    ))
    backend, frontend = settings["backend_repository"], settings["frontend_repository"]
    if any(repository.split(".", 1)[0] != identity["Account"]
           for repository in (backend, frontend)):
        raise RuntimeError("The AWS login account does not match the Terraform image repositories")
    print(f"AWS account: {identity['Account']}\nCaller: {identity['Arn']}", flush=True)

    run = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "-1"
    output = ROOT / "releases" / run
    output.mkdir(parents=True, exist_ok=False)
    print(f"Release: {commit}\nResults: {output}", flush=True)
    password = subprocess.check_output(["aws", "ecr", "get-login-password"])
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin",
                    backend.split("/", 1)[0]], input=password, check=True)
    publish_release.publish(commit, backend, frontend, output)
    manifest = deploy_via_ssm.read_manifest(output / "release.env")
    for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE"):
        subprocess.run(["docker", "pull", manifest[key]], check=True)
    os.environ.update(CLASSPORT_TEST_BACKEND_IMAGE=manifest["BACKEND_IMAGE"],
                      CLASSPORT_TEST_FRONTEND_IMAGE=manifest["FRONTEND_IMAGE"],
                      CLASSPORT_TEST_RELEASE_COMMIT=commit)
    subprocess.run(["uv", "run", "--no-sync", "pytest", "tests/test_release_images.py", "-q",
                    f"--junitxml={output / 'image-tests.xml'}"], check=True)
    status = deploy_via_ssm.deploy(manifest, settings["instance_id"], run, output)
    if status == 0:
        print(f"Release succeeded: {settings['public_url']}", flush=True)
    else:
        print(f"Release did not succeed; inspect {output / 'ssm-result.json'}", file=sys.stderr)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="AWS CLI short-lived login profile")
    args = parser.parse_args()
    try:
        return release(args.profile)
    except (RuntimeError, subprocess.CalledProcessError, KeyError, ValueError) as error:
        print(f"Release stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
