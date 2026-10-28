#!/usr/bin/env bash
# Stable, root-only SSM launcher. All versioned deployment files come from ECR.
# Usage: classport-release BACKEND@sha256:... FRONTEND@sha256:... COMMIT RELEASE_ID-ATTEMPT
set -euo pipefail
umask 077
set -a
source "${CLASSPORT_HOST_ENV:-/etc/classport/host.env}"
set +a
root="${CLASSPORT_DEPLOY_ROOT:-/opt/classport}"
[ "$#" -eq 4 ] || { echo "Expected backend digest, frontend digest, commit and run_id-attempt" >&2; exit 1; }
[[ $1 == "$BACKEND_REPOSITORY@sha256:"* && ${1##*@sha256:} =~ ^[a-f0-9]{64}$ ]]
[[ $2 == "$FRONTEND_REPOSITORY@sha256:"* && ${2##*@sha256:} =~ ^[a-f0-9]{64}$ ]]
[[ $3 =~ ^[a-f0-9]{40}$ && $4 =~ ^[0-9]+-[0-9]+$ ]]
backend="$1"; frontend="$2"; commit="$3"; run="$4"
exec 8>"$root/aws-release.lock"
flock -n 8 || { echo "Another AWS release is running" >&2; exit 1; }
release_id="$commit.$run"
mkdir -p "$root/attempts"
attempt="$root/attempts/$release_id"
mkdir "$attempt"
started=$(date -u +%FT%TZ)
stage=preflight
scratch=''
container=''
failed_stage=''
declare -A outcomes=([preflight]=pending [login]=pending [pull]=pending [extract]=pending [database]=pending [application]=pending)
write_result() {
    local status="$1" code="$2" database=null application=null name separator='' finished=null
    [ ! -f "$attempt/database-result.json" ] || database=$(cat "$attempt/database-result.json")
    [ ! -f "$attempt/application-result.json" ] || application=$(cat "$attempt/application-result.json")
    [ "$status" = running ] || finished="\"$(date -u +%FT%TZ)\""
    {
        printf '{"manifest":{"commit":"%s","backend":"%s","frontend":"%s"},' "$commit" "$backend" "$frontend"
        printf '"run":"%s","status":"%s","exit_code":%s,"started_at":"%s","finished_at":%s,' \
            "$run" "$status" "$code" "$started" "$finished"
        printf '"failed_stage":"%s","database":%s,"application":%s,"steps":{' "$failed_stage" "$database" "$application"
        for name in preflight login pull extract database application; do
            printf '%s"%s":"%s"' "$separator" "$name" "${outcomes[$name]}"
            separator=,
        done
        printf '}}\n'
    } > "$attempt/result.json.tmp"
    mv -f "$attempt/result.json.tmp" "$attempt/result.json"
}
start_step() { stage="$1"; outcomes[$stage]=running; write_result running 0; }
pass_step() { outcomes[$stage]=succeeded; write_result running 0; }
finish() {
    local code="$1" status=succeeded
    trap - EXIT INT TERM
    if [ "$code" -ne 0 ]; then
        status=failed
        failed_stage="$stage"
        outcomes[$stage]=failed
        echo "AWS release failed at $stage (exit $code)" >&2
    fi
    [ -z "$container" ] || docker rm "$container" >/dev/null 2>&1 || true
    [ -z "$scratch" ] || rm -rf "$scratch"
    write_result "$status" "$code"
    exit "$code"
}
trap 'finish $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start_step preflight
[ -f "$root/.host-ready" ] || { echo "Host initialization has not completed" >&2; exit 1; }
[ -n "$PUBLIC_URL" ] || { echo "Configure domain_name and public DNS before publishing" >&2; exit 1; }
mountpoint -q "$root/data"
[ -f "$root/certs/global-bundle.pem" ]
pass_step
start_step login
registry=${BACKEND_REPOSITORY%%/*}
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry"
pass_step
start_step pull
docker pull "$backend"
docker pull "$frontend"
for image in "$backend" "$frontend"; do
    revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
    [ "$revision" = "$commit" ] || { echo "Image revision does not match the checked commit" >&2; exit 1; }
done
pass_step
start_step extract
bundle="$attempt/bundle"
mkdir "$bundle"
container=$(docker create "$backend")
docker cp "$container:/app/release/." "$bundle/"
docker rm "$container" >/dev/null
container=''
for name in docker-compose.prod.yml deploy-prod.sh bootstrap.py; do [ -f "$bundle/$name" ]; done
printf 'RELEASE_COMMIT=%s\nBACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\n' "$commit" "$backend" "$frontend" > "$bundle/release.env"
pass_step
start_step database
scratch=$(mktemp -d /run/classport.XXXXXXXX)
aws secretsmanager get-secret-value --secret-id "$CONFIG_SECRET_ARN" \
    --query SecretString --output text > "$scratch/application.json"
aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN" \
    --query SecretString --output text > "$scratch/database.json"
# Only this short-lived maintenance container sees administrator credentials.
docker run --rm --network host --log-driver local \
    --env-file "${CLASSPORT_HOST_ENV:-/etc/classport/host.env}" \
    --mount "type=bind,src=$scratch,dst=/run/secrets,readonly" \
    --mount "type=bind,src=$root,dst=/deployment" \
    --mount "type=bind,src=$root/certs,dst=/etc/classport/certs,readonly" \
    "$backend" uv run --no-sync python /app/release/bootstrap.py \
    --result-file "/deployment/attempts/$release_id/database-result.json"
rm -rf "$scratch"
scratch=''
pass_step
if [ -n "${STORAGE_ROLE_ARN:-}" ]; then
    python3 "$bundle/storage_credentials.py" install
fi
start_step application
export CLASSPORT_APPLICATION_RESULT="$attempt/application-result.json"
bash "$bundle/deploy-prod.sh" "$bundle"
pass_step
