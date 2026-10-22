# EC2 / RDS releases

For a fresh AWS account/environment, use the [Terraform AWS deployment](../infra/aws/README.md).
It provisions the host, private RDS, ECR, persistent storage, secrets and HTTPS,
including first-install database/account setup and an ECR/SSM release entrypoint.
The manual setup and GHCR workflow below remain available for existing hosts.

The local `deploy.sh` and `docker-compose.yml` retain their existing behavior.
Cloud releases use `deploy-prod.sh` and the standalone `docker-compose.prod.yml`.
EC2 downloads the CI-built images; it never builds source code or installs Node,
pnpm, Python, or application dependencies on the host.

## Prepare EC2 once

Use an x86-64 Linux EC2 instance: the release workflow builds `linux/amd64`.
Install Docker with Compose v2.30+, Bash, curl, and the usual Linux utilities
including `flock` and `mountpoint`. The SSH deployment user must be able to run
Docker and write `/opt/classport`.

Mount the persistent data disk at `/opt/classport/data`, and configure it to
mount after reboot. The script refuses to run if this is not a mount point,
preventing an absent EBS volume from silently sending uploads to the root disk.
It creates `data/courses` on that disk and binds it to `/opt/courses` in the app.
Put existing course data there using the current per-user directory layout before
the first cloud release. The cloud script does not run the historical file-layout
migration.

Configure EBS retention and snapshots separately. Follow the
[AWS EBS mount instructions](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-using-volumes.html)
for the device actually attached to your instance. Formatting/mounting disks are
administrator operations, not actions performed by the release script.

```text
/opt/classport/
  .env                      runtime RDS login and application settings
  .env.migration            migration RDS login and operator initialization
  .env.smoke                dedicated smoke account and MCP token
  certs/global-bundle.pem   RDS CA bundle
  data/                     mounted persistent disk
    courses/                current per-user course tree
  incoming/                 bundles transferred by CI
  releases/                 retained bundles and current/previous links
```

Configure RDS and separate runtime/migration roles as described in
[DATABASE.md](DATABASE.md#rds--external-postgresql). Both URLs must target the
same RDS database. Set `PGSSLMODE=verify-full` and
`PGSSLROOTCERT=/etc/classport/certs/global-bundle.pem` in both env files.
Provision runtime permissions/default grants before deployment. Do not pass the
migration URL to the runtime container.

Set `PUBLIC_URL` in `.env` to the public HTTPS origin. The outer TLS proxy forwards
this origin to `127.0.0.1:8080`; the backend has no host port in production. Allow
RDS traffic from EC2, without opening RDS to the internet. Production starts no
database container.

Copy `.env.smoke.example` to `.env.smoke` and supply:

- The public HTTPS origin (`SMOKE_BASE_URL`).
- A dedicated account's email/password, with TOTP disabled.
- A valid, unexpired MCP OAuth access token for that account, obtained through
  the existing OAuth flow. Renew it before expiry. Deployment does not mint tokens.

Prepare the account/token during initial database bootstrap, before the first
release. Checks log in and read that account's courses through REST and MCP; they
do not create or delete courses. Login records normal authentication audit and
rate-limit entries. Protect the three env files with mode `600`. They stay on EC2
and are never uploaded as release artifacts.

For private GHCR packages, log Docker on EC2 into GHCR with package read access.
Keep that credential on the host, outside release files.

## Configure GitHub Actions

Create these repository variables:

| Variable | Value |
|---|---|
| `PUBLIC_SITE_URL` | Required production HTTPS origin; baked into the frontend |
| `PUBLIC_SITE_NAME` | Optional display name; defaults to ClassPort |
| `PUBLIC_SHOW_LANDING` | Optional; defaults to false |
| `PUBLIC_GOOGLE_SITE_VERIFICATION` | Optional search verification value |

Create the `production` GitHub environment with these deployment secrets:

| Secret | Value |
|---|---|
| `EC2_HOST` | EC2 SSH hostname/address |
| `EC2_USER` | Deployment user |
| `EC2_SSH_KEY` | SSH private key for that user |
| `EC2_KNOWN_HOSTS` | Verified OpenSSH known-hosts entry for that hostname/address |

GitHub's token publishes two GHCR packages associated with the repository and
reads them back for verification. Enable package permissions if organization
policy restricts them. EC2 uses its own package read credential; GitHub's token
is not sent to EC2.

Trigger Deploy with an explicit ref, or push a `v*.*.*` tag. It:

1. Resolves one commit and runs source checks against it.
2. Builds/publishes both images and captures their registry digests.
3. Pulls those exact digests and tests them against disposable PostgreSQL:
   login, static assets, authenticated REST, MCP initialization and a read-only tool.
4. Creates the `production-release` artifact containing `release.env`, the
   production Compose file, and the cloud deployment script.
5. Transfers that verified bundle to EC2 and invokes the script.

`release.env` contains `RELEASE_COMMIT`, `BACKEND_IMAGE` and `FRONTEND_IMAGE`;
see `.env.release.example`. Full registry digests are mandatory. Mutable tags,
including commit tags, are rejected as deployment input. Changing frontend public
settings requires a new CI build because Vite embeds them.

## Deployment and recovery

The script locks deployment, validates the bundle, ensures both images are
available locally, runs one migration job using the candidate backend image, and
reconciles the operator with maintenance credentials. It updates the app/frontend
with building and further pulling disabled.

Readiness is checked through the frontend at `/api/health/ready`. The smoke job
uses `.env.smoke` to check the public login HTML and assets, log in and read the
course API, then initialize MCP, list tools and call `list_courses`. A 200 SPA
fallback or an unauthenticated MCP response does not count as success. The smoke
job runs inside the backend image; the host does not need Python.

Only a successful release advances `releases/current`; the previous successful
bundle becomes `releases/previous`. Repeating the same image pair and identical
Compose configuration preserves the existing rollback target. Each bundle retains
its Compose configuration and both digests. A failed pull/migration does not replace
containers. Failure after switching begins restores both old images/configuration
and repeats checks; changes to current/previous records are also undone.
Deployment stays failed even when recovery succeeds (exit 1); failed recovery
exits 2. A failed first deployment has no recorded version to restore and requires
operator attention. Do not prune images needed by current/previous, delete their
bundles, or expire their GHCR packages.

Restore the previous successful image pair manually:

```bash
bash /opt/classport/releases/current/deploy-prod.sh --rollback
```

Manual rollback skips migrations/initialization, obtains missing images from the
saved digests if necessary, and verifies the result. Successful rollback makes the
replaced release available as `previous`.

To deploy a downloaded `production-release` artifact, extract it on EC2 and run:

```bash
bash /path/to/bundle/deploy-prod.sh /path/to/bundle
```

For a different root set `CLASSPORT_DEPLOY_ROOT` on every invocation; its `data/`
must be the mounted persistent disk. App secrets, RDS credentials, proxy settings
and data are shared host state, not snapshotted by an image release.

## Database compatibility

Migration runners use a database advisory lock: competing runners fail before
reading/applying stale migration lists. The pending batch is transactional; a
failed file rolls back that batch. A successful invocation commits before the app
is replaced. Image rollback never undoes committed SQL, initialization or user writes.

The old app runs during migrations. New migrations must support that app during
release and throughout the rollback window. Add fields first, migrate reads/writes
and backfill in compatible releases, and remove old fields after the window ends.
Keep applied migration files immutable. For schema/data changes test the previous
image on the upgraded schema; readiness alone cannot prove business compatibility.
Database backup restoration needs a separate, explicitly planned recovery operation.
