resource "scaleway_domain_registration" "dashboard" {
  domain_names      = [var.dashboard_domain_name]
  duration_in_years = 1
  auto_renew        = true

  owner_contact {
    legal_form     = "individual"
    firstname      = var.domain_owner.firstname
    lastname       = var.domain_owner.lastname
    email          = var.domain_owner.email
    phone_number   = var.domain_owner.phone_number
    address_line_1 = var.domain_owner.address_line_1
    city           = var.domain_owner.city
    zip            = var.domain_owner.zip
    country        = var.domain_owner.country
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "scaleway_domain_record" "dashboard" {
  dns_zone = var.dashboard_domain_name
  name     = var.dashboard_subdomain
  type     = "A"
  data     = [for ip in scaleway_baremetal_server.this.ips : ip.address if ip.version == "IPv4"][0]
  ttl      = 3600

  depends_on = [scaleway_domain_registration.dashboard]
}
