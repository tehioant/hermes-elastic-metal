# Context: hermes-host (Scaleway Elastic Metal Docker host)

## Scope
Single bare-metal server `emeta-01` (EM-A116X-SSD, Ubuntu 26.04 LTS, `fr-par-2`,
personal Scaleway account) running Docker for the self-hosted Hermes agent.
Everything is code: Terraform creates the server + backup bucket + IAM key,
`scripts/bootstrap.sh` configures the host, restic backs up Docker volumes to
Object Storage in `nl-ams`. No Datadog, no private network, no CI (v1).

## Layout
- `terraform/` — `server.tf` (offer/OS lookup, SSH key, server with
  `prevent_destroy`), `backup.tf` (bucket + versioning + Object Lock COMPLIANCE
  35d, IAM app/policy/key), remote locked state in a separate private bucket.
  Existing installations must migrate the local state first; see
  `docs/runbooks/terraform-state.md`.
- `scripts/bootstrap.sh` — idempotent, run as root once then as `ops` via sudo.
  Installs files from `config/` and `systemd/` with `install_if_changed`.
- `scripts/ship.sh <user@host>` — tars `scripts config systemd` to `/tmp/hermes`
  on the host, reads secrets from `terraform output`, runs bootstrap. Generates
  `.restic-password` (gitignored) on first run.
- `scripts/backup.sh` / `restore-drill.sh` / `healthcheck.sh` — installed to
  `/usr/local/sbin`, driven by systemd timers in `systemd/`.
- `scripts/install-caddy.sh` + `config/caddy/Caddyfile` — publishes the Hermes
  dashboard (`127.0.0.1:9119`) at `https://apollo.antelab.eu`; opens 80/443
  only if Hermes `/api/status` reports `auth_required: true` with the
  `self-hosted` OIDC provider. Guide: `docs/runbooks/dashboard-domain.md`.
- Dashboard auth = Hermes built-in OIDC → Google (`~/.hermes/.env` on host,
  not in repo). Allowlist = Google app **test users** (app stays *Testing*;
  publishing opens it to every Google account). No roles: every user is admin.
  No refresh token → silent Google re-login ~hourly.
- `config/docker/published-ports.allow` (host-only file) — `port [source-cidr]`
  lines opened in the DOCKER-USER chain; empty by default = nothing published.

## Key commands
```bash
source .env                                   # SCW_* credentials
terraform -chdir=terraform plan -out=tfplan   # never apply without reading the plan
scripts/ship.sh root@$(terraform -chdir=terraform output -raw ipv4)   # first run
scripts/ship.sh ops@<ip>                      # subsequent runs (root login disabled)
ssh ops@<ip> sudo systemctl list-timers        # restic-backup, docker-prune, healthcheck
ssh ops@<ip> sudo healthcheck.sh
ssh ops@<ip> 'sudo bash -c "set -a; . /etc/restic/env; restic snapshots"'
ssh ops@<ip> 'sudo bash -c "set -a; . /etc/restic/env; restore-drill.sh"'
shellcheck scripts/*.sh config/docker/*.sh
```

## Hard constraints
- Elastic Metal: no snapshots, no cloud-init, no live migration.
- Changing `os` in Terraform **reinstalls** (wipes disks); changing `offer`
  **recreates**. `prevent_destroy = true` blocks accidental destroy.
- `reinstall_on_config_changes = false`: changing `ssh_key_ids` in TF has no
  effect on a running server; manage keys on the host instead.
- CPU Xeon E3-1220 (Sandy Bridge): **no AVX2**. `x86-64-v3` images → `SIGILL`.
- Default partitioning = RAID1 over `/dev/sda` + `/dev/sdb` (`/dev/md*`).
- IAM cannot scope to one bucket; the restic key has object R/W/D on the whole
  project. Ransomware guard = bucket versioning + Object Lock, not IAM.
- Object Lock COMPLIANCE cannot be shortened or removed, even by the owner.

## Troubleshooting
- `SIGILL` / "Illegal instruction" in a container → image needs AVX2; rebuild
  for `x86-64-v2` or pick another tag.
- Locked out after bootstrap → root login is disabled; use `ops@`. If `ops`
  key is missing, boot rescue mode from the console (`docs/runbooks/rescue-mode.md`).
- `ufw` allows only `admin_cidrs` on 22. New home IP → re-run `ship.sh` with
  updated `terraform.tfvars` or `sudo ufw allow from <cidr> to any port 22`.
- Published container port unreachable → add `port cidr` to
  `/etc/docker/published-ports.allow`, `sudo systemctl restart docker-user-rules`.
- `healthcheck` failing → `journalctl -u healthcheck --since today`.
- `PUBLIC WITHOUT OIDC AUTH` → `sudo systemctl stop caddy`, fix Hermes OIDC
  env, re-run `install-caddy.sh`; locked out → break-glass in the runbook.
- Backup failing → `journalctl -u restic-backup`; another job holding
  `/run/lock/restic.lock` (drill) makes backup exit immediately by design.
- RAID degraded → `cat /proc/mdstat`, `smartctl -a /dev/sdX`; open a Scaleway
  ticket for disk swap, then `mdadm --add /dev/mdN /dev/sdXn`.
- Terraform `plan` shows server replacement → STOP; check `offer`/`zone` diff.

## Change checklist
1. Edit code → `shellcheck` / `terraform fmt -check && terraform validate`.
2. `terraform plan` and read it; apply only when the user says so.
3. Host changes go through `ship.sh` (re-runs bootstrap), never by hand-editing
   files under `/etc` that this repo owns.
4. Small commits with `Co-Authored-By: Warp <agent@warp.dev>`.
