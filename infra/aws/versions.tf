terraform {
  required_version = ">= 1.13.5, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { Project = "ClassPort", Environment = var.name, ManagedBy = "Terraform" }
  }
}
