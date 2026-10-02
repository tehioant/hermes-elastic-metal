terraform {
  required_providers {
    scaleway = {
      source = "scaleway/scaleway"
    }
  }
}

variable "root_domain" {
  description = "Existing Scaleway DNS zone, supplied by the parent module."
  type        = string
  nullable    = false
}

variable "portfolio_pages_hostname" {
  description = "Assigned production Cloudflare Pages hostname (not a URL or preview hostname). Empty keeps DNS disabled."
  type        = string
  default     = ""
  nullable    = false

  validation {
    condition = (
      var.portfolio_pages_hostname == "" ||
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.pages\\.dev\\.?$", var.portfolio_pages_hostname))
    )
    error_message = "Use the exact lowercase production <project>.pages.dev hostname, optionally ending in a DNS root dot, or an empty string. Do not include https://, a path, whitespace, or a preview prefix."
  }
}

locals {
  dns_enabled           = var.portfolio_pages_hostname != ""
  absolute_pages_target = "${trimsuffix(var.portfolio_pages_hostname, ".")}."
}

resource "scaleway_domain_record" "portfolio" {
  count = local.dns_enabled ? 1 : 0

  dns_zone = var.root_domain
  name     = "portfolio"
  type     = "CNAME"
  data     = local.absolute_pages_target
  ttl      = 300

  lifecycle {
    prevent_destroy = true
  }
}

output "portfolio_fqdn" {
  description = "Configured portfolio hostname, or empty while DNS is disabled. Cloudflare domain activation and HTTPS are configured separately."
  value       = local.dns_enabled ? "portfolio.${var.root_domain}" : ""
}
