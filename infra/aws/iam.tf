resource "aws_iam_role" "app" {
  name = "${var.name}-ec2"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}
resource "aws_iam_instance_profile" "app" {
  name = var.name
  role = aws_iam_role.app.name
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "app" {
  name = "${var.name}-runtime"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRLogin", Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Sid      = "PullImages", Effect = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = [for repository in aws_ecr_repository.images : repository.arn]
      },
      {
        Sid      = "ReadConfiguration", Effect = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.config.arn, aws_db_instance.postgres.master_user_secret[0].secret_arn]
      },
      {
        Sid      = "ContainerLogs", Effect = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.containers.arn}:*"
      },
      {
        Sid      = "WriteBackups", Effect = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.backups.arn}/*"
      },
      {
        Sid       = "OperationalMetrics", Effect = "Allow"
        Action    = ["cloudwatch:PutMetricData"], Resource = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "ClassPort/Operations" } }
      }
    ]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_oidc_subject != null && var.github_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github" {
  count = var.github_oidc_subject != null ? 1 : 0
  name  = "${var.name}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = var.github_oidc_subject
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github" {
  count = var.github_oidc_subject != null ? 1 : 0
  name  = "${var.name}-publish-and-deploy"
  role  = aws_iam_role.github[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRLogin", Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Sid = "ReleaseImages", Effect = "Allow"
        Action = [
          "ecr:DescribeImages", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"
        ]
        Resource = [for repository in aws_ecr_repository.images : repository.arn]
      },
      {
        Sid    = "DeployInstance", Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          "arn:${local.partition}:ssm:${var.aws_region}::document/AWS-RunShellScript",
          "arn:${local.partition}:ec2:${var.aws_region}:${local.account}:instance/${aws_instance.app.id}"
        ]
      },
      {
        Sid    = "ReadCommandResult", Effect = "Allow"
        Action = ["ssm:GetCommandInvocation"], Resource = "*"
      }
    ]
  })
}
