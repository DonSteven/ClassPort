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
      }
    ]
  })
}
