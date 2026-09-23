terraform {
  backend "s3" {
    key                         = "hermes-elastic-metal/terraform.tfstate"
    region                      = "nl-ams"
    endpoints                   = { s3 = "https://s3.nl-ams.scw.cloud" }
    use_path_style              = true
    use_lockfile                = true
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
  }
}
