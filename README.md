# hermes-elastic-metal — Docker host on Scaleway Elastic Metal

Infrastructure as code for a single bare-metal Docker host (`emeta-01`,
EM-A116X-SSD, Ubuntu 26.04 LTS, `fr-par-2`) that runs the self-hosted Hermes
agent. The server is disposable: everything is rebuildable from this repo plus
a restic backup stored in Scaleway Object Storage in another region.

## Quick start

First create the private state bucket and configure credentials as described in
[the state runbook](docs/runbooks/terraform-state.md). If this server already
exists, migrate its **existing** local state before running any new plan.

```bash
cp .env.example .env && $EDITOR .env && source .env
cd terraform
cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars
terraform init -backend-config="bucket=${STATE_BUCKET:?set the state bucket}"
terraform plan -out=tfplan
terraform apply tfplan                      # ~15–30 min bare-metal install
cd ..
./scripts/ship.sh "$(terraform -chdir=terraform output -raw ipv4)"
```

See [docs/runbooks/provision.md](docs/runbooks/provision.md) for the full
procedure, [docs/runbooks/dr.md](docs/runbooks/dr.md) for disaster recovery and
[docs/contexts/hermes-host.md](docs/contexts/hermes-host.md) for the agent
context.

## Layout

- `terraform/` — server, SSH key, backup bucket, IAM key (locked remote state)
- `scripts/` — `bootstrap.sh` (idempotent host setup), `install-netdata.sh`
  (optional, independent monitoring), `backup.sh`, `restore-drill.sh`,
  `healthcheck.sh`, `ship.sh`
- `config/` — host configuration including the localhost-only Netdata dashboard
  ([maintenance runbook](docs/runbooks/maintenance.md#host-metrics-netdata))
- `systemd/` — units and timers installed on the host
- `docs/` — runbooks and agent context

## Constraints to remember

- No snapshots, no cloud-init, no live migration on Elastic Metal.
- Changing `os` reinstalls the server; changing `offer` recreates it
  (`prevent_destroy` guards against that).
- CPU is Sandy Bridge: no AVX2. Images built for `x86-64-v3` crash with `SIGILL`.
- Applying `terraform/domain.tf` purchases and auto-renews the dashboard domain.
  Review the plan and registrar price before applying; see
  [the domain runbook](docs/runbooks/dashboard-domain.md).
