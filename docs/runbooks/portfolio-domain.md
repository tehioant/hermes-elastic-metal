# Portfolio domain (Cloudflare Pages + Scaleway DNS)

Publish `https://portfolio.antelab.eu` without moving nameservers or hosting
anything on Elastic Metal. This repo manages only the Scaleway CNAME; the
Cloudflare Pages project and custom-domain association are configured in the
Cloudflare dashboard. Existing dashboard, Netdata, mail, and root records stay
unchanged.

## 1. Deploy Pages and associate the custom domain

1. Cloudflare **Workers & Pages → Create application → Continue to Pages**
   (the dashboard may label this the legacy Pages workflow) → connect Git:
   select the private `tehioant/orbit-portfolio` repo, production branch `main`.
2. Build command `npm run build`, output directory `dist`, root directory blank,
   environment variable `NODE_VERSION=24`.
3. Deploy and verify the production `*.pages.dev` URL. Copy the actual assigned
   hostname, not a guessed project name or a commit/branch preview address.
4. Pages **Custom domains → Set up a domain**: add `portfolio.antelab.eu` and
   follow the external-DNS instructions. Do not migrate the `antelab.eu` zone.

The assigned production hostname is `orbit-portfolio.pages.dev`. The site
has been checked in a browser. A `*.workers.dev` address belongs to a different
product and must not be substituted for this Pages target.

## 2. Configure Terraform through a reviewed PR

The production value is committed as the default of `portfolio_pages_hostname`
in `terraform/variables.tf`:

```hcl
portfolio_pages_hostname = "orbit-portfolio.pages.dev"
```

CI and CD intentionally do not set `TF_VAR_portfolio_pages_hostname`: both
use this reviewed Terraform default. The previously used repository variable
`PORTFOLIO_PAGES_HOSTNAME` is no longer consumed, so a missing or empty Actions
variable cannot silently disable the record. No extra GitHub variable is needed.

Make target changes in a PR. PR CI runs a real read-only plan against the
existing remote state; review it before merging. Merge to `main` triggers the
existing CD plan/apply workflow (including any configured production approval).
Do not dispatch CD or apply locally as part of preparing the PR.

For local plans, check that ignored tfvars or shell `TF_VAR_` values do not
accidentally override the production default. Test fixture hostnames are not
deployment values; the reusable DNS module still accepts empty for callers
that have not yet obtained their Pages hostname.

Terraform creates:

- Address: `module.portfolio_dns.scaleway_domain_record.portfolio[0]`
- Zone: existing `root_domain` (`antelab.eu` by default)
- Label: `portfolio`
- Type: `CNAME`
- Target: `orbit-portfolio.pages.dev.` (absolute DNS hostname)
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

The normal production path is to merge the reviewed PR and let CD deploy it;
local apply commands above are for explicitly authorized manual operation only.
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
python3 -m unittest discover -s tests -p 'test_*.py' -v
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
