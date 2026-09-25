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
`http://127.0.0.1:9119/api/status` reports `auth_required: true` **and**
`"self-hosted"` in `auth_providers` (Hermes OIDC provider). A password-only
(`basic`) setup is never published.

Login uses Hermes' built-in OIDC provider pointed at Google. The allowlist is
the Google OAuth app's **test users** list (app stays in *Testing*). Every
logged-in user is a full Hermes admin (no roles): only add people you would
give SSH access to.

### 1. Prerequisites

- A record resolves to the server (see *Verify* above).
- Hermes dashboard running natively under systemd, bound to `127.0.0.1:9119`.
- A Hermes version where `dashboard.public_url` engages the auth gate on a
  loopback bind and `HERMES_DASHBOARD_OIDC_CLIENT_SECRET` is supported
  (checked in step 3; upgrade Hermes if it fails).

### 2. Create the Google OAuth client (console, one-time)

Google OAuth clients for external apps cannot be managed by Terraform.

1. [Google Cloud console](https://console.cloud.google.com) → new project
   `hermes-dashboard` → **Google Auth Platform**.
2. **Branding**: app name `Hermes dashboard`, your support email.
3. **Audience**: user type **External**, publishing status **Testing**;
   add each allowed person under **Test users** (Google account email).
4. **Data access**: scopes `openid`, `email`, `profile` only.
5. **Clients** → **Create client** → type **Web application**,
   authorized redirect URI `https://apollo.antelab.eu/auth/callback`.
6. Store the client ID and secret in your password manager.

> Never click **Publish app**: these scopes need no Google review, so
> publishing silently lets **any** Google account log in.

### 3. Configure Hermes (on the host, one-time)

As the user running Hermes, add to `~/.hermes/.env` (loaded by Hermes at
startup; never commit these values):

```bash
HERMES_DASHBOARD_OIDC_ISSUER=https://accounts.google.com
HERMES_DASHBOARD_OIDC_CLIENT_ID=<client id>
HERMES_DASHBOARD_OIDC_CLIENT_SECRET=<client secret>
HERMES_DASHBOARD_PUBLIC_URL=https://apollo.antelab.eu
```

```bash
chmod 600 ~/.hermes/.env
sudo systemctl restart <hermes unit>
curl -s http://127.0.0.1:9119/api/status
# must contain "auth_required": true and "auth_providers": ["self-hosted"]
```

### 4. Install the proxy

```bash
scripts/ship.sh ops@$(terraform -chdir=terraform output -raw ipv4)
```

`ship.sh` runs `install-caddy.sh` after bootstrap. To run it alone:

```bash
ssh ops@<ip> sudo DASHBOARD_FQDN=apollo.antelab.eu bash /tmp/hermes/scripts/install-caddy.sh
```

The script installs Caddy, deploys `config/caddy/Caddyfile`, sets
`DASHBOARD_FQDN` in a `caddy.service` drop-in, validates the config, opens `80/tcp` + `443/tcp` in UFW,
starts Caddy and checks HTTPS locally.

### 5. Verify

```bash
curl -sI https://apollo.antelab.eu                       # 200 or redirect to login
curl -s  https://apollo.antelab.eu/api/status            # "auth_required": true, "self-hosted"
nc -vz -w3 apollo.antelab.eu 9119                        # must fail
ssh ops@<ip> sudo healthcheck.sh                         # dashboard proxy: hermes OIDC auth required
```

In a browser: a test user logs in via Google; any other Google account is
blocked by Google ("Access blocked"). Logins are audited per user in
`~/.hermes/logs/dashboard-auth.log`.

### Manage users

- **Add**: Google Auth Platform → **Audience** → **Test users** → add email
  (max 100). They see a "Google hasn't verified this app" screen once →
  *Continue*.
- **Remove**: delete the email there. New logins are refused immediately; an
  open session lasts until its Google ID token expires (~1h, no refresh token).

Sessions last about an hour, then Hermes silently re-runs the Google login.

### Break-glass (Google or OIDC login broken)

```bash
ssh ops@<ip> sudo systemctl stop caddy                  # never expose without auth
# on the host: comment out HERMES_DASHBOARD_PUBLIC_URL in ~/.hermes/.env, then
ssh ops@<ip> sudo systemctl restart <hermes unit>       # loopback-only, no auth gate
ssh -L 9119:127.0.0.1:9119 ops@<ip>                     # open http://127.0.0.1:9119
```

Revert the `.env` change, restart Hermes, then re-run `install-caddy.sh`.

### Troubleshooting

- Script says `auth_required != true` → finish step 3; if the values are set,
  Hermes is too old to gate a loopback bind behind `public_url` → upgrade.
- Script says provider is not self-hosted OIDC → OIDC env vars missing/typo in
  `~/.hermes/.env`, or Hermes restarted before they were set; check
  `journalctl -u <hermes unit> | grep dashboard-auth-self-hosted`.
- Google `redirect_uri_mismatch` → client redirect URI must be exactly
  `https://apollo.antelab.eu/auth/callback` and `HERMES_DASHBOARD_PUBLIC_URL`
  exactly `https://apollo.antelab.eu`.
- Google `Access blocked` for an allowed person → add them as test user.
- Certificate not issued → `journalctl -u caddy`; port 80 must be reachable
  from the internet and DNS must point to the server.
- 502 Bad Gateway → Hermes is down: `systemctl status <hermes unit>`.
- `healthcheck` reports `PUBLIC WITHOUT OIDC AUTH` → Hermes auth was lost
  (upgrade, config edit, missing OIDC secret). Run `sudo systemctl stop caddy`
  immediately, fix step 3, then re-run `install-caddy.sh`.

### Rollback

```bash
ssh ops@<ip> 'sudo systemctl disable --now caddy && sudo ufw delete allow 80/tcp && sudo ufw delete allow 443/tcp'
```
