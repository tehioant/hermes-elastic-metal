provider "scaleway" {
  zone   = var.zone
  region = local.server_region
}

provider "scaleway" {
  alias  = "backup"
  region = var.backup_region
}
