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
| Terraform state | Private, versioned `nl-ams` state bucket (key `hermes-elastic-metal/terraform.tfstate`) | encrypted versioning + separate protected backup |
| Restic password | `.restic-password` + password manager | without it backups are lost |

Restic repo: `s3:https://s3.nl-ams.scw.cloud/hermes-backup-emeta-01`, tag
`emeta-01`, retention 7 daily / 4 weekly / 12 monthly.

Bucket: versioning on, Object Lock **COMPLIANCE 35 days** (no one can delete a
version before 35 days, including the account owner), noncurrent versions
expire after 45 days. `restic forget --prune` deletes → creates delete
markers; the locked versions survive a compromised host key.

## Terraform state

The private remote bucket is the **authoritative** state (see the
[migration runbook](terraform-state.md)). The laptop's original
`terraform.tfstate` is only an encrypted pre-migration backup; do not copy
state after each apply. Keep a separate encrypted, access-restricted backup of
the remote state and protect access to the bucket's previous versions. State
can contain IAM secrets, registrant details and a domain transfer code: never
attach it to chat, issues or PRs.

If state is lost or corrupt:

1. Freeze manual and GitHub applies.
2. Preserve the current remote object and its versions.
3. Under a trusted operator account, recover the last known-good version of
   `hermes-elastic-metal/terraform.tfstate` through the storage console,
   keeping bucket versioning and encryption intact.
4. Reinitialize Terraform against the same bucket and check lineage and
   resource addresses with the [read-only state guard](terraform-state.md).
5. Review a new plan before unfreezing deployment.

Never start with an empty backend or use `-lock=false`. Importing resources is
a last-resort manual recovery path if all protected state copies are lost.

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

1. Freeze GitHub deployment while rebuilding. **Reinstall the same server**
   (keeps IP): `scw baremetal server install <server-id> os-id=<os-id> ssh-key-ids.0=<key-id> zone=fr-par-2`
   — or, if hardware is dead, order a new one through a deliberately reviewed
   manual Terraform recovery. The automatic CD plan guard rejects deletion and
   replacement, and `prevent_destroy` must only be disabled temporarily in a
   controlled recovery checkout. The IP changes; update DNS and admin CIDRs.
2. Wait for SSH as root (15–30 min).
3. `scripts/ship.sh root@<ip>` — bootstrap reuses the existing restic repo
   (`restic cat config` succeeds, so no `init`). Confirm the Terraform
   `ssh_public_key` is the explicitly trusted `ops` admin key before shipping.
4. Install and re-enroll Tailscale as the tagged host, restore the dedicated
   CI deploy public key in `/etc/hermes/deploy.pub`, and rerun
   `scripts/ship.sh ops@<ip>` to authorize it. A rebuilt host may have a
   **new** SSH host key: verify it out of band before updating
   `SSH_KNOWN_HOSTS` in GitHub; never accept a fresh `ssh-keyscan` blindly.
   Test personal and CI `ops` access before re-enabling CD.
5. Restore volumes:
   ```bash
   sudo bash -c 'set -a; . /etc/restic/env; restic restore latest --target /tmp/restore --include /var/backups/docker'
   for f in /tmp/restore/var/backups/docker/*.tar.gz; do
     v="$(basename "${f%.tar.gz}")"
     docker volume create "$v"
     docker run --rm -v "$v:/target" -v /tmp/restore/var/backups/docker:/src:ro busybox:1.37 tar xzf "/src/$v.tar.gz" -C /target
   done
   ```
6. Redeploy stacks from git: `docker compose up -d`.
7. Run `healthcheck.sh` and the restore drill; confirm the next timer fires.
   Re-enable CD only after reviewing a new plan against the recovered state.

## Losing the restic password

There is no recovery. The repo is AES-256 encrypted with that password. Start a
new repository (`restic init` in a new bucket) and accept the gap.
