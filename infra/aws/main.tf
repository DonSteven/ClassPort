data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  partition  = data.aws_partition.current.partition
  account    = data.aws_caller_identity.current.account_id
  public_url = var.domain_name == null ? "" : "https://${var.domain_name}"
  caddyfile = (var.domain_name == null ? <<-CADDY
    {
        auto_https off
    }
    :80 {
        respond "Configure the ClassPort HTTPS domain before publishing." 503
    }
  CADDY
    : <<-CADDY
    ${var.acme_email == null ? "" : "{\n    email ${var.acme_email}\n}"}
    ${var.domain_name} {
        reverse_proxy 127.0.0.1:8080 {
            flush_interval -1
        }
    }
  CADDY
  )
  host_env = join("\n", [
    "AWS_REGION=${var.aws_region}",
    "AWS_DEFAULT_REGION=${var.aws_region}",
    "AWS_PAGER=",
    "CONFIG_SECRET_ARN=${aws_secretsmanager_secret.config.arn}",
    "DB_SECRET_ARN=${aws_db_instance.postgres.master_user_secret[0].secret_arn}",
    "DB_HOST=${aws_db_instance.postgres.address}",
    "DB_NAME=classport",
    "PUBLIC_URL=${local.public_url}",
    "BACKEND_REPOSITORY=${aws_ecr_repository.images["backend"].repository_url}",
    "FRONTEND_REPOSITORY=${aws_ecr_repository.images["frontend"].repository_url}",
    "DATA_VOLUME_ID=${aws_ebs_volume.data.id}",
    "BACKUP_BUCKET=${aws_s3_bucket.backups.id}",
    "ENVIRONMENT=${var.name}",
    "", # trailing newline
  ])
  cloud_init = templatefile("${path.module}/cloud-init.yaml", {
    host_env = base64encode(local.host_env)
    setup    = base64encode(file("${path.module}/scripts/setup-host.sh"))
    release  = base64encode(file("${path.module}/scripts/release.sh"))
    docker_config = base64encode(jsonencode({
      "log-driver" = "awslogs"
      "log-opts" = {
        "awslogs-region"  = var.aws_region
        "awslogs-group"   = aws_cloudwatch_log_group.containers.name
        "mode"            = "non-blocking"
        "max-buffer-size" = "4m"
      }
    }))
    caddyfile = base64encode(local.caddyfile)
  })
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}
resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.42.0.0/24"
  availability_zone = local.azs[0]
  tags              = { Name = "${var.name}-app" }
}
resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.42.${count.index + 10}.0/24"
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.name}-db-${count.index + 1}" }
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
resource "aws_route_table" "database" {
  vpc_id = aws_vpc.main.id # Local VPC routes only; no NAT/Internet gateway.
}
resource "aws_route_table_association" "database" {
  count          = 2
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.database.id
}

resource "aws_security_group" "app" {
  name   = "${var.name}-app"
  vpc_id = aws_vpc.main.id
}
resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = toset(["80", "443"])
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
}
resource "aws_vpc_security_group_egress_rule" "app" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
resource "aws_security_group" "database" {
  name   = "${var.name}-database"
  vpc_id = aws_vpc.main.id
}
resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_subnet_group" "postgres" {
  name       = var.name
  subnet_ids = aws_subnet.database[*].id
}
resource "aws_db_parameter_group" "postgres" {
  name   = var.name
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "aws_db_instance" "postgres" {
  identifier                  = var.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "classport"
  username                    = "classport_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.postgres.name
  parameter_group_name        = aws_db_parameter_group.postgres.name
  vpc_security_group_ids      = [aws_security_group.database.id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  copy_tags_to_snapshot       = true
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.name}-final"
  auto_minor_version_upgrade  = true
}

# Terraform owns secret metadata only. Values are supplied out of band and never
# enter plans, state or EC2 user data. RDS manages its own administrator secret.
resource "aws_secretsmanager_secret" "config" {
  name                    = "${var.name}/application"
  recovery_window_in_days = 7
}
resource "aws_ecr_repository" "images" {
  for_each             = toset(["backend", "frontend"])
  name                 = "${var.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}
resource "aws_s3_bucket" "backups" {
  bucket        = "${var.name}-backups-${local.account}-${var.aws_region}"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_policy" "backups" {
  bucket = aws_s3_bucket.backups.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_cloudwatch_log_group" "containers" {
  name              = "/${var.name}/containers"
  retention_in_days = 14
}

resource "aws_ebs_volume" "data" {
  availability_zone = local.azs[0]
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.name}-data" }
  lifecycle {
    prevent_destroy = true
  }
}
resource "aws_instance" "app" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  user_data_base64            = base64gzip(local.cloud_init)
  user_data_replace_on_change = true
  root_block_device {
    volume_size = 24
    volume_type = "gp3"
    encrypted   = true
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1 # App bridge containers cannot use the host IAM role.
    instance_metadata_tags      = "disabled"
  }
  tags       = { Name = var.name }
  depends_on = [aws_route_table_association.public, aws_iam_role_policy.app, aws_iam_role_policy_attachment.ssm]
  lifecycle {
    precondition {
      condition     = length(regexall("amd64", data.aws_ami.ubuntu.name)) > 0
      error_message = "Use the Ubuntu x86-64 AMI for the existing release images."
    }
  }
}
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}
resource "aws_eip" "app" {
  domain     = "vpc"
  instance   = aws_instance.app.id
  depends_on = [aws_internet_gateway.main]
}
