# Task-role policies: each service gets only the AWS API access it uses.

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ArtifactsReadWrite"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${module.storage.bucket_arn}/workspaces/*"]
  }
  statement {
    sid       = "BucketReadiness"
    actions   = ["s3:ListBucket"]
    resources = [module.storage.bucket_arn]
  }
  statement {
    sid       = "ArtifactKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [module.kms.key_arn]
  }
  statement {
    sid       = "EnqueueIngestion"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [module.storage.queue_arn]
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ReadArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${module.storage.bucket_arn}/workspaces/*"]
  }
  statement {
    sid       = "ConsumeIngestion"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:SendMessage"]
    resources = [module.storage.queue_arn]
  }
  statement {
    sid       = "ArtifactAndQueueKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [module.kms.key_arn]
  }
  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [module.storage.dlq_arn]
  }
}
