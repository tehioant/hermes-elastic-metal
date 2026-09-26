# Runbook — maintenance

| Cadence | Action | How |
|---|---|---|
| Continuous | Security patches, auto-reboot at 04:00 (server time) only when required | `unattended-upgrades` (installed by bootstrap) |
| Daily 02:30 | Backup to Object Storage | `restic-backup.timer` |
| Daily 07:00 | Health check (RAID, SMART, disk, memory, containers, RAS, last backup) | `healthcheck.timer` — `journalctl -u healthcheck` |
| Weekly Sun 02:00 | SMART short self-test | `smartd` (`DEVICESCAN -s (S/../.././02)`) |
| Weekly Sun 03:00 | Prune images/containers/networks older than 7 days | `docker-prune.timer` |
| Monthly | Restore drill | `sudo bash -c 'set -a; . /etc/restic/env; restore-drill.sh'` |
| Monthly | Check the nightly reboots happened | `last reboot \| head`; manual: `sudo reboot` if `/var/run/reboot-required` exists |
| Monthly | Copy `terraform/terraform.tfstate` off-laptop | manual |
| Quarterly | Terraform drift check | `terraform -chdir=terraform plan -detailed-exitcode` (exit 2 = drift) |
| Quarterly | Review `admin_cidrs`, rotate restic IAM key | `terraform taint scaleway_iam_api_key.backup && terraform apply` then `ship.sh` |

## Daily glance (manual)

```bash
ssh ops@<ip> 'sudo healthcheck.sh; docker ps --format "table {{.Names}}\t{{.Status}}"; df -h /'
```

## Host metrics (Netdata)

`install-netdata.sh` (run by `ship.sh` after `bootstrap.sh`, independently:
a Netdata failure only warns and never blocks host setup) installs the stable
**native** Netdata package from Netdata's
repository (not a privileged Docker container), enables its updater, and keeps
its dashboard bound to `127.0.0.1:19999`. Anonymous telemetry is disabled and
no Netdata Cloud account is required. Its history is local to the host; it is
not an off-host backup or an external availability check.

After deploying through `ship.sh`, check the service and loopback endpoint:

```bash
ssh ops@<ip> 'systemctl is-active netdata; sudo ss -ltnp "( sport = :19999 )"; curl -fsS http://127.0.0.1:19999/api/v1/info >/dev/null'
```

Open `https://netdata.antelab.eu` (Google login via oauth2-proxy, see
`netdata-domain.md`), or use `ssh -N -L 19999:127.0.0.1:19999 ops@<ip>` and
`http://127.0.0.1:19999/`. Do **not** allow port 19999 in UFW or the Docker
port allowlist, and never proxy it without `forward_auth`. The config is owned by
`config/netdata/netdata.conf` and applied by `install-netdata.sh`; do not edit it
only on the server. To retry alone: `ssh ops@<ip> sudo bash /tmp/hermes/scripts/install-netdata.sh`.
The script refuses a pre-existing Netdata package that it did not install
itself; inspect or remove that package deliberately before rerunning.
For upgrades, check the installed updater schedule (systemd timer or cron)
and its logs; Netdata's kickstart installer enables automatic updates on the
stable channel.

## Alerting

Netdata's local charts/alerts cannot report a full host outage. The existing
`healthcheck.service` also exits non-zero on failures, but has no notification
route. Add an `OnFailure=` webhook unit separately once a dedicated alert
channel is available, and use an off-host heartbeat for host-down detection.

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
