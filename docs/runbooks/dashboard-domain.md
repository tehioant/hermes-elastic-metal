# Dashboard domain and DNS

Terraform registers `ante.eu` with Scaleway for one year, enables annual
auto-renewal, and creates an A record for `apollo.ante.eu` pointing to the
Elastic Metal server's public IPv4. The registered domain and subdomain can
be changed in Terraform code before purchase. The auto-CD runner does not read
your laptop’s `terraform.tfvars`. A DNS record alone does not
start the dashboard, open a firewall port, or provide HTTPS/authentication.

## Before applying

1. Search for `ante.eu` in Scaleway **Domains and DNS**. If unavailable,
   choose an available name and change `dashboard_domain_name` in a reviewed
   Terraform PR (and your local tfvars, if used). Do not choose a name based
   on DNS lookup alone.
2. Supply the real eligible registrant in the ignored local tfvars and, for
   auto-CD, the GitHub `production` environment secret `DOMAIN_OWNER_JSON`.
   Scaleway may require contact/email verification after registration. Do not
   commit personal information or put it in command-line arguments.
3. Confirm that the selected domain's registration and renewal prices, tax,
   account balance/payment method, and annual auto-renewal are acceptable.
   **A merge to `main` can purchase the domain automatically** once CD is
   enabled, without another approval step. Review cost and eligibility before
   merging this PR; the workflow has no price ceiling.
4. **Do not merge/apply until the existing laptop state is migrated** using
   [the locked-state guide](terraform-state.md) in PR #3. The registration
   resource writes registrant details and an EPP/transfer code into Terraform
   state. Use a private, versioned, encrypted backend and protected backups;
   never commit or share state/plan files. `.gitignore` does not encrypt them.

For an optional manual review on your trusted laptop, using the **same
migrated state and variables** as CD:

```bash
umask 077
terraform -chdir=terraform init -backend-config="bucket=${STATE_BUCKET:?set the private state bucket}"
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform show tfplan
# Manual apply is optional; once CD is enabled, merge-to-main applies automatically.
# terraform -chdir=terraform apply tfplan
```

## Verify

```bash
terraform -chdir=terraform output -raw dashboard_fqdn
terraform -chdir=terraform output -raw ipv4
dig +short A apollo.ante.eu
```

Check that the resolved A address matches the Terraform IPv4 output and that
the domain registration is active in the Scaleway console. DNS propagation can
take time. If a different name was selected, query that hostname instead.

Before pointing browsers at the dashboard, deploy HTTPS with a reverse proxy,
configure the Hermes OAuth/OIDC auth provider and exact HTTPS
`dashboard.public_url`, and verify `/api/status` reports `auth_required: true`.
Keep Hermes' backend on loopback; do not expose port 9119 directly. These
network/application changes are **not** made by this domain/DNS change.

The registration resource uses `prevent_destroy` to stop an accidental
Terraform replacement/cancellation. To change names later, register the new
domain deliberately, migrate DNS, and handle the old registration separately.
