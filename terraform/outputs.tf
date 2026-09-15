output "ipv4" {
  description = "Public IPv4 of the server."
  value       = [for ip in scaleway_baremetal_server.this.ips : ip.address if ip.version == "IPv4"][0]
}

output "domain" {
  description = "Scaleway-provided DNS name of the server."
  value       = scaleway_baremetal_server.this.domain
}

output "admin_cidrs" {
  description = "Comma-separated admin CIDRs, passed to bootstrap.sh as ADMIN_CIDRS."
  value       = join(",", var.admin_cidrs)
}
