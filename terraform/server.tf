locals {
  server_region = join("-", slice(split("-", var.zone), 0, 2))
  server_ipv4   = [for ip in scaleway_baremetal_server.this.ips : ip.address if ip.version == "IPv4"][0]
}

check "backup_region_differs_from_server" {
  assert {
    condition     = var.backup_region != local.server_region
    error_message = "backup_region (${var.backup_region}) must differ from the server region (${local.server_region}) for off-site backups."
  }
}

data "scaleway_baremetal_offer" "this" {
  zone = var.zone
  name = var.offer_name
}

data "scaleway_baremetal_os" "ubuntu" {
  zone    = var.zone
  name    = "Ubuntu"
  version = var.os_version
}

resource "scaleway_iam_ssh_key" "ops" {
  name       = "${var.hostname}-ops"
  public_key = var.ssh_public_key
}

resource "scaleway_baremetal_server" "this" {
  zone        = var.zone
  name        = var.hostname
  hostname    = var.hostname
  description = "Hermes Docker host - managed by Terraform"
  offer       = data.scaleway_baremetal_offer.this.offer_id
  os          = data.scaleway_baremetal_os.ubuntu.os_id
  ssh_key_ids = [scaleway_iam_ssh_key.ops.id]
  tags        = var.tags

  reinstall_on_config_changes = false

  lifecycle {
    prevent_destroy = true
  }
}
