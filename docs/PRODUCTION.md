# Production delivery

Production uses one GitHub Actions → ECR → SSM → EC2 release flow. The local
`deploy.sh` remains the local Docker development workflow. Provisioning, HTTPS,
persistent storage and initial secrets are described in [the AWS guide](../infra/aws/README.md).

The backend digest also runs the file worker, dispatcher and recovery services.
Redis uses a private Docker network and its own persistent directory. The
coordinators receive `.env.coordinator` with a restricted database identity;
normal releases include background processing in switching and rollback. See
[WORKERS.md](WORKERS.md) for configuration and task recovery.

## Configure the release environment

Create the GitHub `production` environment. Restrict its deployment branches/tags
to `main` and protected `v*.*.*` release tags; restrict who may create those tags.
Protect `main` with required CI checks. Both the publisher and deploy job use this
environment. Required reviewers are optional; configuring them pauses those jobs
for approval instead of allowing fully automatic releases.

Set `github_oidc_subject` in Terraform to this repository's actual production
OIDC subject. Use the exact subject, including immutable owner/repository IDs
when GitHub uses them. Its final segment must be `:environment:production`.
If the AWS account already has the GitHub OIDC provider, supply
`github_oidc_provider_arn` to reuse it. The default null subject grants no GitHub
access. See [GitHub's AWS OIDC guide](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).

Set these variables in the production environment:

| Variable | Terraform output or value |
|---|---|
| `AWS_REGION` | `aws_region` |
| `AWS_DEPLOY_ROLE_ARN` | `github_deploy_role_arn` |
| `AWS_INSTANCE_ID` | `instance_id` |
| `BACKEND_REPOSITORY` | `backend_repository` |
| `FRONTEND_REPOSITORY` | `frontend_repository` |
| `PUBLIC_SITE_URL` | `public_url`, the same HTTPS origin configured on the host |
| `PUBLIC_SITE_NAME` | Optional; defaults to ClassPort |
| `PUBLIC_SHOW_LANDING` | Optional; defaults to false |
| `PUBLIC_GOOGLE_SITE_VERIFICATION` | Optional |

The GitHub role can publish/read the two ECR repositories and send the approved
shell document to the one EC2 instance. It cannot read Secrets Manager or manage
IAM. SSM executes as root on that host, so granting this deployment role is a
privileged trust decision. The host's separate role reads application/database
configuration. No AWS access key, SSH key, application password or database URL
is stored in GitHub release configuration.

## One release

Push to protected `main`, push a protected version tag, or use Deploy's manual
Run workflow button. Every target must resolve to a commit on `main` history.
All triggers follow the same gates:

1. Resolve one commit and run existing source checks against that SHA.
2. Query each immutable ECR SHA tag. Reuse an existing image; build/push only a
   missing image. This also resumes a run that published just one image.
3. Pull both registry digests, verify their commit labels and bundled release
   files, then test login, assets, authenticated REST and MCP against disposable
   PostgreSQL. Reused images are tested again on every run.
4. Save `release.env` with the commit and both digests, plus `images.json`.
5. Use a short-lived OIDC session to send one SSM command. Wait for its final
   status and exit code, then validate the host result and running image pair.

A SHA tag is immutable. Changes to frontend public settings require a new commit;
rerunning an old commit intentionally reuses its originally built frontend.

The root-only `classport-release` entrypoint locks the host, pulls both digests
and verifies their commit labels. It extracts Compose, `deploy-prod.sh` and
`bootstrap.py` from `/app/release` in that backend image to a version directory.
Application release files are never read from EC2 user data.

The one maintenance container uses that backend digest to prepare roles, run
migrations once, grant runtime access, seed the operator/check account and write
runtime configuration, in that order. Only this short-lived container sees
administrator and migration credentials. Secret input files under `/run` are
removed on exit; no `.env.migration` file is retained on the AWS host.

The versioned `deploy-prod.sh` switches both application images with building
disabled. It checks readiness through the frontend, performs public HTTPS login,
REST and MCP checks, uploads a small file and waits for searchable processing,
then verifies download/MCP reads. It inspects API, frontend and background image
references against the requested digests. Only success advances `releases/current`
and `previous`.

## Results and failures

GitHub retains these artifacts for 90 days:

- `production-release`: commit, exact image pair and build/reuse results.
- `production-deployment-result`: the manifest, SSM command ID, invocation status,
  output tail, database step outcomes, application checks and recovery result.
- `production-pipeline-result`: check/publish/deploy job results, including skipped
  jobs when an earlier gate failed. The run summary shows the same stage results.

The host saves `/opt/classport/attempts/COMMIT.RUN_ID-ATTEMPT/result.json`, including
start/end times, requested images, database preparation and application outcomes.
Database and application reports are kept alongside it. The SSM launcher stores
the full command log under `/opt/classport/commands/COMMIT.RUN_ID-ATTEMPT.log` and
returns a bounded tail plus the structured result to GitHub.

A failed check never starts production deployment. Pull/database preparation
failure stops before the application switch. Failure after switching starts
restores both old images/configuration and repeats their checks. Recovery success
still returns failure (exit 1); failed recovery returns exit 2. A failed first
release has no previous version to restore. Current/previous promotion failures
also restore the recorded pointers.

An SSM timeout or query failure is recorded as unconfirmed remote state, never as
success. Inspect the saved command ID and host result before starting another
release; a second command is not automatically sent. The host lock also prevents
an overlapping application deployment. See [SSM command results](https://docs.aws.amazon.com/cli/latest/reference/ssm/get-command-invocation.html).

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
release is always initiated through GitHub; directly invoking the application
switch script is not a complete release.

## Database compatibility and acceptance

Image rollback does not undo committed migrations or user writes. The previous
app continues running while migrations apply, so schema changes must support it
through the rollback window. Keep applied migration files immutable. Database
backup restoration is a separate recovery operation.

Local scripted tests and mocked Terraform validation do not demonstrate a cloud
release. Before accepting A7, run the GitHub workflow against AWS, verify its
recorded digests match the running containers, confirm HTTPS/login/REST/MCP
checks, and exercise the changed failure/recovery path. Keep the resulting run
and command records as acceptance evidence.

