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

The web file browser displays upload outcomes separately from processing status.
It polls active work and stops polling on terminal states. Only an indexed
current version with successful processing is labelled searchable.

`GET /api/jobs/{job_id}`, `GET /api/jobs?file_id=...` and
`POST /api/jobs/{job_id}/retry` all restrict access to the authenticated user.
Permanent errors, deleted files and superseded versions cannot be retried.
Temporary failures can be retried manually without resetting attempt history.

## Processes and credentials

| Service | Responsibility | Database access |
|---|---|---|
| `classport` | REST/MCP, upload confirmation and queries | Ordinary application login |
| `worker` | Text extraction and index publication | Ordinary application login |
| `dispatcher` | Send due outbox notifications | Restricted `classport_jobs` login |
| `job-recovery` | Expired executions and missing notifications | Restricted `classport_jobs` login |
| `redis` | Notification transport with AOF persistence | None |

API and worker processes create their own database pools. Each Celery prefork
child creates its event loop after fork and uses that same loop for its pool.
No connection is created in the Celery parent. Graceful exit closes the pool;
forced termination is recovered through database leases.

Workers currently mount the LocalStorage content root read-only. Coordinators
have no content mount, web session secret, migration credentials, or access to
user credential tables. Redis exposes no host port. API readiness does not
depend on Redis, so confirmed uploads can accumulate safely while it is down.

## Local configuration

Copy `.env.coordinator.example` to `.env.coordinator` and set its separately
provisioned database login. The file is ignored by Git. Application `.env`
continues to contain only runtime configuration; `.env.migration` remains a
maintenance-only file.

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
`JOB_REDELIVERY_SECONDS` can be set in runtime configuration; workers and
coordinators must use the same values. Celery does not maintain a separate
business retry counter or result backend.

Workers default to one child, a separate 1–2 connection pool, a 512 MiB memory
limit and one CPU. `WORKER_CONCURRENCY` controls child count through Compose
interpolation. Account for every child when budgeting database connections.
Separate processes still share their host's total CPU and memory. Moving workers
to another host also requires access to the content; the plan's S3 stage handles
that later.

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
Immutable object cleanup remains part of the storage retention work.

Maintenance-window backups stop API, worker, dispatcher and recovery writers,
then verify clean shutdown before exporting data. Restore targets use a fresh
Redis directory. Before starting consumers, `app.job_coordination restore --once`
expires restored executions and rebuilds notifications from PostgreSQL.
Redis backups are not needed to determine which jobs remain unfinished.

Logs correlate request, job, user, file, version, attempt and execution IDs.
The restricted coordinator exports queue age, outbox age, processing duration,
failure rate, retries and expired leases. Host monitoring forwards these to
CloudWatch with alarms for stalled dispatch/recovery and growing waits.

## Validation

`tests/services/test_jobs.py` covers completion atomicity, duplicate handling,
safe errors, bounded retries and execution fencing. `tests/test_worker_processes.py`
uses committed PostgreSQL data, real Redis, Celery prefork children and separate
coordinator processes. It covers lost Redis notifications, interruption after
publishing but before recording dispatch, worker termination, user separation
and restricted database permissions.

`tests/test_release_images.py` starts the image-built API, worker, coordinators
and frontend with shared disposable content storage. It exercises the same
authenticated smoke script used by production deployment. Local checks do not
substitute for AWS deployment, alarm delivery or a cloud restore drill.
