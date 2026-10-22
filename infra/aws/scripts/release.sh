#!/usr/bin/env bash
# Root-only entrypoint invoked from an SSM session/Run Command. No secrets in args.
# Usage: classport-release BACKEND@sha256:... FRONTEND@sha256:... COMMIT_SHA
set -euo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin
set -a
source /etc/classport/host.env
set +a
root=/opt/classport
[ -f "$root/.host-ready" ] || { echo "Host initialization has not completed" >&2; exit 1; }
[ -n "$PUBLIC_URL" ] || { echo "Configure domain_name and public DNS before publishing" >&2; exit 1; }
[ "$#" -eq 3 ] || { echo "Expected backend digest, frontend digest and commit SHA" >&2; exit 1; }
[[ $1 == "$BACKEND_REPOSITORY@sha256:"* && ${1##*@sha256:} =~ ^[a-f0-9]{64}$ ]]
[[ $2 == "$FRONTEND_REPOSITORY@sha256:"* && ${2##*@sha256:} =~ ^[a-f0-9]{64}$ ]]
[[ $3 =~ ^[a-f0-9]{40}$ ]]
exec 8>"$root/aws-release.lock"
flock -n 8 || { echo "Another AWS release is running" >&2; exit 1; }
mountpoint -q "$root/data"

scratch=$(mktemp -d /run/classport.XXXXXXXX)
trap 'rm -rf "$scratch"' EXIT
aws secretsmanager get-secret-value --secret-id "$CONFIG_SECRET_ARN" \
    --query SecretString --output text > "$scratch/application.json"
aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN" \
    --query SecretString --output text > "$scratch/database.json"
registry=${BACKEND_REPOSITORY%%/*}
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry"
docker pull "$1"

# Only this short-lived host-network maintenance container can read the master
# secret. The running app uses the bridge network, IMDS hop limit 1 and .env only.
docker run --rm --network host --log-driver local \
    --env-file /etc/classport/host.env \
    --mount "type=bind,src=$scratch,dst=/run/secrets,readonly" \
    --mount "type=bind,src=$root,dst=/deployment" \
    --mount "type=bind,src=$root/certs,dst=/etc/classport/certs,readonly" \
    "$1" uv run --no-sync python /deployment/aws/bootstrap.py

bundle=$(mktemp -d "$root/incoming/$3.XXXXXXXX")
printf 'RELEASE_COMMIT=%s\nBACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\n' "$3" "$1" "$2" > "$bundle/release.env"
cp "$root/aws/docker-compose.prod.yml" "$root/aws/deploy-prod.sh" "$bundle/"
bash "$bundle/deploy-prod.sh" "$bundle"
