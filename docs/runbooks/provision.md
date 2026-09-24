# Runbook — first provisioning

Expected duration: ~45 min (of which 15–30 min is the bare-metal install).

## 0. Prerequisites (laptop)

```bash
brew install scw terraform shellcheck
cp .env.example .env && $EDITOR .env && source .env
scw account project list                                   # credentials work
scw baremetal offer list zone=fr-par-2 | grep EM-A116X-SSD  # stock "available"
scw baremetal os list zone=fr-par-2 | grep 26.04            # version string matches terraform/variables.tf
```

Confirm the Hermes image is not built for `x86-64-v3` (the CPU has no AVX2).
Set up the private, versioned, encrypted state bucket and its credentials as
described in [terraform-state.md](terraform-state.md) before initializing.

## 1. Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars            # ssh_public_key, admin_cidrs (curl -4 ifconfig.me)
terraform init
terraform plan -out=tfplan          # expect 7 to add, 0 to change, 0 to destroy
terraform apply tfplan
```

Billing starts at apply (~€0.077/h, hourly plan). The server appears in the
console immediately; installation runs 15–30 min. Poll:

```bash
scw baremetal server list zone=fr-par-2
ssh -o ConnectTimeout=5 root@"$(terraform output -raw ipv4)" true && echo READY
```

## 2. Bootstrap the host

From the repo root:

```bash
scripts/ship.sh root@"$(terraform -chdir=terraform output -raw ipv4)"
```

`ship.sh` generates `.restic-password` on first run. **Copy it into your
password manager now** — without it the backups are unreadable.

What bootstrap does, in order: base packages → Docker CE (official repo) →
`daemon.json` → `ops` user (docker + passwordless sudo, your SSH key) → sshd
hardening (root login off) → ufw (22 from `admin_cidrs` only) + DOCKER-USER
chain → unattended security upgrades + fail2ban → smartd/mdmonitor/rasdaemon →
restic repo init + daily timer → prune + healthcheck timers → `docker run busybox`.

## 3. Validate

```bash
IP="$(terraform -chdir=terraform output -raw ipv4)"
ssh ops@"$IP"

docker run --rm busybox:1.37 echo "hello from busybox"
docker run --rm busybox:1.37 nslookup scaleway.com
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges busybox:1.37 id
cat /proc/mdstat
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT
sudo smartctl -H /dev/sda && sudo smartctl -H /dev/sdb
sudo ufw status
sudo iptables -L DOCKER-USER -n
sudo systemctl list-timers
sudo healthcheck.sh
sudo systemctl start restic-backup.service && sudo journalctl -u restic-backup -n 20
```

Expected: busybox prints, DNS resolves, `md*` arrays `[UU]`, SMART `PASSED`,
ufw active with only your CIDR on 22, all timers scheduled, healthcheck all ✅,
first backup snapshot pushed.

## 4. Re-running

Bootstrap is idempotent. After any change to `scripts/`, `config/` or
`systemd/`:

```bash
scripts/ship.sh ops@"$IP"
```

## 5. Publishing a container port

Docker bypasses ufw; the DOCKER-USER chain drops everything by default.

```bash
echo "8080 0.0.0.0/0" | sudo tee -a /etc/docker/published-ports.allow
sudo systemctl restart docker-user-rules
```
