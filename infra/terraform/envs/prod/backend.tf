# Remote state: S3 with native locking (Terraform >= 1.11). Create the bucket once (versioned, encrypted,
# public access blocked) and pass it at init time:
#   terraform init -backend-config="bucket=<state-bucket>" -backend-config="region=<region>"
terraform {
  backend "s3" {
    key          = "opspilot/prod/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
