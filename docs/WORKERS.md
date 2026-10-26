# Background file processing

Uploading a file and making it searchable are separate operations. The API
accepts immutable bytes and confirms the version; independent workers extract
text and publish a search index. PostgreSQL records all task state. Redis carries
job IDs, and Celery runs the processing in separate child processes.

## Upload flow

1. `POST /api/files/upload-url` creates a pending version and returns the PUT URL.
2. PUT the bytes with the returned headers. This stages bytes without making
   the pending version current. Repeated identical pending PUTs are allowed;
   completed version bytes cannot be changed.
3. `POST /api/files/{file_id}/versions/{version_id}/complete` checks ownership,
   content presence, size and checksum. One transaction completes the version,
   advances the current revision, creates its job and saves an outbox notification.
   Repeated confirmation returns the same job. An older upload cannot replace
   a newer completed revision.
4. The dispatcher sends the job ID to Redis. The worker reads the trusted user,
   file, version and processing type from PostgreSQL, then atomically claims an
   execution. It releases database connections while reading and parsing.
5. A short transaction checks the file's current version and the execution's
   identity/lease, then saves the index and job/attempt success together.

The web file browser displays transfer, completion confirmation and processing
separately. If confirmation fails or its response is lost, the browser retains
the original file/version IDs and offers confirmation again without another PUT
or version allocation. These pending confirmations are kept in the current file
browser session.

Upload summaries track jobs by ID across folder changes. New batches retain
earlier summaries, and manual processing retries resume task polling. Polling
stops on terminal states and refreshes file/search caches even when the first
task response is already terminal. Directory listings also invalidate search
when they first observe a completed task. Only an indexed current version with
successful processing is labelled searchable in the file list.

`GET /api/jobs/{job_id}`, `GET /api/jobs?file_id=...` and
`POST /api/jobs/{job_id}/retry` all restrict access to the authenticated user.
Permanent errors, deleted files and superseded versions cannot be retried.
Temporary failures can be retried manually without resetting attempt history.

## Processes and credentials

| Service | Responsibility | Database access |
|---|---|---|
| `classport` | REST/MCP, upload confirmation and queries | Ordinary application login |
| `worker` | Text extraction, index publication and fixed-object cleanup | Ordinary application login |
| `storage-maintenance` | Bounded cleanup reconciliation | Ordinary application login |
| `dispatcher` | Send due outbox notifications | Restricted `classport_jobs` login |
| `job-recovery` | Expired executions and missing notifications | Restricted `classport_jobs` login |
| `redis` | Notification transport with AOF persistence | None |

API and worker processes create their own database pools. Each Celery prefork
child creates its event loop after fork and uses that same loop for its pool.
No connection is created in the Celery parent. Graceful exit closes the pool;
forced termination is recovered through database leases.

Local workers mount the LocalStorage content root writable for physical cleanup. AWS production
workers use fixed S3 versions and a restricted credential_process grant; see
[S3_STORAGE.md](S3_STORAGE.md). Coordinators
have no content mount, web session secret, migration credentials, or access to
user credential tables. Redis exposes no host port. API readiness does not
depend on Redis, so confirmed uploads can accumulate safely while it is down.

## Local configuration

Copy `.env.coordinator.example` to `.env.coordinator` and set its separately
provisioned database login. Copy `.env.jobs.example` to `.env.jobs` and keep all
three task settings in that shared file. Compose loads `.env.jobs` after each
service's credential file for the API, worker, dispatcher and recovery process.
Set task parameters only in `.env.jobs`. These local files are ignored by Git;
`.env.migration` remains a maintenance-only file.

After applying the normal SQL migrations, an administrator provisions the
coordinator role. Use the actual database name, and set the password interactively:

```sql
CREATE ROLE classport_jobs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password classport_jobs
GRANT CONNECT ON DATABASE classport TO classport_jobs;
GRANT USAGE ON SCHEMA public TO classport_jobs;
GRANT SELECT, INSERT, UPDATE ON public.jobs, public.job_attempts, public.outbox
    TO classport_jobs;
```

