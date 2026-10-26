#!/usr/bin/env bash
# ClassPort deploy script — runs on the deployment host inside /opt/classport.
#
# Usage:
#   ./deploy.sh             # full deploy: build → migrate → roll → health-gate → rollback-on-fail
#   ./deploy.sh --pull       # fetch + reset to origin/main (or $CLASSPORT_DEPLOY_REF) before deploying
#   ./deploy.sh --skip-build # skip image rebuild (useful for env-only changes)
#   ./deploy.sh --no-rollback # halt on health failure but don't auto-rollback (debugging)
#   ./deploy.sh --status     # print current image tags + container health and exit
#   ./deploy.sh --external-db # use an existing PostgreSQL / RDS, skip local Postgres
#
# Environment variables (for --pull):
#   CLASSPORT_DEPLOY_REF  git ref to reset to (default: origin/main); e.g. a version tag like v0.7.2
#
# Behaviour:
#   1. Pre-flight: validate compose, check disk space, check .env files exist.
#   2. Tag the currently-running image as classport:previous (rollback target).
#   3. Build new backend and frontend images (unless --skip-build).
#   4. Apply pending migrations against the live postgres (still running).
#   5. Recreate the classport container with the new image.
#   6. Poll readiness at http://127.0.0.1:8000/api/health for up to 60s.
#   7. PASS → prune dangling images, log success.
#      FAIL → re-tag :previous as :latest, recreate container, wait for old health.
#
# Logs to /var/log/classport-deploy.log (root-owned).

set -euo pipefail

cd "$(dirname "$0")"

# .env.docker supplies Compose build / local Postgres settings. Service-level
# env_file directives separately inject runtime and maintenance credentials.
COMPOSE="docker compose --env-file .env.docker"

LOG="$PWD/classport-deploy.log"
# Readiness uses the existing URL so the same gate also works on older images.
HEALTH_URL=http://127.0.0.1:8000/api/health
HEALTH_TIMEOUT=60   # seconds
HEALTH_INTERVAL=2   # seconds

SKIP_BUILD=0
NO_ROLLBACK=0
SHOW_STATUS=0
PULL=0
EXTERNAL_DB=0
for arg in "$@"; do
    case "$arg" in
        --pull)        PULL=1 ;;
        --external-db) EXTERNAL_DB=1 ;;
        --skip-build)  SKIP_BUILD=1 ;;
        --no-rollback) NO_ROLLBACK=1 ;;
        --status)      SHOW_STATUS=1 ;;
        -h|--help)     sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [ "$EXTERNAL_DB" -eq 0 ]; then
    COMPOSE="$COMPOSE --profile local-db"
fi

# ── helpers ──────────────────────────────────────────────────────────────────

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { local m="$*"; echo "[$(ts)] $m" | tee -a "$LOG"; }
err() { local m="$*"; echo "[$(ts)] ERROR: $m" | tee -a "$LOG" >&2; }

current_image_id() {
    $COMPOSE images classport --format '{{.ID}}' 2>/dev/null | head -1
}

container_health() {
    docker inspect --format='{{.State.Health.Status}}' classport 2>/dev/null || echo "missing"
}

poll_health() {
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        local body
        body=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null) || body=""
        if [ -n "$body" ] && echo "$body" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
            log "readiness: PASS — $body"
            return 0
        fi
        sleep "$HEALTH_INTERVAL"
    done
    err "readiness: FAIL — $HEALTH_URL did not return ok=true within ${HEALTH_TIMEOUT}s"
    $COMPOSE logs --tail=40 classport | tee -a "$LOG"
    return 1
}

# ── status mode ──────────────────────────────────────────────────────────────

if [ "$SHOW_STATUS" -eq 1 ]; then
    echo "=== compose state ==="
    $COMPOSE ps
    echo
    echo "=== image tags ==="
    docker images classport --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}\t{{.Size}}'
    echo
    echo "=== current classport readiness ==="
    container_health
    echo
    echo "=== last 20 lines of $LOG ==="
    tail -n 20 "$LOG" 2>/dev/null || echo "(no log yet)"
    exit 0
fi

# ── pull from origin (--pull) ────────────────────────────────────────────────
if [ "$PULL" -eq 1 ]; then
    REF="${CLASSPORT_DEPLOY_REF:-origin/main}"
    log "pulling latest from git (ref: $REF)..."
    git fetch --all --tags 2>&1 | tee -a "$LOG"
    if ! git reset --hard "$REF" 2>&1 | tee -a "$LOG"; then
        err "git reset to $REF failed — aborting before any container changes"
        exit 1
    fi
    log "git tree now at: $(git log -1 --oneline)"
fi

# ── pre-flight ───────────────────────────────────────────────────────────────

log "=== deploy starting ==="

[ -f docker-compose.yml ] || { err "docker-compose.yml missing"; exit 1; }
[ -f .env ]               || { err ".env missing — backend secrets file";  exit 1; }
[ -f .env.docker ]        || { err ".env.docker missing — postgres secrets"; exit 1; }
[ -f .env.migration ]     || { err ".env.migration missing — maintenance credentials"; exit 1; }
[ -f .env.coordinator ]   || { err ".env.coordinator missing — restricted task coordinator credentials"; exit 1; }
[ -f .env.jobs ]          || { err ".env.jobs missing — copy .env.jobs.example for shared task settings"; exit 1; }
[ -f Dockerfile ]         || { err "Dockerfile missing"; exit 1; }

