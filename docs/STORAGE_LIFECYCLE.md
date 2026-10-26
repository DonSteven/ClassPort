# Material migration and cleanup (B8)

PostgreSQL owns file identity, names, upload state and cleanup intent. Storage
operations run separately; interrupted work is resumed from database records.
Renaming never changes file/version IDs, jobs or object keys.

## Retention

`STORAGE_RETENTION_SECONDS` defaults to 604800 (seven days). Deleted files and
superseded completed versions retain their content for this period. Existing
superseded versions receive a fresh retention window when the B8 migration runs.
`STORAGE_UPLOAD_GRACE_SECONDS` defaults to 3600: abandoned uploads are not cleaned
until their recorded upload grant has expired plus this transfer grace period.
Choose a grace period that covers the installation's longest supported transfer.

Reads and download grants extend a version's read protection. Active index leases
also prevent physical cleanup. A protected target is deferred, and the ordinary
worker later retries it. Already-issued S3 grants remain valid until expiry.

Deleting a file commits its tombstone, cancellation of waiting index jobs, cleanup
targets and outbox notifications together. The worker retires eligible content
before deleting its exact physical version, then records completion. If deletion
succeeds but the result transaction fails, repeated deletion is harmless. Logical
file/version records remain; retired or purged content cannot be confirmed or read.
Historical version URLs stop resolving after their content is retired.

`storage-maintenance` uses the ordinary application account and restricted material
credentials. Every minute it reconciles at most 100 versions per account, rotating
through records by last-check time. It discovers expired pending uploads and S3
versions left by replayed PUTs, including delete markers. The existing worker,
outbox dispatcher and lease recovery execute/retry cleanup. The restricted
coordinator account receives no new file or credential access.

## LocalStorage to S3 cutover

Actual copying uses paid S3 operations and updates storage references. Approve the
operation, back up the installation, and stop application, worker, coordinator,
storage-maintenance and external import writers before running it. Do not overlap
this operator command with backup, restore or another migration command.

1. Apply the normal SQL migrations. Mount the **existing versioned LocalStorage
   root**, provide runtime DATABASE_URL and the restricted S3 credentials, and run:

   ```sh
   uv run --no-sync python scripts/migrate_storage.py --user-id USER_UUID --limit 100
   ```

2. Repeat batches until `remaining` is zero and `errors` is empty for every owner.
   The JSON report includes copied/reused counts, bytes and failed version IDs.
   Copies preserve application IDs, revisions and index/job links. Size, SHA-256
   and content type must match before the database mapping is changed. A recorded
   target key permits reuse even after interruption immediately following PUT.
3. Start production with S3 only after the complete inventory passes. Production
   API/worker containers have no material-directory mounts. Per-version storage
   selection already supports mixed references, but an online mixed deployment
   would require explicit temporary local mounts; the standard cutover uses the
   maintenance window instead.
4. Keep the original root through verification and the rollback window. Source
   retention starts again at successful mapping commit. Later, in a maintenance
   window with workers and dispatchers stopped, use a container with the original
   root **writable** to plan/execute source cleanup. Planning enqueues durable work:

   ```sh
   uv run --no-sync python scripts/cleanup_storage.py --user-id USER_UUID --local-sources
   uv run --no-sync python scripts/cleanup_storage.py --user-id USER_UUID --local-sources --run
   ```

   Source deletion is destructive and requires approval. Ordinary production
   reconciliation does not enqueue these local-source tasks, because production
   has no local material mount. Existing source cleanup work is cancelled during
   mapping cutover and reactivated only by the explicit local-source command.

This migrates registered versions, not a legacy directory. Older installations
must first finish [B2 import](FILE_STORAGE.md#import-and-cutover). A code rollback
does not undo storage mappings; retain the pre-cutover backup and sources.

## Cleanup inspection and recovery

`scripts/cleanup_storage.py --user-id USER_UUID` reconciles a bounded batch and
prints cleanup job IDs, states and safe errors. `--run` also executes due jobs;
`--retry JOB_UUID` requeues a failed cleanup belonging to that owner. Finite
automatic retry exhaustion stays visible for operator action. Repeated scans
reuse target/job records. Cleanup errors do not restore deleted files to the UI.

Only exact S3 VersionIds are deleted. Prefix listing filters exact keys so a
similarly named object's versions cannot become targets. The material role adds
ListBucketVersions on the material prefix; it retains no backup-bucket access.

## Backup and restore

Backups stop storage-maintenance with the other writers. Available completed
versions, including retained historical/deleted versions, are copied independently
into the backup bucket. Retiring/purged versions retain metadata but require no
content copy. Existing published backups restore from independent copies, so
material cleanup never deletes their only source or any backup objects.

Restore validates database state before remapping available S3 content to new
target VersionIds. It discards source-environment cleanup jobs/targets and migration
checkpoints before consumers start. Reconciliation then derives fresh targets
from the restored mappings; source-bucket deletion work is never replayed.

## External material imports

The repository provides an import contract and directory importer; it does not
contain or deploy the school's scraper or an existing external n8n workflow.
Until that workflow is upgraded/configured, web and Telegram sync return an
explicit unavailable result.

Configure INTERNAL_API_SECRET on the API and as a credential on the trusted
workflow runner. It is an administrative integration secret: every request must
also name an existing owning user. Do not expose this credential to a browser.

For each downloaded file, a workflow must:

1. POST `/api/internal/materials/upload-url` with X-Internal-Secret and JSON
   `user_id`, logical `path`, byte `size`, lowercase `sha256`, `content_type`, and
   `scheduled: true` for scheduled imports. Identical current content returns
   `reused: true`; do not upload it again.
2. PUT content to the returned S3 URL with its returned headers, **without** the
   application credential. Capture `x-amz-version-id`. For local development,
   PUT `/api/internal/materials/FILE_ID/versions/VERSION_ID/content?user_id=...`
   with the internal credential instead.
3. POST `/api/internal/materials/FILE_ID/versions/VERSION_ID/complete` with the
   credential and JSON `user_id`, `object_version` (S3 only). This creates the same
   durable indexing job as a browser upload; retry confirmation after a lost reply.

The ready-to-run adapter for a downloaded folder is:

```sh
uv run --no-sync python scripts/import_materials.py --base-url https://YOUR_SITE \
  --user-id USER_UUID --source-root DOWNLOADED_DIRECTORY
```

Add `--scheduled` for scheduled execution. `/pause` and `/resume` now store
per-account schedule state in PostgreSQL. A scheduled workflow should GET
`/api/internal/materials/schedule?user_id=...` before scraping; upload requests
also enforce the pause. Manual imports remain possible.

To enable web/Telegram triggers, set N8N_MATERIALS_WEBHOOK_URL and explicitly set
N8N_MATERIALS_PROTOCOL=versioned-v1 only after upgrading the external workflow. The
trigger POSTs `protocol`, authenticated `user_id` and optional `course`; the
workflow returns a JSON summary with `protocol: versioned-v1`, `written_count`,
`unchanged` and `errors`. Do not resume direct legacy-directory writes or a
write-then-full-rebuild workflow. AWS bootstrap passes these optional settings
from the application configuration secret to the runtime.

## Acceptance

`tests/services/test_storage_lifecycle.py` covers migration interruption/reuse,
source corruption, retained IDs/search paths, cleanup storage/commit failures,
download protection, abandoned completion races, S3 replay/delete-marker cleanup,
local source cleanup and authenticated imports. Existing file/index/job tests
remain part of the regression checks. AWS signature/IAM, real transfers, external
n8n workflow execution and cloud recovery still require environment acceptance.
