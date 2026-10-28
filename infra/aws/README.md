# Minimal AWS deployment

This creates one Ubuntu 24.04 x86-64 EC2 host, an encrypted persistent EBS disk,
private single-AZ PostgreSQL 16 RDS, two immutable ECR repositories, a private
versioned backup bucket, Secrets Manager configuration, and CloudWatch logs.
The VPC has one public app subnet and two private database subnets. Only TCP
80/443 are public; PostgreSQL accepts only the app security group. Administration
uses Systems Manager Session Manager; no SSH key or inbound SSH port is needed.
Caddy obtains and renews a public HTTPS certificate once a real domain is
configured, and forwards to the existing frontend on `127.0.0.1:8080`. With no
domain it serves only a configuration notice on HTTP, requests no certificates
and blocks application releases. Existing app routes and MCP streaming are preserved.
The AWS region defaults to `us-east-2` and can be overridden with `aws_region`.
DNS is managed in Cloudflare; this module creates no Route 53 resources.

The current preparation status, validation results, and account/domain/GitHub
steps are recorded in [the cloud setup report](../../docs/CLOUD_SETUP_REPORT.md).

This is a small single-host installation, without high availability or a NAT
gateway. EC2, EBS, RDS, public IPv4, Secrets Manager, logs and S3 usage incur AWS
charges. Creating resources, uploading secrets/images, changing DNS and deploying
must be approved by the operator before execution.

## Files and dependencies

| Files | Responsibility |
|---|---|
| `main.tf`, `iam.tf`, `variables.tf`, `outputs.tf` | AWS resources and restricted instance permissions |
| `cloud-init.yaml`, `scripts/setup-host.sh` | Install Docker Compose/Caddy/AWS CLI, identify and mount EBS, configure HTTPS/logging |
| `scripts/create-config.py` | Generate initial secrets locally with hidden password input |
| `scripts/bootstrap.py` | Provision DB identities, apply existing migrations and initialize operator/release checks |
| `scripts/release.sh`, `../../scripts/release_local.py`, `../../scripts/publish_release.py`, `../../scripts/deploy_via_ssm.py` | Local release flow: publish/reuse ECR images, invoke SSM and retain results |
| `tests/environment.tftest.hcl`, `../../tests/test_aws_bootstrap.py` | Mock AWS tests and real local PostgreSQL permissions/bootstrap tests |

The backend image includes its Compose file, application switch script and DB
bootstrap helper under `/app/release`. The stable host launcher extracts those
files from the selected digest on every release. Application releases do not
change user data or rebuild EC2. No app API or schema migration is changed by
this infrastructure module.

## Local validation (no AWS account)

These commands install provider dependencies and check the local configuration.
Tests use mocked AWS responses and cannot contact AWS or create resources.
No domain, certificate request or AWS credentials are needed.

```bash
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
```

## 1. Future AWS credentials and provisioning

Prerequisites: an AWS account with authenticated CLI credentials and permission
to create the declared VPC, EC2, IAM, RDS, ECR, S3, Secrets Manager and logs
resources; Terraform 1.13.5+ and Python 3 for initial configuration. For interactive SSM access install the AWS Session Manager plugin
locally. This module targets standard commercial AWS regions with two AZs.

Prefer an AWS CLI profile using IAM Identity Center/SSO or another short-lived
credential source. For example, configure and log in with `aws configure sso
--profile classport`, then set `AWS_PROFILE=classport` in the shell running
Terraform and administrative SSM. Terraform and AWS CLI use the normal credential
chain; never put access keys in `.tfvars`, user data or Git. `aws sts
get-caller-identity` can confirm the intended account without printing keys.
An existing console login profile created with `aws login --profile classport`
is also supported by the installed provider. Select it with
`export AWS_PROFILE=classport` before planning; the provider must be able to
refresh that profile's short-lived cache under `~/.aws/login/cache`. No static
access keys or credential-export wrapper are needed for this checkout.
The provisioning identity needs IAM/PassRole permissions and access to the listed
services; the local publisher needs ECR/SSM rights, the configuration operator needs
`secretsmanager:PutSecretValue` on this application secret, and the administrator
needs SSM session rights. EC2 receives its own separate instance role.

Once the account and real domain are ready, from the repository root:

```bash
cp infra/aws/terraform.tfvars.example infra/aws/terraform.tfvars
# Set a unique environment name and your real domain_name; acme_email is optional.
# Override aws_region if needed (default us-east-2).
terraform -chdir=infra/aws init
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
terraform -chdir=infra/aws plan -out=environment.tfplan
# After operator approval of the plan and ongoing charges:
terraform -chdir=infra/aws apply environment.tfplan
```

