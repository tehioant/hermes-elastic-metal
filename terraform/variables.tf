variable "zone" {
  description = "Elastic Metal zone hosting the server."
  type        = string
  default     = "fr-par-2"

  validation {
    condition     = contains(["fr-par-1", "fr-par-2", "nl-ams-1", "nl-ams-2", "pl-waw-2", "pl-waw-3"], var.zone)
    error_message = "EM-A116X-SSD is only available in fr-par-1/2, nl-ams-1/2, pl-waw-2/3."
  }
}

variable "hostname" {
  description = "Server name and hostname."
  type        = string
  default     = "emeta-01"
}

variable "offer_name" {
  description = "Elastic Metal offer name."
  type        = string
  default     = "EM-A116X-SSD"
}

variable "os_version" {
  description = "Ubuntu version string as listed by `scw baremetal os list`."
  type        = string
  default     = "26.04 LTS (Resolute Raccoon)"
}

variable "ssh_public_key" {
  description = "Public key installed for root at first boot."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+) ", var.ssh_public_key))
    error_message = "ssh_public_key must be an OpenSSH public key (ssh-ed25519, ssh-rsa or ecdsa)."
  }
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach SSH. Consumed by bootstrap.sh via the admin_cidrs output."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "admin_cidrs must be a non-empty list of valid CIDRs, e.g. [\"203.0.113.4/32\"]."
  }
}

variable "backup_region" {
  description = "Object Storage region for restic backups. Must differ from the server region."
  type        = string
  default     = "nl-ams"

  validation {
    condition     = contains(["fr-par", "nl-ams", "pl-waw"], var.backup_region)
    error_message = "backup_region must be one of fr-par, nl-ams, pl-waw."
  }
}

variable "backup_bucket_name" {
  description = "Globally unique Object Storage bucket name for restic."
  type        = string
  default     = "hermes-backup-emeta-01"
}

variable "backup_lock_days" {
  description = "Object Lock default retention in days. Must cover the restic retention window."
  type        = number
  default     = 35
}

variable "dashboard_domain_name" {
  description = "Registered domain for the Hermes dashboard. Applying the registration purchases it."
  type        = string
  default     = "antelab.eu"
}

variable "dashboard_subdomain" {
  description = "DNS label for the dashboard, beneath dashboard_domain_name."
  type        = string
  default     = "apollo"
}

variable "domain_owner" {
  description = "Individual domain registrant. Supply in ignored tfvars or TF_VAR_domain_owner; details are stored in Terraform state."
  type = object({
    firstname      = string
    lastname       = string
    email          = string
    phone_number   = string
    address_line_1 = string
    city           = string
    zip            = string
    country        = string
  })
  sensitive = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = list(string)
  default     = ["env:prod", "app:hermes", "role:docker-host", "managed-by:terraform"]
}
