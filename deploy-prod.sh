#!/usr/bin/env bash
# Internal application switch after classport-release prepares the database.
# Usage: deploy-prod.sh RELEASE_BUNDLE_DIRECTORY | --rollback
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

manifest_json() {
    load_manifest "$1/release.env"
    printf '{"commit":"%s","backend":"%s","frontend":"%s"}' \
        "$RELEASE_COMMIT" "$BACKEND_IMAGE" "$FRONTEND_IMAGE"
}

write_result() {
    local status="$1" code="$2" current=null name separator='' finished=null
    if [ -L "$STATE/current" ]; then
        current=$(manifest_json "$STATE/current")
    fi
    [ "$status" = running ] || finished="\"$(date -u +%FT%TZ)\""
    {
        printf '{"manifest":%s,"status":"%s","exit_code":%s,"started_at":"%s","finished_at":%s,' \
            "$requested" "$status" "$code" "$started" "$finished"
        printf '"failed_stage":"%s","rollback":"%s","current_release":%s,"steps":{' \
            "$failed_stage" "$rollback" "$current"
        for name in configuration pull start readiness smoke images promote; do
            printf '%s"%s":"%s"' "$separator" "$name" "${outcomes[$name]}"
            separator=,
        done
        printf '}}\n'
    } > "$result_file.tmp"
    mv -f "$result_file.tmp" "$result_file"
}

start_step() { stage="$1"; outcomes[$stage]=running; write_result running 0; }
pass_step() { outcomes[$stage]=succeeded; write_result running 0; }

verify_images() {
    local release="$1" backend frontend container actual service expected
    load_manifest "$release/release.env"
    backend="$BACKEND_IMAGE"; frontend="$FRONTEND_IMAGE"
    for service in classport frontend; do
        expected="$backend"
        [ "$service" != frontend ] || expected="$frontend"
        container=$(compose "$release" ps -q "$service") || return 1
        [ -n "$container" ] || return 1
        actual=$(docker inspect --format '{{.Config.Image}}' "$container") || return 1
        [ "$actual" = "$expected" ] || return 1
    done
}

check_release() {
    local release="$1" body deadline track=0
    [ "$release" != "$candidate" ] || track=1
    [ "$track" -eq 0 ] || start_step readiness
    deadline=$((SECONDS + HEALTH_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        body=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null) || body=''
        if [[ $body =~ \"ok\"[[:space:]]*:[[:space:]]*true ]]; then
            [ "$track" -eq 0 ] || { pass_step; start_step smoke; }
            log "readiness passed; checking login page, API and MCP"
            compose "$release" run --rm --no-deps --pull never smoke || return $?
            [ "$track" -eq 0 ] || { pass_step; start_step images; }
            verify_images "$release" || return 1
            [ "$track" -eq 0 ] || pass_step
            return 0
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
    if [ -n "$old" ]; then point_to current "$old" || return 1
    else rm -f "$STATE/current" || return 1; fi
    if [ -n "$old_previous" ]; then point_to previous "$old_previous" || return 1
    else rm -f "$STATE/previous" || return 1; fi
}

[ "$#" -eq 1 ] || die "usage: $0 RELEASE_BUNDLE_DIRECTORY | --rollback"
[ -d "$ROOT" ] || die "deployment root missing: $ROOT"
mkdir -p "$STATE"
exec 9>"$STATE/deploy.lock"
flock -n 9 || die "another production deployment is running"
for name in .env .env.smoke; do
    [ -f "$ROOT/$name" ] || die "$ROOT/$name missing"
done
[ -f "$ROOT/certs/global-bundle.pem" ] || die "RDS CA bundle missing"
mountpoint -q "$ROOT/data" || die "$ROOT/data must be the mounted persistent data disk"
mkdir -p "$ROOT/data/courses"
old=''
if [ -L "$STATE/current" ]; then
    old=$(readlink -f "$STATE/current")
    [ -d "$old" ] || die "current release directory missing"
fi
old_previous=''
[ ! -L "$STATE/previous" ] || old_previous=$(readlink "$STATE/previous")
if [ "$1" = --rollback ]; then
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
    for helper in bootstrap.py recovery.py; do
        [ ! -f "$bundle/$helper" ] || cp "$bundle/$helper" "$candidate/"
    done
fi

requested=$(manifest_json "$candidate")
started=$(date -u +%FT%TZ)
result_file="${CLASSPORT_APPLICATION_RESULT:-$candidate/application-result.json}"
declare -A outcomes=([configuration]=pending [pull]=pending [start]=pending [readiness]=pending [smoke]=pending [images]=pending [promote]=pending)
stage=configuration
failed_stage=''
rollback=not_needed
records_changed=0
switch_started=0
on_exit() {
    local status="$1" records_restored=1 code=1
    trap - EXIT INT TERM
    [ "$status" -ne 0 ] || return 0
    failed_stage="$stage"
    outcomes[$stage]=failed
    log "release failed at $stage; committed database changes are not reverted"
    if [ "$records_changed" -eq 1 ] && ! restore_records; then
        log "failed to restore release records; manual intervention required"
        records_restored=0
    fi
    if [ "$switch_started" -eq 1 ]; then
        compose "$candidate" logs --tail 40 classport frontend || true
        if [ -z "$old" ]; then
            rollback=no_previous_release
            log "no previous successful release is recorded"
        elif compose "$old" up -d --no-build --pull never classport frontend && check_release "$old"; then
            rollback=succeeded
            log "previous image pair restored; deployment remains failed"
        else
            rollback=failed
            log "rollback failed; manual intervention required"
            code=2
        fi
    fi
    [ "$records_restored" -eq 1 ] || code=2
    write_result failed "$code"
    exit "$code"
}
trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_step configuration
same_release=0
if [ -n "$old" ]; then
    load_manifest "$candidate/release.env"
    candidate_backend="$BACKEND_IMAGE"; candidate_frontend="$FRONTEND_IMAGE"
    load_manifest "$old/release.env"
    if [ "$BACKEND_IMAGE" = "$candidate_backend" ] && [ "$FRONTEND_IMAGE" = "$candidate_frontend" ] && \
       cmp -s "$old/docker-compose.prod.yml" "$candidate/docker-compose.prod.yml"; then
        same_release=1
    fi
fi
compose "$candidate" config -q
pass_step
start_step pull
log "pulling the release image pair"
compose "$candidate" pull --policy missing classport frontend
pass_step
start_step start
switch_started=1
log "starting the release image pair"
compose "$candidate" up -d --no-build --pull never classport frontend
pass_step
check_release "$candidate"
start_step promote
records_changed=1
if [ -n "$old" ] && [ "$same_release" -eq 0 ]; then point_to previous "$old"; fi
point_to current "$candidate"
pass_step
write_result succeeded 0
trap - EXIT INT TERM
log "release passed; current release is $candidate"
