# hermes-elastic-metal — Docker host on Scaleway Elastic Metal

Infrastructure as code for a single bare-metal Docker host (`emeta-01`,
EM-A116X-SSD, Ubuntu 26.04 LTS, `fr-par-2`) that runs the self-hosted Hermes
agent. The server is disposable: everything is rebuildable from this repo plus
a restic backup stored in Scaleway Object Storage in another region.

## Quick start

```bash
cp .env.example .env && $EDITOR .env && source .env
cd terraform
cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars
terraform init && terraform plan -out=tfplan
terraform apply tfplan                      # ~15–30 min bare-metal install
cd ..
./scripts/ship.sh "$(terraform -chdir=terraform output -raw ipv4)"
```

See [docs/runbooks/provision.md](docs/runbooks/provision.md) for the full
procedure, [docs/runbooks/dr.md](docs/runbooks/dr.md) for disaster recovery and
[docs/contexts/hermes-host.md](docs/contexts/hermes-host.md) for the agent
context.

## Layout

- `terraform/` — server, SSH key, backup bucket, IAM key (local state)
- `scripts/` — `bootstrap.sh` (idempotent host setup), `backup.sh`,
  `restore-drill.sh`, `healthcheck.sh`, `ship.sh`
- `config/` — files installed verbatim on the host
- `systemd/` — units and timers installed on the host
- `docs/` — runbooks and agent context

## Constraints to remember

- No snapshots, no cloud-init, no live migration on Elastic Metal.
- Changing `os` reinstalls the server; changing `offer` recreates it
  (`prevent_destroy` guards against that).
- CPU is Sandy Bridge: no AVX2. Images built for `x86-64-v3` crash with `SIGILL`.
