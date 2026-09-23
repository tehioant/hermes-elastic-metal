# Dashboard domain and DNS

Terraform registers `tehio.eu` with Scaleway for one year, enables annual
auto-renewal, and creates an A record for `apollo.tehio.eu` pointing to the
Elastic Metal server's public IPv4. The registered domain and subdomain can
be changed in `terraform.tfvars` before purchase. A DNS record alone does not
start the dashboard, open a firewall port, or provide HTTPS/authentication.

## Before applying

1. Search for `tehio.eu` in Scaleway **Domains and DNS**. If unavailable,
   choose an available name and set `dashboard_domain_name` in your ignored
   `terraform/terraform.tfvars`. Do not choose a name based on DNS lookup alone.
2. Fill `domain_owner` in the ignored `terraform/terraform.tfvars` with the
   real eligible registrant's details. Scaleway may require contact/email
   verification after registration. Do not commit personal information or
   put it in command-line arguments.
3. Confirm that the selected domain's registration and renewal prices, tax,
   account balance/payment method, and annual auto-renewal are acceptable.
   `terraform apply` makes a **paid domain purchase**; obtaining approval for
   the plan is not approval to apply it automatically.
4. **Do not apply until state storage is secured.** The registration resource
   writes registrant details and an EPP/transfer code into local Terraform state.
   Keep the checkout and its plan/state files accessible only to the operator,
   use encrypted storage and encrypted, access-restricted backups, and never
   commit or share state/plan files. The repository's `.gitignore` prevents
   accidental Git tracking but does not encrypt the files.

From the repository root, with your existing Scaleway credentials and local
Terraform state available (on protected storage):

```bash
umask 077
terraform -chdir=terraform init
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform show tfplan
# Apply only after explicitly checking the domain, price and all other changes.
terraform -chdir=terraform apply tfplan
```

## Verify

```bash
terraform -chdir=terraform output -raw dashboard_fqdn
terraform -chdir=terraform output -raw ipv4
dig +short A apollo.tehio.eu
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
