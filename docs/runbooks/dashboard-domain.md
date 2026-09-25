# Dashboard domain and DNS

`antelab.eu` is registered manually in the Scaleway console (1 year,
auto-renewal). Terraform only reads its DNS zone through the
`scaleway_domain_zone` data source and manages the A record
`apollo.antelab.eu` pointing to the Elastic Metal server's public IPv4.

Registration is not managed by Terraform: the Scaleway API rejected
individual `.eu` registrants with `invalid SIRET or SIREN`.

A DNS record alone does not start the dashboard, open a firewall port, or
provide HTTPS/authentication.

## Register or change the domain

1. Scaleway console → **Domains and DNS** → **Register a domain**.
2. Legal form **Individual**, EU/EEA address, same project as Terraform.
3. Validate the EURid verification email and wait for status **Active**.
4. If the name differs, update `root_domain` in a reviewed PR.

Terraform fails at plan time if the zone does not exist yet.

## Verify

```bash
terraform -chdir=terraform output -raw dashboard_fqdn
terraform -chdir=terraform output -raw ipv4
dig +short A apollo.antelab.eu
```

The resolved A address must match the Terraform IPv4 output. DNS propagation
can take time.

## Publish the dashboard over HTTPS

Caddy (Ubuntu `caddy` package) terminates TLS with automatic Let's Encrypt
certificates and proxies `https://apollo.antelab.eu` to Hermes on
`127.0.0.1:9119`. Hermes stays on loopback; port 9119 is never exposed.

`scripts/install-caddy.sh` refuses to run (and never opens 80/443) unless
`http://127.0.0.1:9119/api/status` reports `auth_required: true`.

### 1. Prerequisites

- A record resolves to the server (see *Verify* above).
- Hermes dashboard running natively under systemd, bound to `127.0.0.1:9119`.

### 2. Configure Hermes authentication (on the host)

1. Create an OAuth/OIDC client at your provider (GitHub, Google, ...):
   - Redirect / callback URI: `https://apollo.antelab.eu/<hermes callback path>`
     (see the Hermes auth docs for the exact path).
2. Put the client ID/secret in the Hermes service environment on the host
   (e.g. a `0600` `EnvironmentFile`), never in this repo.
3. Set `dashboard.public_url = https://apollo.antelab.eu` (exact, HTTPS).
4. Restart Hermes and check:

```bash
ssh ops@<ip> curl -s http://127.0.0.1:9119/api/status   # must contain "auth_required": true
```

### 3. Install the proxy

```bash
scripts/ship.sh ops@$(terraform -chdir=terraform output -raw ipv4)
```

`ship.sh` runs `install-caddy.sh` after bootstrap. To run it alone:

```bash
ssh ops@<ip> sudo DASHBOARD_FQDN=apollo.antelab.eu bash /tmp/hermes/scripts/install-caddy.sh
```

The script installs Caddy, deploys `config/caddy/Caddyfile`, writes
`/etc/default/caddy`, validates the config, opens `80/tcp` + `443/tcp` in UFW,
starts Caddy and checks HTTPS locally.

### 4. Verify

```bash
curl -sI https://apollo.antelab.eu                       # 200 or redirect to login
curl -s  https://apollo.antelab.eu/api/status            # "auth_required": true
nc -vz -w3 apollo.antelab.eu 9119                        # must fail
ssh ops@<ip> sudo healthcheck.sh                         # dashboard proxy: caddy active
```

### Troubleshooting

- Script says `auth_required != true` → finish step 2.
- Certificate not issued → `journalctl -u caddy`; port 80 must be reachable
  from the internet and DNS must point to the server.
- 502 Bad Gateway → Hermes is down: `systemctl status <hermes unit>`.

### Rollback

```bash
ssh ops@<ip> 'sudo systemctl disable --now caddy && sudo ufw delete allow 80/tcp && sudo ufw delete allow 443/tcp'
```
