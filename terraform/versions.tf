terraform {
  required_version = ">= 1.10"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.84.0" # 2.84.0 ignores region for S3 when SCW_DEFAULT_REGION is set
    }
  }
}
