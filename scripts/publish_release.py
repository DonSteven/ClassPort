"""Publish or reuse each immutable ECR image for the checked commit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


SHA = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
REPOSITORY = re.compile(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?/[a-z0-9][a-z0-9._/-]*")


def find_digest(repository: str, commit: str) -> str | None:
    result = subprocess.run(
        ["aws", "ecr", "describe-images", "--repository-name", repository.split("/", 1)[1],
         "--image-ids", f"imageTag={commit}", "--output", "json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        if "(ImageNotFoundException)" in result.stderr:
            return None
        raise RuntimeError("Unable to query ECR; refusing to treat an AWS error as a missing image")
    digest = json.loads(result.stdout)["imageDetails"][0]["imageDigest"]
    if not DIGEST.fullmatch(digest):
        raise ValueError("ECR returned an invalid image digest")
    return digest


def publish(commit: str, backend: str, frontend: str, output: Path) -> dict:
    if not SHA.fullmatch(commit) or not all(REPOSITORY.fullmatch(r) for r in (backend, frontend)):
        raise ValueError("A full commit SHA and two ECR repository URLs are required")
    if backend.split("/", 1)[0] != frontend.split("/", 1)[0]:
        raise ValueError("Both repositories must use the same ECR registry")
    if not os.environ.get("PUBLIC_SITE_URL", "").startswith("https://"):
        raise ValueError("PUBLIC_SITE_URL must be the production HTTPS origin")
    output.mkdir(parents=True, exist_ok=True)
    images = {}
    for name, repository, dockerfile in (
        ("backend", backend, "Dockerfile"), ("frontend", frontend, "web/Dockerfile"),
    ):
        digest = find_digest(repository, commit)
        reused = digest is not None
        if not reused:
            command = [
                "docker", "buildx", "build", "--platform", "linux/amd64", "--push",
                "--file", dockerfile, "--tag", f"{repository}:{commit}",
                "--label", f"org.opencontainers.image.revision={commit}",
            ]
            if name == "frontend":
                for key, value in {
                    "VITE_API_BASE_URL": "", "VITE_SITE_URL": os.environ["PUBLIC_SITE_URL"],
                    "VITE_SITE_NAME": os.environ.get("PUBLIC_SITE_NAME") or "ClassPort",
                    "VITE_SHOW_LANDING": os.environ.get("PUBLIC_SHOW_LANDING") or "false",
                    "VITE_GOOGLE_SITE_VERIFICATION": os.environ.get("PUBLIC_GOOGLE_SITE_VERIFICATION", ""),
                }.items():
                    command.extend(["--build-arg", f"{key}={value}"])
            subprocess.run([*command, "."], check=True)
            digest = find_digest(repository, commit)
            if digest is None:
                raise RuntimeError(f"Published {name} image is missing from ECR")
        images[name] = {"image": f"{repository}@{digest}", "reused": reused}
    (output / "release.env").write_text(
        f"RELEASE_COMMIT={commit}\nBACKEND_IMAGE={images['backend']['image']}\n"
        f"FRONTEND_IMAGE={images['frontend']['image']}\n"
    )
    (output / "images.json").write_text(json.dumps({"commit": commit, "images": images}, indent=2) + "\n")
    return images


if __name__ == "__main__":
    publish(os.environ["RELEASE_COMMIT"], os.environ["BACKEND_REPOSITORY"],
            os.environ["FRONTEND_REPOSITORY"], Path("release"))
