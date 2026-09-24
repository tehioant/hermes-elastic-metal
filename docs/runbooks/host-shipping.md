# Private host shipping after Terraform apply

The deployment job runs on a **GitHub-hosted** runner. It joins a restricted
Tailscale network, checks the server's pinned SSH host key and `ops` sudo
access **before** Terraform apply, then ships the same commit and checks
Docker and the backup/healthcheck timers. This is continuous deployment to
an **already provisioned and manually enrolled** host—not automatic first
provisioning. The state guard rejects an empty backend; a new host needs a
separate trusted first-bootstrap phase. Do not use a persistent self-hosted
runner on this public repo or open public SSH for GitHub's changing IP ranges.

## One-time server and tailnet setup

1. Merge the [non-resetting firewall](firewall.md), [dedicated deploy key](deploy-ssh-key.md),
   the explicit-ops-key and key-transport PRs (#12–#13), and the
   restic-password guard before host shipping. Verify that Terraform's
   `ssh_public_key` belongs to your intended privileged admin key; restricted
   root keys are no longer copied to `ops`. On the existing server,
   audit UFW and seed `/etc/hermes/ssh-admin-cidrs` with the *current* rules as
   the firewall guide explains. Keep a tested public admin/console fallback.
2. Install Tailscale on the server using its [official Linux guide](https://tailscale.com/docs/install/linux),
   enroll it with `tag:metal`, and verify the machine is online and reachable
   over the tailnet. Keep public SSH restricted to your admin CIDRs; bootstrap
   adds only TCP/22 on `tailscale0`. Do not use `0.0.0.0/0` for admin CIDRs.
3. In the tailnet policy, define `tag:ci` and `tag:metal` with owners you
   control. Remove any default broad allow-all access *after preserving your
   own admin access*. Add a narrow grant from `tag:ci` to `tag:metal` on
   `tcp:22` only. Tailscale ACLs and grants can coexist; audit both. The
   server's SSH key and `ops` authorized key remain separate checks.
4. Enroll a dedicated restricted CI public key in `/etc/hermes/deploy.pub`
   and place its private key in the GitHub `production` environment secret
   `SSH_DEPLOY_PRIVATE_KEY`, following [deploy-ssh-key.md](deploy-ssh-key.md).
   Rerun the new bootstrap once from your trusted laptop, then test the CI
   key's noninteractive `sudo -n true` over the tailnet.

## Tailscale workload identity

In the Tailscale **Trust credentials** console, create a GitHub Actions
federated identity. Set the issuer to
`https://token.actions.githubusercontent.com`, subject to
`repo:tehioant/hermes-elastic-metal:environment:production`, and custom
claim matches for `ref=refs/heads/main` and
`workflow_ref=tehioant/hermes-elastic-metal/.github/workflows/deploy.yml@refs/heads/main`.
Grant only the `auth_keys` scope for `tag:ci`. This pins which workflow may
mint a short-lived ephemeral tailnet node. The GitHub workflow requests
`id-token: write`; no long-lived Tailscale OAuth secret is stored in GitHub.

In GitHub's `production` environment, restrict deployment branches to `main`
and set these variables:

- `TS_CLIENT_ID`: federated identity client ID from Tailscale.
- `TS_AUDIENCE`: audience from Tailscale.
- `TAILSCALE_HOST`: server's stable tailnet IPv4 or full MagicDNS name.

Set these **environment secrets**, in addition to the Terraform secrets in
[cd.md](cd.md):

- `SSH_DEPLOY_PRIVATE_KEY`: dedicated unencrypted CI private key (multiline).
- `SSH_KNOWN_HOSTS`: a known_hosts line whose hostname is **exactly**
  `TAILSCALE_HOST` and whose public host key was verified through an existing
  trusted SSH session or the provider console. Do not trust `ssh-keyscan`
  output from the workflow without out-of-band verification.
- `RESTIC_PASSWORD`: **the existing** restic repository password from the
  laptop's ignored `.restic-password` file/password manager. Do not generate
  a new one, paste it in chat, or commit it. The bootstrap guard refuses a
  mismatch before changing the host's backup password file.

The workflow first checks that all settings exist and can SSH with the pinned
key. It then applies a locked, guarded Terraform plan. Only after success does
it run `ship.sh` and check the three systemd units. If shipping fails after a
successful apply, fix the cause and rerun **Run workflow** on `main`: a fresh
plan is generated and shipping is retried. No automatic rollback is attempted.

Sources: [Tailscale GitHub Action](https://tailscale.com/docs/integrations/github/github-action),
[workload identity federation](https://tailscale.com/docs/features/workload-identity-federation),
[GitHub OIDC claims](https://docs.github.com/en/actions/reference/security/oidc),
[host-key verification warning](https://man.openbsd.org/ssh-keyscan.1).
