# Production delivery

Production is released from a workstation using a short-lived AWS CLI login:
committed source → ECR image pair → exact-image checks → SSM → EC2. GitHub stores
source and runs CI. Provisioning, HTTPS and initial configuration are described
in [the AWS guide](../infra/aws/README.md). `deploy.sh` is the separate local Docker
development workflow.

The backend image also runs the worker, dispatcher, recovery and storage-maintenance
services. Redis uses a private Docker network and a persistent directory. Runtime
and coordination database identities remain separate. AWS material bytes use a
private, versioned S3 bucket; see [S3_STORAGE.md](S3_STORAGE.md).

## Prepare the workstation

Install AWS CLI 2.32.0 or later, Terraform, Docker with Buildx, Python 3.12+ and uv.
Use an authenticated short-lived CLI profile. For console login:

```bash
aws login --profile classport
export AWS_PROFILE=classport
aws sts get-caller-identity
uv sync --frozen
```

Keep an existing IAM Identity Center profile if that is how the account is managed.
The profile must be allowed to publish/pull images in the two ECR repositories and
send `AWS-RunShellScript` to the application's EC2 instance and read its result.
Infrastructure provisioning additionally needs the permissions listed in the AWS
guide. EC2 uses its own instance role to read configuration and pull images.
Credentials stay in the local AWS login cache and are never committed or uploaded
to GitHub. An expired session must be renewed before release.

## First installation

1. Commit the release source and push it to the ClassPort repository. Require the
   existing CI checks to pass for that commit.
2. Configure `infra/aws/terraform.tfvars`, inspect a new plan, and apply the approved
   infrastructure. Preserve its state locally with restricted permissions.
3. Point the exact configured hostname at Terraform's `public_ip` in Cloudflare,
   then verify Caddy's HTTPS certificate.
4. Upload the installation configuration to the application secret as described in
   the AWS guide. Preserve existing session/encryption keys and database passwords.
5. If importing an existing installation, back up and migrate its database and
   registered file versions before starting production writers. Existing local bytes
   require the [storage cutover](STORAGE_LIFECYCLE.md#localstorage-to-s3-cutover);
   a new deployment does not automatically transfer a local database or Docker volume.
6. Run the release command below after DNS, configuration and any data cutover are ready.

## One release

From a clean, committed checkout whose CI has passed:

```bash
uv run --no-sync python -m scripts.release_local --profile classport
```

The command reads region, instance, public origin and ECR repository addresses from
this checkout's Terraform outputs. The selected AWS profile is used for all cloud
calls, and its account must match the image repositories. `PUBLIC_SITE_NAME`,
`PUBLIC_SHOW_LANDING` and `PUBLIC_GOOGLE_SITE_VERIFICATION` are optional frontend
build settings; their defaults are ClassPort, false and empty. Changing a build
setting requires a new source commit because image tags are immutable.

The command performs these steps:

1. Reject uncommitted source and resolve one full commit SHA.
2. Verify the AWS login and log in to ECR without printing the registry password.
3. Reuse existing immutable SHA images or build/push the missing image in the pair.
4. Pull the two exact registry digests and run `tests/test_release_images.py` against
   disposable local PostgreSQL/Redis and application containers. This checks image
   commit labels, bundled release files, login, assets, authenticated REST, file
   processing and MCP. A failed check stops before any production deployment.
5. Send one SSM command, wait for its final result, and verify the requested image
   pair and successful database/application outcomes.

Results are saved under the ignored `releases/<UTC-release-id>/` directory:

- `release.env` and `images.json`: commit, exact image pair and build/reuse decisions.
- `image-tests.xml`: local exact-image check results.
- `ssm-result.json`, `ssm-stdout.log`, `ssm-stderr.log`: command ID, invocation,
  database/application outcomes and the bounded host output.

Retain the whole release directory privately for operational evidence. The host
keeps attempt reports under `/opt/classport/attempts/COMMIT.RELEASE_ID-ATTEMPT/`
and full command logs under `/opt/classport/commands/`.

## Host switching and failures

The root-only `classport-release` launcher locks the host, pulls the requested
image digests and verifies their commit labels. It extracts Compose, the switch
script and bootstrap from that backend image. A short-lived maintenance container
prepares database roles, runs migrations once, preserves the existing operator
password and writes runtime/check configuration. Temporary secret files are removed.

The application switch starts both images and background services, checks public
HTTPS login, REST, file upload/index/search/download and MCP, and checks running
image references. Only success advances the current and previous release pointers.

Pull or database-preparation failure stops before application switching. Failure
after a switch restores the previous images/configuration and checks them again;
a first release has no previous version to restore. An SSM timeout or query failure
is recorded as unconfirmed remote state. Inspect that command and its saved report
before starting another release; the command is never resubmitted automatically.

## Emergency rollback

In an administrator SSM session, authenticate to ECR using the host role and run
the current release's rollback script:

```bash
sudo bash -c '
  set -euo pipefail
  set -a
  source /etc/classport/host.env
  set +a
  aws ecr get-login-password | docker login --username AWS --password-stdin "${BACKEND_REPOSITORY%%/*}"
  bash /opt/classport/releases/current/deploy-prod.sh --rollback
'
```

Keep both current/previous image pairs and their saved Compose files available.
Rollback checks the restored services and skips database initialization. A normal
release uses the local release command; directly invoking the application switch
script is not a complete release.

## Database compatibility and acceptance

Image rollback does not undo committed migrations or user writes. The previous
app continues running while migrations apply, so schema changes must support it
through the rollback window. Keep applied migration files immutable. Database
backup restoration is a separate recovery operation.

Local scripted tests and mocked Terraform validation do not demonstrate a cloud
release. Before accepting a release, run the local release command against AWS, verify
its recorded digests match the running containers, and confirm HTTPS, login, REST,
file processing and MCP checks. Keep the release directory and host command records
as acceptance evidence.

