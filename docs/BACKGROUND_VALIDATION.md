# Background task validation and measurements

Uploads are accepted when their database transaction commits. Independent workers
then read a fixed file version, extract text and publish the search index. A
confirmed upload remains recoverable while Redis or a worker is unavailable.
PostgreSQL is the source of task state; Redis carries notifications.

## Repeatable local checks

The regular rollback-based tests remain in `tests/conftest.py`. The process tests
use committed data, ordinary runtime/coordinator database roles, temporary
storage, real Redis, an HTTP API and Celery prefork children. Their subprocess
launcher is shared with the measurement script in `tests/processes.py`.

`tests/test_worker_processes.py` covers duplicate delivery, lost notifications,
interrupted dispatch, worker loss, API acceptance during Redis downtime, API
termination before commit and after commit but before its response, natural hard
timeout/lease recovery, non-default attempt limits, two users concurrently using
REST/MCP/workers, permanent parsing errors and invalid versions. Commit boundaries
are observed using locks and triggers in the disposable database; the production
application has no test fault-injection endpoints.

`tests/test_worker_s3.py` sends a job through Redis and an actual Celery worker
against local Moto S3. It verifies reading the confirmed object version after a
newer, unconfirmed version exists, without a material directory. This does not
validate AWS IAM, signing, CORS or real S3 behavior.

`tests/test_recovery_round_trip.py` restores a real database export into a second
instance. It starts with an empty Redis, runs the separate restore coordinator,
then starts the dispatcher and worker and waits for the restored task to succeed.
It also checks recovered login, application data, PDF content and MCP access.
The S3 mapping test executes the generated SQL with psql inside the disposable
database container, including its JSON input, without relying on host networking.

CI retains JUnit results and subprocess logs in the `backend-tests` artifact for
14 days. Set `PROCESS_LOG_DIR` to retain local subprocess logs outside temporary
test directories. No production credentials are needed for these checks.

## Metric definitions

The coordinator reports the following values for all jobs and separately with
`Index` and `Cleanup` prefixes for `index_file` and `storage_cleanup` jobs:

| Metric | Meaning |
|---|---|
| `JobWaitingAgeSeconds` | Oldest currently due task's age since `next_run_at`; excludes intentional retention and retry backoff |
| `JobWaitingCount` | Number of currently due queued/retrying jobs |
| `JobOutboxAgeSeconds` | Oldest due, pending notification's age since `available_at` |
| `JobExpiredLeases` | Running jobs whose lease has expired at sampling time |
| `JobExpiredAttempts` | Attempts recorded as expired in the last 15 minutes, including already recovered ones |
| `JobProcessingSamples` | Attempts that succeeded or failed in the last 15 minutes; excludes cancellations and expired attempts |
| `JobProcessingSeconds` | Mean duration of those attempts |
| `JobProcessingP50Seconds`, `JobProcessingP95Seconds` | Duration percentiles across those individual attempts |
| `JobCompletedCount`, `JobFailureRate` | Number of currently terminal succeeded/failed jobs finished in the last 15 minutes, and percentage failed |
| `JobRetries` | Attempts started in the last 15 minutes whose previous attempt failed or expired; normal cleanup deferrals do not count |

These are snapshots or sliding-window values. Summing successive overlapping
15-minute samples would count the same events multiple times. A zero duration
with zero samples means no observations, rather than instant processing.

Workers log `queue_wait_ms` at claim and committed `processing_ms` and
`end_to_end_ms` at completion. Index operations log `index.read`, `index.extract`
and `index.publish` durations. Task IDs, execution IDs, task type and pipeline
version correlate these events. Registration and outcome logs are emitted after
their owning transaction has completed, so a rolled-back index write cannot
produce a successful task event. PostgreSQL remains the authoritative outcome.

Host monitoring also publishes `JobDispatcherReady` and `JobRecoveryReady` from
the actual long-running containers' health checks. `JobMetricsReady` separately
reports whether the one-shot database metric query succeeded. The two new health
alarms detect stopped/unhealthy coordinators even with no queued tasks. Existing
queue-age, outbox-age and expired-lease alarms remain. Cloud delivery and email
notification still require actual AWS acceptance.

## Local performance measurement

Run from the repository root using the existing development environment:

```sh
.venv/bin/python scripts/benchmark_jobs.py --files 6 --text-kib 256 --pdf-pages 5 \
  --concurrency 1 2 --repeats 3 --output docs/measurements/b9-jobs-local.json
```

The script creates disposable PostgreSQL and Redis containers and separate API,
dispatcher, recovery and worker processes. It logs in through the API, uploads
alternating text/PDF files, waits for completion, and verifies search results.
Each worker configuration gets a warm-up file before its measured batch. The
report records each job's file size, wait/processing/total task duration, outcome
and attempts; each batch includes throughput and latency percentiles. It also
retains individual read/extract/publish stage timings, source commit, dirty-tree
status and task settings. Startup and warm-up are excluded from batch throughput.

`task_seconds` starts at task registration in PostgreSQL. `batch_seconds` starts
before the batch uploads and ends after completion and search verification.
Neither includes browser file selection or rendering. Run this measurement after
other integration tests finish so they do not compete for the same host.

Results are local measurements of this workload and worker configuration. They
are not an estimate of AWS speed, a comparison with the pre-change application,
or proof that increasing concurrency always improves throughput. Cloud restart,
rollback, alarm delivery and combined S3/database recovery remain separate drills.

## Recorded local result — 2026-10-25 (America/New_York)

The focused backend check covered 124 cases. The batch passed 123; after correcting
the new multi-user test's MCP URL construction, that case passed on a targeted
rerun. The full batch was not rerun. Ruff and whitespace checks passed, as did all
six Terraform mock tests and configuration validation. The case-level record is
[measurements/b9-validation-local.json](measurements/b9-validation-local.json).

The measurement script completed six batches and 36 measured jobs, with zero
failures and zero retries. Each batch contained three 256 KiB text files and
three five-page PDFs (2,430 bytes each). There were three repeats per worker setting.

| Worker processes | Median throughput across batches | Median task wait across jobs | Median processing time across jobs |
|---|---:|---:|---:|
| 1 | 1.49 files/s | 1,133 ms | 107 ms |
| 2 | 1.56 files/s | 980 ms | 146 ms |

The raw report includes per-batch P50/P95 and all 108 measured index stage timings:
[measurements/b9-jobs-local.json](measurements/b9-jobs-local.json). The run used
Python 3.13.12 and the recorded commit with uncommitted changes. These numbers
establish a local baseline for the specified small-file workload; they do not
establish a before/after application performance gain or a cloud result.
