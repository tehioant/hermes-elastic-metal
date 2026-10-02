# Keep Scaleway authoritative; Cloudflare Pages manages hosting and HTTPS.
module "portfolio_dns" {
  source = "./modules/portfolio-dns"

  root_domain              = var.root_domain
  portfolio_pages_hostname = var.portfolio_pages_hostname

  depends_on = [data.scaleway_domain_zone.root]
}