State/plan files and local variable files are ignored by Git. Keep state in a
restricted directory, or configure a separately provisioned encrypted remote
backend with locking before the first apply. The backup bucket here is for app
backups, not a bootstrap backend. Commit `.terraform.lock.hcl` for reproducible
provider installation. Secret values never enter Terraform: RDS manages its
administrator password, and Terraform creates only application-secret metadata.

`domain_name` defaults to null. It is safe to leave it unset while preparing the
code or provisioning a host; no example hostname is configured for ACME. A real
application release requires it. Setting/changing it updates cloud-init and thus
replaces EC2 on the next approved apply; EBS and the public EIP are retained.

## Cloudflare DNS, HTTPS and PUBLIC_URL

After an approved apply, create an **A** record in the Cloudflare zone: name
`@` for the zone apex (or the configured subdomain), content equal to Terraform's `public_ip`, initially **DNS only** (gray
cloud). Remove conflicting A/AAAA records. This does not require a Cloudflare API
token on EC2 or in Terraform. Wait until the configured hostname resolves publicly
to that IP. Leave inbound TCP 80 and 443 reachable; Caddy uses ACME HTTP/TLS
challenges and renews its certificate automatically. Do not substitute an example
hostname in a real apply.

Set `domain_name` to that exact real hostname, without `https://` or a trailing
slash, before the approved apply that will enable HTTPS. `acme_email` is optional.
The host's `/etc/caddy/Caddyfile` is generated from these values. Initial DNS
propagation may delay issuance; check `sudo journalctl -u caddy`. Verify public
HTTPS before publishing the app. DNS can remain in DNS-only mode. If Cloudflare's
proxy is enabled later, set SSL/TLS mode to **Full (strict)** so Cloudflare verifies
Caddy's origin certificate, and rerun the public release checks.
See [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/)
and [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/).

Terraform derives the `public_url` output as `https://<domain_name>` (empty while
unset). The host puts this same origin in backend `PUBLIC_URL`, `CORS_ORIGINS` and
smoke `SMOKE_BASE_URL`. The publisher bakes it into frontend `VITE_SITE_URL`.
This keeps login cookies, OAuth redirects, verification links and frontend metadata
on one origin. A later domain change requires a frontend rebuild and a release
to regenerate backend settings; editing only the DNS record is insufficient.

## 2. Supply application configuration

Generate this **once for a new installation**, then upload to the secret created
by Terraform. Use a different email for the dedicated release-check account.
The operator password is entered without echoing it or putting it in arguments.

The application secret also accepts optional string values `JOB_TIME_LIMIT`
(default `"120"`, minimum 2), `JOB_MAX_ATTEMPTS` (default `"3"`, minimum 1) and
`JOB_REDELIVERY_SECONDS` (default `"60"`, minimum 1). Bootstrap writes them to one
shared `.env.jobs` file used by the API and all background services. Change these
values in the existing secret while preserving its credentials and keys; each
release regenerates the file. Backup restores use the recorded secret version.

```bash
python3 infra/aws/scripts/create-config.py \
  --operator-email admin@example.com --smoke-email checks@example.com \
  --output infra/aws/application-secret.json
export AWS_REGION
AWS_REGION=$(terraform -chdir=infra/aws output -raw aws_region)
aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=infra/aws output -raw config_secret_arn)" \
  --secret-string file://infra/aws/application-secret.json \
  --query ARN --output text
rm infra/aws/application-secret.json
```

The instance role can read only this application secret and its RDS administrator
secret; it cannot change either. Both use AWS-managed service encryption keys.
Do not add secret-version data sources to Terraform. Never regenerate the
Fernet encryption key or session/database passwords on normal releases. Keep the
application secret for recovery; changing the Fernet key would make stored user
integration secrets unreadable. Automatic app credential rotation is not configured.

## 3. Release from the workstation

Push the committed ClassPort source to GitHub and wait for its CI checks. Then use
[Production delivery](../../docs/PRODUCTION.md) with a short-lived local AWS profile:

```bash
uv run --no-sync python -m scripts.release_local --profile classport
```

This reads Terraform outputs, verifies the caller's account, publishes/reuses the
immutable ECR image pair, tests those digests locally, and sends and waits for one
SSM release command. Results remain in the ignored local `releases/` directory.
The EC2 host pulls images and reads configuration with its own instance role.

