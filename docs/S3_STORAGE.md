# S3 material storage (B7)

The cloud runtime stores material bytes in a private, versioned bucket, separate
from the backup bucket. PostgreSQL remains the authority for ownership, logical
paths, file revisions, current versions and processing jobs. Local development
continues to use `STORAGE_BACKEND=local` and `STUDY_ROOT`.

## Upload and confirmation

1. The browser reads the file and computes SHA-256. It posts `path`, `size`,
   lowercase hexadecimal `sha256` and `content_type` to `/api/files/upload-url`.
2. The API creates a pending application version, generates its private object
   key, and returns `file_id`, `version_id`, `storage_backend`, `url`, `headers`
   and `expires_in`. The browser cannot choose the bucket, owner or physical key.
3. The browser PUTs the file directly with the returned signed content type and
   base64 `x-amz-checksum-sha256`. It sends no application cookies to S3.
4. S3 validates the checksum and returns `x-amz-version-id`. Bucket CORS must
   expose that header to JavaScript. A missing or `null` version is an error.
5. The browser posts `{"object_version":"S3-VersionId"}` to
   `/api/files/{file_id}/versions/{version_id}/complete`.
6. The API checks ownership and HEADs that exact key/version with checksum mode
   enabled. It compares size, checksum and content type without reading the
   content body. A short transaction rechecks state and atomically commits the
   fixed object version, completion, current revision, job and outbox.

Repeated confirmation returns the existing job. A completed application version
cannot be rebound to a different S3 version. Reusing a still-valid PUT URL may
create another S3 version, but worker, MCP and download reads retain the confirmed
VersionId. An older application revision completing late cannot become current.
ETag is not used as a content hash. Completed S3 rows require a non-null, nonempty,
non-`null` object version at the database boundary.

The local upload endpoint remains for local versions; it rejects S3 targets.
Service-side uploads also record the S3 VersionId returned by PUT.

## Downloads and preview

Signed-url and raw endpoints authenticate the caller before resolving metadata.
S3 downloads pin the confirmed VersionId. Raw responds with an uncached 307
redirect. The signature sets the current filename, content type and safe
inline/attachment disposition. S3 handles ranges; text fetches require CORS.
MCP and indexing download fixed versions through the storage interface.

S3 URLs are bearer grants until expiry: deleting a file stops new grants but does
not revoke an already-issued grant. Download responses use `private, no-store`.
The browser refreshes the URL before its returned lifetime expires. Lifetime is
capped by `S3_URL_TTL_SECONDS` (300 by default) and the signing credential's
remaining lifetime, with a 30-second margin.

## Runtime configuration and credentials

Cloud bootstrap writes these non-administrative settings to the runtime env:

- `STORAGE_BACKEND=s3`, `S3_BUCKET`, `AWS_REGION`.
- `AWS_PROFILE=classport-storage`.
- `AWS_CONFIG_FILE=/run/classport-storage/config`.
- `AWS_EC2_METADATA_DISABLED=true`.

The host assumes the dedicated material role for one hour and refreshes the
published grant every five minutes. `/run/classport-storage` contains only an AWS
profile and the restricted STS grant. Files are replaced atomically, are root-only,
and are mounted as a directory read-only by API/worker. The SDK's
`credential_process` rereads the grant when its cached credentials approach expiry;
no container restart is required. Docker startup waits for the credential service
after a host reboot, recreating the runtime directory and profile before containers.

The application containers cannot use the host's high-privilege IMDS identity:
IMDSv2 remains required with hop limit 1. Coordinators, frontend and smoke services
receive no storage-credential mount. The material role can inspect versioning,
list the material prefix, and put/read/delete specific material versions. It has
no Secrets Manager, backup-bucket or database-administration access.

Terraform configures private access, SSE-S3 encryption, Versioning, HTTPS-only
access and CORS for the configured HTTPS origin. Browser methods are PUT/GET/HEAD;
request headers and exposed version/checksum/range headers are explicit.
Readiness checks bucket versioning and a bounded prefix listing within the existing
readiness timeout. Local endpoint overrides (`S3_ENDPOINT_URL`) are for tests only.

## Deployment and existing data

The backend image bundles the host credential installer. The host release installs
and refreshes it before switching application containers. Production Compose has
no material-directory mounts. Redis and maintenance backups still use host storage.
The host's environment must include `S3_BUCKET` and `STORAGE_ROLE_ARN` generated
by the updated Terraform configuration.

Existing available completed LocalStorage versions require B8 migration and verification
before switching to the production S3 configuration. Changing a default backend
does not move existing bytes. Keep old sources and backups through the rollback
window; do not delete old directories or introduce automatic object expiry here.

Backups enumerate all completed S3 versions, including historical versions. They
read each exact source version, verify bytes/checksum, and write an independent
copy into the backup bucket before publishing the manifest. Restore uploads copies
to the target material bucket and updates database mappings with the **new**
VersionIds. Source VersionIds are never assumed valid in another bucket. [B8 lifecycle](STORAGE_LIFECYCLE.md) implements abandoned-upload cleanup,
retention, physical-version reconciliation and restore-time cleanup reset.

## Validation status

Local checks cover version binding, metadata rejection, atomic task publication,
fixed-version reads, authorization, SDK credential refresh, MCP, preview redirects,
production mounts, a separate ordinary worker reading a fixed remote version,
and independent backup mapping executed against real PostgreSQL. The local S3 emulator needs a
REST checksum-header correction documented in `tests/s3_emulator.py`; it does not
prove AWS signature, IAM, checksum enforcement or cloud CORS behavior.

Actual AWS acceptance remains required: real browser upload/preview, replayed PUT,
worker and MCP without material mounts, IAM denial of host administration, an STS
rotation, backup and isolated restore. No AWS resources are created by unit tests
or mocked Terraform tests. Browser overhead measurements and their limitations
are in [UPLOAD_BENCHMARK.md](UPLOAD_BENCHMARK.md).
