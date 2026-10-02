# Portfolio domain (Cloudflare Pages + Scaleway DNS)

Publish `https://portfolio.antelab.eu` without moving nameservers or hosting
anything on Elastic Metal. This repo manages only the Scaleway CNAME; the
Cloudflare Pages project and custom-domain association are configured in the
Cloudflare dashboard. Existing dashboard, Netdata, mail, and root records stay
unchanged.

## 1. Deploy Pages and associate the custom domain

1. Cloudflare **Workers & Pages → Pages → Connect to Git**: select the private
   `tehioant/orbit-portfolio` repository, production branch `main`.
2. Build command `npm run build`, output directory `dist`, root directory blank,
   environment variable `NODE_VERSION=24`.
3. Deploy and verify the production `*.pages.dev` URL. Copy the actual assigned
   hostname, not a guessed project name or a commit/branch preview address.
4. Pages **Custom domains → Set up a domain**: add `portfolio.antelab.eu` and
   follow the external-DNS instructions. Do not migrate the `antelab.eu` zone.

The exact Pages hostname is not known yet, so `portfolio_pages_hostname`
defaults to empty and Terraform plans **no portfolio record**. Test fixture
hostnames are not deployment values.

## 2. Configure Terraform (do not apply yet)

Set the exact production hostname in your gitignored `terraform/terraform.tfvars`:

```hcl
# Replace this example with the hostname actually assigned by Cloudflare.
portfolio_pages_hostname = "assigned-project.pages.dev"
```

For GitHub Actions, set the **repository** Actions variable
`PORTFOLIO_PAGES_HOSTNAME` to the same hostname. It is public DNS information,
not a secret. Both PR CI and the CD plan/apply jobs read this variable. Use a
repository variable, not an environment-only variable: CD's plan job does not
use the production environment.

**Changing this variable affects future CI/CD runs.** Only set it when the
actual target is known and deployment is approved. Do not merge or dispatch CD
without reviewing the full infrastructure plan and its production approval.

Terraform creates:

- Address: `module.portfolio_dns.scaleway_domain_record.portfolio[0]`
- Zone: existing `root_domain` (`antelab.eu` by default)
- Label: `portfolio`
- Type: `CNAME`
- Target: supplied production hostname with one DNS root dot
- TTL: 300 seconds

URLs, paths, preview prefixes, whitespace, invalid labels, and other domains
are rejected. No Cloudflare API token or additional provider is needed.

If a record already exists, inspect it first. Do not let Terraform conflict
with an unmanaged CNAME/A/AAAA record at `portfolio`. To adopt an existing
CNAME, use `terraform import` with Scaleway's zone/record ID format only after
confirming the precise record and approving the state change; see the provider
record documentation linked below.

## 3. Review and apply only with approval

Use the existing credentials, variables, and remote-state workflow described
in `terraform-state.md`.

```bash
terraform -chdir=terraform init
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform show tfplan
```

For a clean, already-provisioned environment the infrastructure change should
be the portfolio CNAME only (plus its output). **Stop if the plan changes the
server, backup resources, or unrelated DNS.** This module does not require a
server reinstall, firewall changes, Caddy, or `ship.sh`.

Only after explicit approval:

```bash
terraform -chdir=terraform apply tfplan
```

Alternatively use the reviewed repo CD workflow and its production approval.
Do not manually create a duplicate DNS record in the Scaleway console.

## 4. Verify

```bash
terraform -chdir=terraform output -raw portfolio_fqdn
dig +short CNAME portfolio.antelab.eu
curl --head --fail https://portfolio.antelab.eu
```

The CNAME must match the Pages-assigned target. Wait for Cloudflare's custom
domain to report **Active** and issue its HTTPS certificate, then load the site
in a browser. A DNS record alone does not register the custom domain or issue
its certificate.

## Offline checks and safeguards

```bash
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
terraform -chdir=terraform test -filter=tests/portfolio_dns.tftest.hcl
```

The tests plan the actual isolated production DNS module with a mocked
Scaleway provider. They do not contact Scaleway, access remote state, or apply
live changes. The root configuration is also validated separately. Installing
the provider still requires network access on first initialization.

The record has `prevent_destroy = true`. Once created, accidentally clearing
the hostname makes Terraform reject deletion rather than silently removing
the site. Target changes can be reviewed normally; intentional removal needs
a separate reviewed change to the guard and an explicitly approved plan.

## References

- https://developers.cloudflare.com/pages/configuration/custom-domains/
- https://developers.cloudflare.com/pages/get-started/git-integration/
- https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/domain_record