One maintenance container prepares DB roles, applies migrations once, configures
runtime access and initializes accounts. The root-only temporary secret directory
is removed on exit. Only runtime `.env` and release-check `.env.smoke` files are
retained with mode `0600`; database maintenance credentials remain in Secrets
Manager and the temporary maintenance process. Operator hashes are preserved by
the existing seed script; the separate release-check account/token is refreshed.

The runtime login has DML grants, no DDL/role administration or migration-table
access, and no membership in the migration role. A role-specific permissive policy
preserves current application-level user isolation on existing RLS tables. This
does not enforce database-level tenant RLS; see [the RLS design](../../docs/RLS.md).
RDS certificate/hostname verification remains enabled.

The backend/frontend run on the Docker bridge with IMDSv2 hop limit 1, so they
cannot retrieve host Secrets Manager permissions. The short-lived maintenance
container uses host networking; the host and its administrators are trusted.

### Activating an existing host

Inspect the Terraform plan before applying any infrastructure update. The module
uses `user_data_replace_on_change = true`; changing host initialization, including
the initial transition to the stable launcher, can plan an EC2 replacement. Never
apply that replacement as an unnoticed part of application delivery. Choose and
approve the actual infrastructure/host update before enabling automatic releases.
An existing host must have the reviewed `scripts/release.sh` installed at
`/usr/local/sbin/classport-release` with root ownership and mode `0700`.
Future application releases use their image's files and do not require a host
initialization update. Keep the application secret, EBS data and required release
records available throughout any separately approved host maintenance.

## Acceptance and operations

The existing release script must finish successfully: it checks readiness, the
public **HTTPS** login page and assets, real password login, authenticated course
API, MCP initialization and a course-list tool call. TLS verification remains
enabled. Then open the `public_url` output, log in with the operator email/password,
create a course, upload/read a file and refresh to confirm persistence. These user
writes should be verified against the deployed AWS environment before sign-off.
An apply alone does not demonstrate end-to-end acceptance.

Container stdout/stderr goes to the `log_group` output with 14-day retention.
Host setup diagnostics: `sudo journalctl -u cloud-final` and
`sudo journalctl -u caddy`. No access logging of OAuth query strings is enabled.
The local release directory retains the manifest and SSM/database/application results. The
host retains attempt reports under `/opt/classport/attempts` and full command logs
under `/opt/classport/commands`. See the production guide for the artifact names,
failure semantics and timeout handling.

Emergency rollback uses the saved previous image pair; follow the authenticated
SSM procedure in [Production delivery](../../docs/PRODUCTION.md#emergency-rollback).

RDS keeps one day of automated backups. The private, encrypted, versioned S3
bucket stores consistent database exports, course archives and their manifest.
The host has upload-only backup access; the separate `recovery_policy_arn` can be
attached to an approved isolated recovery identity. A8's versioned `recovery.py`
implements backup, restore and lightweight disk/readiness/backup-age metrics.
The daily backup timer runs at 03:00 America/New_York after the A8 host update;
monitoring runs every minute. Backups pause services throughout export/upload.
Review and approve host changes and this maintenance window before activation.
Subscribe the approved notification destination to `operations_topic_arn` and
verify delivery.
Local tests do not establish that scheduled backups or AWS restores work online.

EBS is separate from EC2 and has `prevent_destroy`; Docker requires its mount
before starting on reboot. On initial boot only an unsigned empty disk matching
the exact EBS volume ID is formatted; an existing ext4 volume is mounted intact.
Cloud-init changes replace EC2 while preserving that volume and EIP. The new host
needs a local release to restore the running containers and host config.
Instance resize and disk growth/filesystem expansion are operator maintenance.

RDS deletion protection defaults on, final snapshots are mandatory, and S3/ECR
contents are never force-deleted. `terraform destroy` is intentionally blocked by
EBS protection; planned teardown requires backup verification and an explicit edit
to remove that protection. The final RDS snapshot name is `<name>-final`; preserve
or rename an existing snapshot before reusing that name for another teardown.
Do not use destroy as an application rollback.

Reference installation behavior: [Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Caddy packages](https://caddyserver.com/docs/install#debian-ubuntu-raspbian),
[RDS administrator privileges](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html).

## S3 material runtime

The material bucket is separate from backups, private, encrypted and versioned.
The updated host environment carries its bucket name and restricted role ARN.
The backend release installs the host STS rotation service before starting the
API/worker, which mount only its isolated credential directory. Keep IMDS hop
limit 1. See [S3 storage](../../docs/S3_STORAGE.md) for the protocol, existing-data
cutover and real AWS acceptance requirements.
