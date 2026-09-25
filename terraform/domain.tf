data "scaleway_domain_zone" "root" {
  domain    = var.root_domain
  subdomain = ""
}

resource "scaleway_domain_record" "dashboard" {
  dns_zone = data.scaleway_domain_zone.root.domain
  name     = var.dashboard_subdomain
  type     = "A"
  data     = local.server_ipv4
  ttl      = 3600
}
