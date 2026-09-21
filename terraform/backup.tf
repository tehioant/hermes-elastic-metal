resource "scaleway_object_bucket" "backup" {
  provider = scaleway.backup

  name                = var.backup_bucket_name
  region              = var.backup_region
  tags                = { for t in var.tags : split(":", t)[0] => split(":", t)[1] }
  object_lock_enabled = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    id      = "expire-noncurrent-versions"
    enabled = true

    noncurrent_version_expiration {
      noncurrent_days = var.backup_lock_days + 10
    }
  }
}

resource "scaleway_object_bucket_lock_configuration" "backup" {
  provider = scaleway.backup

  bucket     = scaleway_object_bucket.backup.name
  region     = var.backup_region
  project_id = scaleway_object_bucket.backup.project_id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.backup_lock_days
    }
  }
}

locals {
  # IAM application/policy tags reject ":"; the bucket resource uses the same
  # var.tags list but converts it to a map instead.
  iam_tags = [for t in var.tags : replace(t, ":", "=")]
}

resource "scaleway_iam_application" "backup" {
  name        = "${var.hostname}-restic"
  description = "restic on ${var.hostname}: object storage access limited to the project"
  tags        = local.iam_tags
}

resource "scaleway_iam_policy" "backup" {
  name           = "${var.hostname}-restic"
  description    = "Read/write/delete objects for restic. Ransomware guard is versioning + Object Lock, not IAM."
  application_id = scaleway_iam_application.backup.id
  tags           = local.iam_tags

  rule {
    project_ids = [scaleway_object_bucket.backup.project_id]
    permission_set_names = [
      "ObjectStorageBucketsRead",
      "ObjectStorageObjectsRead",
      "ObjectStorageObjectsWrite",
      "ObjectStorageObjectsDelete",
    ]
  }
}

resource "scaleway_iam_api_key" "backup" {
  application_id     = scaleway_iam_application.backup.id
  description        = "restic on ${var.hostname}"
  default_project_id = scaleway_object_bucket.backup.project_id
  # Org policy requires an expiration date. Rotate quarterly per docs/runbooks/maintenance.md;
  # ignore_changes keeps `terraform plan` clean between rotations (timestamp() changes every run).
  expires_at = timeadd(timestamp(), "8760h") # ~1 year

  lifecycle {
    ignore_changes = [expires_at]
  }
}

output "restic_repository" {
  description = "restic repository URL for the backup bucket."
  value       = "s3:https://s3.${var.backup_region}.scw.cloud/${scaleway_object_bucket.backup.name}"
}

output "backup_access_key" {
  description = "AWS_ACCESS_KEY_ID for restic."
  value       = scaleway_iam_api_key.backup.access_key
  sensitive   = true
}

output "backup_secret_key" {
  description = "AWS_SECRET_ACCESS_KEY for restic."
  value       = scaleway_iam_api_key.backup.secret_key
  sensitive   = true
}
