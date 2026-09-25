# Dashboard domain and DNS

`antelab.eu` is registered manually in the Scaleway console (1 year,
auto-renewal). Terraform only reads its DNS zone through the
`scaleway_domain_zone` data source and manages the A record
`apollo.antelab.eu` pointing to the Elastic Metal server's public IPv4.

Registration is not managed by Terraform: the Scaleway API rejected
individual `.eu` registrants with `invalid SIRET or SIREN`.

A DNS record alone does not start the dashboard, open a firewall port, or
provide HTTPS/authentication.

## Register or change the domain

1. Scaleway console → **Domains and DNS** → **Register a domain**.
2. Legal form **Individual**, EU/EEA address, same project as Terraform.
3. Validate the EURid verification email and wait for status **Active**.
4. If the name differs, update `root_domain` in a reviewed PR.

Terraform fails at plan time if the zone does not exist yet.

## Verify

```bash
terraform -chdir=terraform output -raw dashboard_fqdn
terraform -chdir=terraform output -raw ipv4
dig +short A apollo.antelab.eu
```

The resolved A address must match the Terraform IPv4 output. DNS propagation
can take time.

Before pointing browsers at the dashboard, deploy HTTPS with a reverse proxy,
configure the Hermes OAuth/OIDC auth provider and exact HTTPS
`dashboard.public_url`, and verify `/api/status` reports `auth_required: true`.
Keep Hermes' backend on loopback; do not expose port 9119 directly.
