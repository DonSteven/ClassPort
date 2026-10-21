#!/usr/bin/env bash
# Usage: deploy-prod.sh RELEASE_BUNDLE_DIRECTORY | --rollback
# A bundle contains release.env, docker-compose.prod.yml and deploy-prod.sh.
# EC2 needs Docker Compose, Bash, curl, flock and a mounted data disk; no build tools.
set -euo pipefail

ROOT="${CLASSPORT_DEPLOY_ROOT:-/opt/classport}"
STATE="$ROOT/releases"
HEALTH_TIMEOUT="${CLASSPORT_HEALTH_TIMEOUT:-60}"
HEALTH_URL=http://127.0.0.1:8080/api/health/ready

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "$(date -u +%FT%TZ) $*"; }

load_manifest() {
    local key value
    unset RELEASE_COMMIT BACKEND_IMAGE FRONTEND_IMAGE
    while IFS='=' read -r key value || [ -n "$key" ]; do
        case "$key" in
            ''|'#'*) continue ;;
            RELEASE_COMMIT|BACKEND_IMAGE|FRONTEND_IMAGE)
                [ -z "${!key:-}" ] || die "duplicate manifest key: $key"
                printf -v "$key" '%s' "$value"
                ;;
            *) die "unknown release manifest key: $key" ;;
        esac
    done < "$1"
    [[ ${RELEASE_COMMIT:-} =~ ^[a-f0-9]{40}$ ]] || die "release commit must be a full SHA"
    local name
    for name in BACKEND_IMAGE FRONTEND_IMAGE; do
        [[ ${!name:-} =~ ^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$ ]] \
            || die "$name must be a full registry image digest"
    done
    export RELEASE_COMMIT BACKEND_IMAGE FRONTEND_IMAGE
}

compose() {
    local release="$1"
    shift
    load_manifest "$release/release.env"
    docker compose --project-name classport-prod --project-directory "$ROOT" \
        --env-file "$release/release.env" -f "$release/docker-compose.prod.yml" "$@"
}

check_release() {
    local release="$1" body deadline
    deadline=$((SECONDS + HEALTH_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        body=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null) || body=''
        if [[ $body =~ \"ok\"[[:space:]]*:[[:space:]]*true ]]; then
            log "readiness passed; checking login page, API and MCP"
            compose "$release" run --rm --no-deps --pull never smoke
            return $?
        fi
        sleep 2
    done
    log "readiness timed out"
    return 1
}

point_to() {
    local name="$1" target="$2"
    ln -sfn "$target" "$STATE/.$name.$$" || return 1
    mv -Tf "$STATE/.$name.$$" "$STATE/$name"
}

restore_records() {
    if [ -n "$old" ]; then
        point_to current "$old" || return 1
    else
        rm -f "$STATE/current" || return 1
    fi
    if [ -n "$old_previous" ]; then
        point_to previous "$old_previous" || return 1
    else
        rm -f "$STATE/previous" || return 1
    fi
}

[ "$#" -eq 1 ] || die "usage: $0 RELEASE_BUNDLE_DIRECTORY | --rollback"
[ -d "$ROOT" ] || die "deployment root missing: $ROOT"
mkdir -p "$STATE"
exec 9>"$STATE/deploy.lock"
flock -n 9 || die "another production deployment is running"

for name in .env .env.smoke; do
    [ -f "$ROOT/$name" ] || die "$ROOT/$name missing"
done
if [ "$1" != --rollback ]; then
    [ -f "$ROOT/.env.migration" ] || die "$ROOT/.env.migration missing"
fi
[ -f "$ROOT/certs/global-bundle.pem" ] || die "RDS CA bundle missing"
mountpoint -q "$ROOT/data" || die "$ROOT/data must be the mounted persistent data disk"
mkdir -p "$ROOT/data/courses"

old=''
if [ -L "$STATE/current" ]; then
    old=$(readlink -f "$STATE/current")
    [ -d "$old" ] || die "current release directory missing"
fi
old_previous=''
if [ -L "$STATE/previous" ]; then
    old_previous=$(readlink "$STATE/previous")
fi

rollback_only=0
if [ "$1" = --rollback ]; then
    rollback_only=1
    [ -L "$STATE/previous" ] || die "no previous successful release"
    candidate=$(readlink -f "$STATE/previous")
    [ -d "$candidate" ] || die "previous release directory missing"
else
    bundle=$(realpath "$1")
    for name in release.env docker-compose.prod.yml deploy-prod.sh; do
        [ -f "$bundle/$name" ] || die "bundle is missing $name"
    done
    load_manifest "$bundle/release.env"
    candidate=$(mktemp -d "$STATE/$RELEASE_COMMIT.XXXXXXXX")
    cp "$bundle/release.env" "$bundle/docker-compose.prod.yml" "$bundle/deploy-prod.sh" "$candidate/"
fi

same_release=0
if [ -n "$old" ]; then
    load_manifest "$candidate/release.env"
    candidate_backend="$BACKEND_IMAGE"
    candidate_frontend="$FRONTEND_IMAGE"
    load_manifest "$old/release.env"
    if [ "$BACKEND_IMAGE" = "$candidate_backend" ] && \
       [ "$FRONTEND_IMAGE" = "$candidate_frontend" ] && \
       cmp -s "$old/docker-compose.prod.yml" "$candidate/docker-compose.prod.yml"; then
        same_release=1
    fi
fi

compose "$candidate" config -q
log "pulling the release image pair"
compose "$candidate" pull --policy missing classport frontend

if [ "$rollback_only" -eq 0 ]; then
    log "running the release's one-shot database migration"
    compose "$candidate" run --rm --no-deps --pull never migrate
    log "reconciling the operator with maintenance credentials"
    compose "$candidate" run --rm --no-deps --pull never migrate \
        uv run --no-sync python scripts/seed_operator_password.py
fi

records_changed=0
on_exit() {
    local status="$1" records_restored=1
    trap - EXIT INT TERM
    [ "$status" -ne 0 ] || return 0
    log "release failed; committed database changes are not reverted"
    if [ "$records_changed" -eq 1 ] && ! restore_records; then
        log "failed to restore release records; manual intervention required"
        records_restored=0
    fi
    compose "$candidate" logs --tail 40 classport frontend || true
    if [ -z "$old" ]; then
        log "no previous successful release is recorded"
        [ "$records_restored" -eq 1 ] || exit 2
        exit 1
    fi
    log "restoring both images from $old"
    if compose "$old" up -d --no-build --pull never classport frontend && check_release "$old"; then
        [ "$records_restored" -eq 1 ] || exit 2
        log "previous image pair restored; deployment remains failed"
        exit 1
    fi
    log "rollback failed; manual intervention required"
    exit 2
}

trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
log "starting the release image pair"
compose "$candidate" up -d --no-build --pull never classport frontend
check_release "$candidate"

records_changed=1
if [ -n "$old" ] && [ "$same_release" -eq 0 ]; then
    point_to previous "$old"
fi
point_to current "$candidate"
trap - EXIT INT TERM
log "release passed; current release is $candidate"