The existing `deploy.sh` starts the background services with the application.
Database role provisioning is an administrator operation, not a side effect of
starting a worker. AWS bootstrap provisions this role and writes the separate
coordinator environment file using `COORDINATOR_DB_PASSWORD` from the application
secret. Existing installations add this one new secret field while preserving
all existing passwords, session keys and encryption keys.

Default execution settings are a 120-second hard deadline, a 150-second lease
and three automatic attempts. Temporary failures wait 5 then 10 seconds before
retrying. Recovery runs every 5 seconds and replenishes due notifications older
than 60 seconds. `JOB_TIME_LIMIT`, `JOB_MAX_ATTEMPTS` and
`JOB_REDELIVERY_SECONDS` are configured together in `.env.jobs`. Restart the API
and all background services after changing them. Celery does not maintain a
separate business retry counter or result backend.

On AWS, the versioned application secret is the configuration source. It accepts
these three optional keys as integer strings, with the defaults above. Bootstrap
and restore generate `.env.jobs` from that secret; edit the secret for cloud
changes, since the next release regenerates the file. Backups retain the secret
version, so restore regenerates the same task settings.

Workers default to one child, a separate 1–2 connection pool, a 512 MiB memory
limit and one CPU. `WORKER_CONCURRENCY` controls child count through Compose
interpolation. Account for every child when budgeting database connections.
Separate processes still share their host's total CPU and memory. Workers on another host need connectivity to the database, Redis and S3, plus
the restricted material credentials. Production workers need no material disk.

## Rebuilds, delivery and recovery

Both internal rebuild endpoints require the internal secret and an explicit
user. They persist individual jobs and return job IDs; they do not parse files.
The operator CLI follows the same path:

```sh
uv run --no-sync python scripts/index_files.py --user-id USER_UUID
uv run --no-sync python scripts/index_files.py --all-users
```

Change `PIPELINE_VERSION` when processing behavior changes. A rebuild creates
jobs for that pipeline while reusing jobs already registered for it. Workers
explicitly fail unsupported processing versions instead of silently substituting
another pipeline.

Application and background services share the same backend image digest in
production. Releases stop the old background executors before switching them;
rollback restores the recorded service group. The release smoke check uploads a
small file, waits for success, checks search/download/MCP and soft-deletes it.
Immutable object cleanup follows [the B8 retention policy](STORAGE_LIFECYCLE.md).

Maintenance-window backups stop API, worker, dispatcher, recovery and storage-maintenance writers,
then verify clean shutdown before exporting data. Restore targets use a fresh
Redis directory. Before starting consumers, `app.job_coordination restore --once`
expires restored executions and rebuilds notifications from PostgreSQL.
Redis backups are not needed to determine which jobs remain unfinished.

Logs correlate request, job, user, file, version, attempt and execution IDs.
The restricted coordinator exports queue age, outbox age, processing duration,
failure rate, retries and expired leases. Host monitoring forwards these to
CloudWatch with alarms for stalled dispatch/recovery and growing waits.

## Validation

Run `pnpm test` in `web/` for the focused upload and query behavior tests. They
exercise lost completion responses, batch failure isolation, folder changes,
search refresh, first-observed terminal states and polling after manual retry.
The frontend CI job runs these tests alongside the production build.

`tests/services/test_jobs.py` covers completion atomicity, duplicate handling,
safe errors, bounded retries and execution fencing. `tests/test_worker_processes.py`
uses committed PostgreSQL data, real Redis, Celery prefork children and separate
coordinator processes. It covers lost Redis notifications, interruption after
publishing but before recording dispatch, worker termination, user separation
and restricted database permissions. The broker fixture waits for Redis PING;
failed process checks include child logs, and failed dispatch interruption checks
include database task/outbox state. Compose and bootstrap tests exercise shared
non-default task settings while checking credential separation.

`tests/test_release_images.py` starts the image-built API, worker, coordinators
and frontend with shared disposable content storage. It exercises the same
authenticated smoke script used by production deployment. Local checks do not
substitute for AWS deployment, alarm delivery or a cloud restore drill.

Metric definitions, local fault coverage and repeatable task measurements are
documented in [BACKGROUND_VALIDATION.md](BACKGROUND_VALIDATION.md).
