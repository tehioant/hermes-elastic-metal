# Runbook — backup & disaster recovery

**RPO** 24 h (daily timer at 02:30 ± 15 min). **RTO** 60–90 min for a full
rebuild. Tighten RPO by editing `systemd/restic-backup.timer`
(`OnCalendar=hourly`) and re-shipping.

## What is protected, and how

| Data | Where | Mechanism |
|---|---|---|
| Docker named volumes | `/var/lib/docker/volumes` | tar per volume → restic |
| Host config | `/etc` | restic |
| Compose / stack definitions | git | source of truth, not a backup |
| Terraform state | `terraform/terraform.tfstate` (local) | **back it up yourself** (see below) |
| Restic password | `.restic-password` + password manager | without it backups are lost |

Restic repo: `s3:https://s3.nl-ams.scw.cloud/hermes-backup-emeta-01`, tag
`emeta-01`, retention 7 daily / 4 weekly / 12 monthly.

Bucket: versioning on, Object Lock **COMPLIANCE 35 days** (no one can delete a
version before 35 days, including the account owner), noncurrent versions
expire after 45 days. `restic forget --prune` deletes → creates delete
markers; the locked versions survive a compromised host key.

## Terraform state

Local state is a single point of failure for `terraform plan`. After every
apply, copy `terraform/terraform.tfstate` somewhere safe (password manager
attachment or encrypted cloud drive). Losing it is recoverable with
`terraform import` but tedious.

## Monthly restore drill (mandatory)

```bash
ssh ops@<ip> 'sudo bash -c "set -a; . /etc/restic/env; restore-drill.sh"'
```

Restores the latest snapshot, unpacks one volume into a scratch Docker volume,
lists it, cleans up. Non-zero exit = your backups are not usable — fix before
anything else.

## Restore a single volume

```bash
sudo bash -c 'set -a; . /etc/restic/env
  restic snapshots --tag emeta-01
  restic restore latest --target /tmp/restore --include /var/backups/docker/<volume>.tar.gz'
docker compose down <service>
docker run --rm -v <volume>:/target -v /tmp/restore/var/backups/docker:/src:ro \
  busybox:1.37 sh -c "rm -rf /target/* && tar xzf /src/<volume>.tar.gz -C /target"
docker compose up -d <service>
rm -rf /tmp/restore
```

## Full host rebuild

1. **Reinstall the same server** (keeps IP):
   `scw baremetal server install <server-id> os-id=<os-id> ssh-key-ids.0=<key-id> zone=fr-par-2`
   — or, if the hardware is dead, order a new one: temporarily set
   `prevent_destroy = false`, `terraform apply -replace=scaleway_baremetal_server.this`,
   then restore `prevent_destroy`. The IP changes; update `admin_cidrs` if needed.
2. Wait for SSH as root (15–30 min).
3. `scripts/ship.sh root@<ip>` — bootstrap reuses the existing restic repo
   (`restic cat config` succeeds, so no `init`).
4. Restore volumes:
   ```bash
   sudo bash -c 'set -a; . /etc/restic/env; restic restore latest --target /tmp/restore --include /var/backups/docker'
   for f in /tmp/restore/var/backups/docker/*.tar.gz; do
     v="$(basename "${f%.tar.gz}")"
     docker volume create "$v"
     docker run --rm -v "$v:/target" -v /tmp/restore/var/backups/docker:/src:ro busybox:1.37 tar xzf "/src/$v.tar.gz" -C /target
   done
   ```
5. Redeploy stacks from git: `docker compose up -d`.
6. Run `healthcheck.sh` and the restore drill; confirm the next timer fires.

## Losing the restic password

There is no recovery. The repo is AES-256 encrypted with that password. Start a
new repository (`restic init` in a new bucket) and accept the gap.
