# Runbook — maintenance

| Cadence | Action | How |
|---|---|---|
| Continuous | Security patches (no auto-reboot) | `unattended-upgrades` (installed by bootstrap) |
| Daily 02:30 | Backup to Object Storage | `restic-backup.timer` |
| Daily 07:00 | Health check (RAID, SMART, disk, memory, containers, RAS, last backup) | `healthcheck.timer` — `journalctl -u healthcheck` |
| Weekly Sun 02:00 | SMART short self-test | `smartd` (`DEVICESCAN -s (S/../.././02)`) |
| Weekly Sun 03:00 | Prune images/containers/networks older than 7 days | `docker-prune.timer` |
| Monthly | Restore drill | `sudo bash -c 'set -a; . /etc/restic/env; restore-drill.sh'` |
| Monthly | Reboot window for kernel updates | `sudo needrestart -r l` → `sudo reboot` if a kernel is pending |
| Monthly | Copy `terraform/terraform.tfstate` off-laptop | manual |
| Quarterly | Terraform drift check | `terraform -chdir=terraform plan -detailed-exitcode` (exit 2 = drift) |
| Quarterly | Review `admin_cidrs`, rotate restic IAM key | `terraform taint scaleway_iam_api_key.backup && terraform apply` then `ship.sh` |

## Daily glance (manual)

```bash
ssh ops@<ip> 'sudo healthcheck.sh; docker ps --format "table {{.Names}}\t{{.Status}}"; df -h /'
```

## Alerting

v1 has no external alerting (personal account). `healthcheck.service` exits
non-zero on any failure, so the cheapest upgrade is an `OnFailure=` unit that
POSTs to a webhook (ntfy, Slack, e-mail). Add it in `systemd/` and re-ship.

## Upgrading Docker

Docker CE comes from `download.docker.com`, excluded from unattended-upgrades
(security origins only). Upgrade deliberately:

```bash
sudo apt-get update && sudo apt-get install --only-upgrade docker-ce docker-ce-cli containerd.io
```

`live-restore: true` keeps containers running across a daemon restart.

## Changing the OS or offer

Never edit `os_version`/`offer_name` casually: `os` change = reinstall (wipes
disks), `offer` change = new server. Follow `dr.md` "Full host rebuild".
