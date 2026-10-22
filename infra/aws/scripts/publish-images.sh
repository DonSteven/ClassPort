#!/usr/bin/env bash
# Builds a committed source snapshot and publishes the first AWS release.
# This changes ECR state. Requires operator approval before running.
# Usage from anywhere: infra/aws/scripts/publish-images.sh [git-ref]
set -euo pipefail
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
infra="$root/infra/aws"
commit=$(git -C "$root" rev-parse "${1:-HEAD}^{commit}")
backend=$(terraform -chdir="$infra" output -raw backend_repository)
frontend=$(terraform -chdir="$infra" output -raw frontend_repository)
origin=$(terraform -chdir="$infra" output -raw public_url)
[ -n "$origin" ] || { echo "Set domain_name before building the production frontend" >&2; exit 1; }
export AWS_REGION
AWS_REGION=$(terraform -chdir="$infra" output -raw aws_region)
export AWS_PAGER=""
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/source"
git -C "$root" archive "$commit" | tar -x -C "$scratch/source"
aws ecr get-login-password | docker login --username AWS --password-stdin "${backend%%/*}"
docker buildx build --platform linux/amd64 --push \
    -f "$scratch/source/Dockerfile" -t "$backend:$commit" \
    --metadata-file "$scratch/backend.json" "$scratch/source"
docker buildx build --platform linux/amd64 --push \
    -f "$scratch/source/web/Dockerfile" -t "$frontend:$commit" \
    --build-arg VITE_API_BASE_URL= --build-arg "VITE_SITE_URL=$origin" \
    --build-arg VITE_SHOW_LANDING=false --metadata-file "$scratch/frontend.json" "$scratch/source"
python3 - "$scratch" "$backend" "$frontend" "$commit" <<'PY'
import json
from pathlib import Path
import sys

directory, backend, frontend, commit = sys.argv[1:]
digests = [json.loads((Path(directory) / f"{name}.json").read_text())["containerimage.digest"]
           for name in ("backend", "frontend")]
print("Run in your EC2 SSM session:")
print(f"sudo classport-release {backend}@{digests[0]} {frontend}@{digests[1]} {commit}")
PY