log "validating docker-compose.yml..."
$COMPOSE config -q || { err "compose validation failed"; exit 1; }

log "checking disk space (need ≥ 1 GB free)..."
free_kb=$(df --output=avail / | tail -1)
if [ "$free_kb" -lt 1048576 ]; then
    err "low disk: only $((free_kb/1024)) MB free on /"
    exit 1
fi

# ── snapshot rollback target ─────────────────────────────────────────────────

PREV_ID=$(current_image_id || true)
PREV_FRONTEND_ID=$($COMPOSE images frontend --format '{{.ID}}' 2>/dev/null | head -1 || true)
if [ -n "$PREV_FRONTEND_ID" ]; then
    docker tag "$PREV_FRONTEND_ID" classport-frontend:previous
fi
if [ -n "${PREV_ID:-}" ]; then
    log "tagging current image $PREV_ID as classport:previous (rollback target)"
    docker tag "$PREV_ID" classport:previous
else
    log "no previous image — first deploy"
fi

# ── build ────────────────────────────────────────────────────────────────────

if [ "$SKIP_BUILD" -eq 0 ]; then
    log "building classport + frontend images..."
    if ! $COMPOSE build 2>&1 | tee -a "$LOG"; then
        err "build failed — aborting before any container changes"
        exit 1
    fi
else
    log "--skip-build: reusing current images"
fi

# ── ensure postgres is up before migrating ───────────────────────────────────

if [ "$EXTERNAL_DB" -eq 0 ]; then
    log "ensuring local postgres is up..."
    $COMPOSE up -d postgres
    for _ in $(seq 1 30); do
        pg_health=$(docker inspect --format='{{.State.Health.Status}}' classport-postgres 2>/dev/null || echo "starting")
        [ "$pg_health" = "healthy" ] && break
        sleep 1
    done
    [ "$pg_health" = "healthy" ] || { err "postgres did not become healthy"; exit 1; }
    log "postgres: healthy"
else
    log "using external database; skipping local postgres (migration runner checks connectivity)"
fi

# ── migrate ──────────────────────────────────────────────────────────────────

log "running migrations..."
if ! $COMPOSE run --rm --no-deps migrate 2>&1 | tee -a "$LOG"; then
    err "migration failed — aborting before swapping containers (postgres untouched if migration ran in transaction)"
    exit 1
fi

# Phase 1: one-shot FS migration (idempotent — guarded by marker file).
if [[ -x ./scripts/migrate_study_root.sh ]]; then
    log "running phase 1 FS migration (idempotent)..."
    OPERATOR_USER_ID="${OPERATOR_USER_ID:-00000000-0000-0000-0000-000000000001}" \
    STUDY_ROOT="${STUDY_ROOT:-/opt/courses}" \
        ./scripts/migrate_study_root.sh || {
        err "FS migration failed — investigate before continuing"
        exit 1
    }
fi

# Reconcile the operator using maintenance credentials and initialization values.
if [[ -x ./scripts/seed_operator_password.py ]]; then
    log "reconciling operator user from .env.migration (idempotent)..."
    $COMPOSE run --rm --no-deps migrate uv run --no-sync python scripts/seed_operator_password.py 2>&1 | tee -a "$LOG" || {
        err "operator seed failed — investigate"
        exit 1
    }
fi

# ── roll forward ─────────────────────────────────────────────────────────────

log "starting new classport + recreating frontend..."
$COMPOSE stop storage-maintenance dispatcher job-recovery worker
$COMPOSE up -d classport redis worker dispatcher job-recovery storage-maintenance
# Force-recreate frontend so it picks up its newly built image — without
# this, the running frontend container keeps serving the previous build
# even though `compose build` just produced a fresh image.
$COMPOSE up -d --force-recreate frontend

# ── health gate ──────────────────────────────────────────────────────────────

log "polling $HEALTH_URL (timeout ${HEALTH_TIMEOUT}s)..."
if poll_health; then
    log "deploy: SUCCESS"
    if [ -n "${PREV_ID:-}" ] && [ "$PREV_ID" != "$(current_image_id)" ]; then
        log "pruning dangling images..."
        docker image prune -f >> "$LOG" 2>&1
    fi
    exit 0
fi

# ── rollback ─────────────────────────────────────────────────────────────────

if [ "$NO_ROLLBACK" -eq 1 ]; then
    err "deploy: FAILED — health gate did not pass. --no-rollback set, leaving broken state for inspection"
    exit 1
fi

if [ -z "${PREV_ID:-}" ] || [ -z "${PREV_FRONTEND_ID:-}" ]; then
    err "deploy: FAILED — no previous image pair to roll back to"
    exit 1
fi

log "ROLLBACK: re-tagging classport:previous as classport:latest..."
docker tag classport:previous classport:latest
docker tag classport-frontend:previous classport-frontend:latest
$COMPOSE stop storage-maintenance dispatcher job-recovery worker
log "ROLLBACK: recreating classport container..."
if [ "$(docker image inspect --format '{{ index .Config.Labels "org.classport.file-jobs" }}' classport:previous)" = 1 ]; then
    $COMPOSE up -d --force-recreate classport frontend worker dispatcher job-recovery
else
    $COMPOSE up -d --force-recreate classport frontend
fi

log "ROLLBACK: polling health on restored image..."
if poll_health; then
    err "deploy: FAILED but rollback succeeded — service is on previous image. Investigate and try again."
    exit 1
else
    err "deploy: CATASTROPHIC — rollback also failed health. Service is down. Investigate immediately."
    exit 2
fi
