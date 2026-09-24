# Automatic infrastructure and host deployment

The `Deploy infrastructure and host` workflow runs on each merge to `main` (and on manual
`workflow_dispatch`). It automatically applies **the same saved plan it
validated**. This includes the paid `tehio.eu` registration and annual
renewal configuration when the domain PR is merged. There is no per-merge
approval step or price ceiling; check the domain's availability, purchase and
renewal prices, payment method, and .eu eligibility before enabling this.
Terraform does not deploy the dashboard or expose a web port. It also does
**not** perform initial provisioning: the authoritative migrated state, host,
Tailscale enrollment and SSH trust must already exist.

## Before merging the deployment PR

1. Merge the locked-backend PR, create a dedicated private versioned/encrypted
   state bucket, and migrate the **real laptop state** by following
   [terraform-state.md](terraform-state.md). Never put state or plans in GitHub,
   an issue, or chat. Verify the lineage and existing resources after migration.
2. Merge the backup-bucket lifecycle and deployment-guard PRs. The server,
   backup bucket, and registered domain have `prevent_destroy`; an additional
   plan guard rejects **any** delete/replacement operation. Intentional
   destructive changes need a separate, reviewed change to these safeguards.
3. In GitHub, create a `production` environment for this repository. Restrict its
   deployment branches to `main` (so another branch cannot request production
   secrets with a modified workflow). Add these **environment variables** (not
   repository-wide values):
   - `STATE_BUCKET`: name of the dedicated Terraform state bucket in `nl-ams`.
   - `EXPECTED_STATE_LINEAGE`: lineage printed by `terraform state pull` on
     your laptop after migration. This pins CD to the correct state.
   - `SCW_PROJECT_ID`, `SCW_ORGANIZATION_ID`: Scaleway IDs from the existing
     `.env`; use the same account/project that owns the server and backups.
   - `SSH_PUBLIC_KEY`: the exact key represented by the existing Terraform
     state; do not rotate the server's key accidentally.
   - `ADMIN_CIDRS_JSON`: JSON array of SSH allowlisted CIDRs (e.g.
     `["203.0.113.4/32"]`). Keep your actual admin IP, not a placeholder.
4. Add these **production environment secrets** via GitHub's secret UI:
   - `STATE_ACCESS_KEY_ID`, `STATE_SECRET_KEY`: the dedicated state bucket
     credentials. Scope them to state and `.tflock` objects.
   - `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`: least-privilege Scaleway credentials
     authorized for **all** Terraform-managed resources, including domain
     registration. Never reuse the restic backup IAM key.
   - `DOMAIN_OWNER_JSON`: JSON object with real eligible registrant details
     (`firstname`, `lastname`, `email`, `phone_number`, `address_line_1`,
     `city`, `zip`, `country`). Use the same details as your local tfvars.
     This is sensitive personal information: do not paste it into PRs or chat.
5. From the laptop, plan against the migrated state with matching values.
   Review any update to the server, SSH key, firewall CIDRs, backup IAM key,
   or domain. A Terraform apply can purchase a domain **without a separate
   confirmation**. Keep the repository's domain defaults (`tehio.eu` and
   `apollo`) aligned with what you approve; a change needs a reviewed PR.
6. Complete the [host shipping prerequisites](host-shipping.md): private
   Tailscale access, pinned SSH host key, dedicated CI key, unchanged restic
   password, and the non-resetting firewall and backup guards. These are
   checked **before** Terraform apply wherever possible. Confirm `production`
   has **no required reviewers** if every merge should deploy automatically.
   Restrict who can push/merge to `main` and require the PR validation check;
   otherwise a merge can spend money or change your infrastructure. Do not
   enable deployment until all settings and remote state are verified.

## What the workflow does

- Runs on a fresh GitHub-hosted runner with per-environment credentials. It
  requests a short-lived OIDC token solely to join the restricted tailnet,
  verifies the pinned host key and `ops` sudo access, then proceeds. Concurrent
  deploys are serialized.
- Fails before planning if any setting is missing or if the remote state
  lineage/resources do not match the existing server and backup bucket. It
  **never** initializes an empty state as if it were a new installation.
- Uses a Terraform S3 `.tflock` with a 10-minute wait for other applies,
  including local ones. It plans once, rejects any delete/replacement action,
  applies that exact saved plan, and deletes the local plan on exit. Plans and
  state are never uploaded as artifacts.
- After a successful apply, ships the same checkout to `ops` over Tailscale,
  reruns bootstrap with the stable restic password, and checks Docker and
  backup/healthcheck timers. It does **not** expose the dashboard or port 9119.
- Failed jobs stop. Fix the configuration or drift, then use **Run workflow**
  on `main`; it creates a fresh plan. Never use `-lock=false` to bypass a
  concurrent apply. If a run fails during apply, inspect the remote state and
  a new plan before retrying.

Host shipping only starts after a successful apply. If Terraform succeeds but
shipping fails, fix the host/credentials and rerun **Run workflow** on `main`:
a fresh no-op Terraform plan is safe, then shipping retries. No automatic
rollback is attempted after a paid domain purchase or partial host bootstrap.

References: [Terraform S3 locking](https://developer.hashicorp.com/terraform/language/backend/s3),
[GitHub's self-hosted runner warning for public repositories](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).
