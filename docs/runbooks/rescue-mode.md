# Runbook — rescue mode

Use when the server does not boot, sshd is misconfigured, or you are locked out
(wrong `admin_cidrs`, lost `ops` key).

## Boot into rescue

```bash
scw baremetal server reboot <server-id> boot-type=rescue zone=fr-par-2
scw baremetal server get <server-id> zone=fr-par-2   # wait for status "running", boot_type "rescue"
```

Credentials for the rescue user are shown in the console (Elastic Metal →
server → Rescue). Your Scaleway SSH keys are also injected:

```bash
ssh rescue@<ip>
```

## Mount the RAID1 root

```bash
sudo mdadm --assemble --scan
cat /proc/mdstat                   # md arrays present
lsblk -f                           # find the ext4 md device holding /
sudo mount /dev/mdX /mnt
sudo mount --bind /dev /mnt/dev && sudo mount --bind /proc /mnt/proc && sudo mount --bind /sys /mnt/sys
sudo chroot /mnt
```

## Typical fixes (inside chroot)

- Locked out by ufw: `ufw allow from <new-cidr> to any port 22 proto tcp`
- Lost `ops` key: `echo '<pubkey>' >> /home/ops/.ssh/authorized_keys`
- Broken sshd config: `rm /etc/ssh/sshd_config.d/99-hardening.conf` then
  re-ship after boot
- Docker not starting: `mv /etc/docker/daemon.json /root/daemon.json.bak`

## Back to normal boot

```bash
exit                               # leave chroot
sudo umount -R /mnt
scw baremetal server reboot <server-id> boot-type=normal zone=fr-par-2
```

Then `scripts/ship.sh ops@<ip>` to re-converge the host with the repo.
