# Netdata at netdata.antelab.eu (Google login)

The Netdata agent has no login of its own. It stays bound to
`127.0.0.1:19999`; Caddy publishes `https://netdata.antelab.eu` and asks
oauth2-proxy (`127.0.0.1:4180`) to authenticate every request with Google
(`forward_auth`). Unauthenticated requests are redirected to Google; if
oauth2-proxy is down, Caddy answers 502 (fail closed).

Access is the same as the Hermes dashboard: the Google OAuth client and its
**test users** list from `dashboard-domain.md` (app stays in *Testing*).

## Pieces
- DNS: `scaleway_domain_record.netdata` (`netdata_subdomain`, output
  `netdata_fqdn`).
- `scripts/install-oauth2-proxy.sh`: installs oauth2-proxy `v7.15.4` from the
  official GitHub release (not in the Ubuntu archive), SHA-256 verified, to
  `/usr/local/bin/oauth2-proxy`; writes `/etc/oauth2-proxy/netdata.env`
  (`0600`) with the Google client ID/secret read from
  `/home/ops/.hermes/.env`, a cookie secret generated once on the host, the
  redirect URL and allowed redirect domain; runs
  `oauth2-proxy-netdata.service` (`DynamicUser`).
- `config/oauth2-proxy/netdata.cfg`: non-secret settings (Google provider,
  12h sessions, `email_domains = *` → the Google test users are the
  allowlist).
- `config/caddy/Caddyfile`: `{$NETDATA_FQDN}` site with `forward_auth`.
- `install-caddy.sh` refuses to open 80/443 if oauth2-proxy is not running,
  and stops Caddy if Netdata answers `/api/v1/info` without login.

## Deploy
1. Prerequisite: Hermes Google OIDC works (`dashboard-domain.md`), so
   `HERMES_DASHBOARD_OIDC_CLIENT_ID/SECRET` exist in `/home/ops/.hermes/.env`.
2. Google Auth Platform → **Clients** → the Hermes client → add authorized
   redirect URI `https://netdata.antelab.eu/oauth2/callback`.
3. DNS:

   ```bash
   source .env
   terraform -chdir=terraform plan -out=tfplan   # expect 1 to add: scaleway_domain_record.netdata
   terraform -chdir=terraform apply tfplan
   dig +short A netdata.antelab.eu               # must return the server IPv4
   ```

4. `scripts/ship.sh ops@$(terraform -chdir=terraform output -raw ipv4)`
   (runs `install-oauth2-proxy.sh`, then `install-caddy.sh`).

To run the two steps alone on the host:

```bash
ssh ops@<ip> sudo NETDATA_FQDN=netdata.antelab.eu bash /tmp/hermes/scripts/install-oauth2-proxy.sh
ssh ops@<ip> sudo DASHBOARD_FQDN=apollo.antelab.eu NETDATA_FQDN=netdata.antelab.eu bash /tmp/hermes/scripts/install-caddy.sh
```

## Verify

```bash
curl -sI https://netdata.antelab.eu | head -3                                        # 302 → /oauth2/sign_in
curl -s -o /dev/null -w '%{http_code}\n' https://netdata.antelab.eu/api/v1/info      # 302, never 200
nc -vz -w3 netdata.antelab.eu 19999; nc -vz -w3 netdata.antelab.eu 4180              # both must fail
ssh ops@<ip> sudo healthcheck.sh                                                     # netdata login: oauth2-proxy active
```

In a browser: a test user reaches Netdata after the Google login; any other
Google account is blocked by Google ("Access blocked").

## Users
Managed in the Google Auth Platform test users list, shared with Hermes.
Removing someone blocks new logins immediately; an existing Netdata session
lasts up to 12h (`cookie_expire`). To cut all sessions now, delete
`OAUTH2_PROXY_COOKIE_SECRET` from `/etc/oauth2-proxy/netdata.env` and re-run
`install-oauth2-proxy.sh` (a new secret is generated).

## Upgrade oauth2-proxy
Bump `OAUTH2_PROXY_VERSION` and `OAUTH2_PROXY_SHA256` (the release's
`linux-amd64.tar.gz` digest) in `scripts/install-oauth2-proxy.sh` in a PR,
then `ship.sh`.

## Troubleshooting
- Script says oauth2-proxy not running → `journalctl -u oauth2-proxy-netdata -n 50`.
- Script says `HERMES_DASHBOARD_OIDC_CLIENT_ID missing` → finish Hermes Google
  OIDC first (`dashboard-domain.md`).
- Google `redirect_uri_mismatch` → the client must list exactly
  `https://netdata.antelab.eu/oauth2/callback`.
- Script says `netdata.antelab.eu not serving` → DNS not propagated or
  certificate not issued yet: `dig`, `journalctl -u caddy`, then re-run.
- Script says `Netdata answered without login; caddy stopped` → the Caddyfile
  lost `forward_auth`; fix it in the repo, then re-run `ship.sh`.
- 502 on netdata.antelab.eu → oauth2-proxy or Netdata down:
  `systemctl status oauth2-proxy-netdata netdata`.

## Rollback
Remove the `{$NETDATA_FQDN}` site from `config/caddy/Caddyfile` in a PR and
ship, then on the host:

```bash
ssh ops@<ip> 'sudo systemctl disable --now oauth2-proxy-netdata'
```
