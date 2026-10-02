# Local security dashboard

This is an opt-in native stack: Loki 3.7.8 (download pinned by SHA-256), a dedicated Alloy service, and a separate Grafana process on `127.0.0.1:3001`. It does not alter the existing Grafana service/database/password, Caddy/OAuth configuration, UFW rules/logging, Terraform, or cloud resources. No logs leave the host. The collector does not start until the pinned Loki binary and all private configs are installed.

## Install and access

Prerequisites: the reviewed Ubuntu 26.04 amd64 host, the normal bootstrap
(UFW, SSH, Fail2Ban, Caddy), and `curl`, `openssl`, `unzip`. On a fresh host,
install a missing prerequisite with `sudo apt-get install unzip openssl curl`
before running this optional installer. Ports 3001, 3100, 9096 and 12346 must
be unused except by this managed stack. No public DNS or firewall rule is needed.

To include installation at the end of the usual `ship.sh` run, explicitly set:

```bash
INSTALL_SECURITY_DASHBOARD=1 scripts/ship.sh ops@<host>
```

Or run the installer directly on the host after the repository files have been copied:

```bash
sudo bash /tmp/hermes/scripts/install-security-dashboard.sh
```

The installer refuses existing security-dashboard config/state/units unless its ownership marker is present. It keeps the Grafana admin password and signing secret in `/etc/hermes-security-dashboard/grafana.env` (`root:grafana`, mode 0600); it never prints or rotates them on re-run. Existing Grafana and Alloy packages are reused without requesting upgrades; absent packages come from Grafana's signed APT repository. Loki is pinned to 3.7.8 and its official release archive SHA-256 is checked. Inspect installer output and service status after running it.

No listener is public. From an authorized SSH client, keep this tunnel open and visit `http://127.0.0.1:3001`:

```bash
ssh -N -L 3001:127.0.0.1:3001 ops@<host>
```

Sign in as `admin`. Read the credential locally on the host only when needed; do not paste it into chat, issue trackers, or CI logs. The separate security Grafana does not share the existing Grafana database or admin credential.

```bash
sudo systemctl status hermes-security-loki hermes-security-alloy hermes-security-grafana
sudo journalctl -u hermes-security-loki -u hermes-security-alloy -u hermes-security-grafana --since today
curl -fsS http://127.0.0.1:3100/ready
curl -fsS http://127.0.0.1:3001/api/health
```

The three services are enabled at boot after successful configuration validation. Data and the separate Grafana SQLite database live under `/var/lib/hermes-security-dashboard/`; Loki retention is seven days. Do not delete that directory if the dashboard history/database should be retained.

## What is collected

Alloy tails only these sources, with constant `job` and `host` labels:

- **UFW**: kernel journal entries matching `_TRANSPORT=kernel SYSLOG_IDENTIFIER=kernel`, filtered to UFW `BLOCK`/`REJECT` records.
- **SSH**: separate journal readers matching `_COMM=sshd` and `_COMM=sshd-session`; the dashboard filters successful/failed authentication text.
- **Fail2Ban**: `/var/log/fail2ban.log` (root-owned, `adm`-readable on the host).
- **Caddy**: journal entries for `_SYSTEMD_UNIT=caddy.service`, filtered to JSON logger `http.log.access` and its named children (e.g. `http.log.access.log0`).

For Caddy, Alloy parses an allowlist and emits status, remote IP, method, host, URI **path**, and `user_id`. It does not store raw JSON, request headers, authorization/cookie values, query strings, or request bodies. Paths and user IDs can still be sensitive; restrict SSH/Grafana access and retention accordingly. SSH/Fail2Ban/UFW event lines can contain account names and IP addresses; those values are log content, never Loki labels. Loki labels stay low-cardinality (`job`, `host`; Loki may add its own fixed inferred `service_name`/`detected_level` metadata).

## Investigating incoming traffic

The `Host Security` dashboard defaults to 24 hours. In Grafana Explore or the dashboard logs panels, use:

```logql
{job="ufw"} |~ "UFW (BLOCK|REJECT)"
{job="ssh"} |~ "(Failed|Accepted) (password|publickey|keyboard-interactive)"
{job="fail2ban"} |~ "Ban|Unban|Found|already banned"
{job="caddy"} |~ "status=401|status=403|status=404"
```

To inspect source IPs in UFW message content, for example:

```logql
{job="ufw"} |~ "UFW BLOCK" | regexp `SRC=(?P<src_ip>\S+)`
```

This adds an extracted field for the query; it does not index the IP as a stream label. Follow an IP across sources by searching the corresponding log text, e.g. `{job=~"ssh|fail2ban|caddy"} |= "203.0.113.9"`.

### Important limits

- UFW is currently configured for **LOW, rate-limited** logging (3/minute, burst 10). The dashboard does not raise verbosity or alter firewall policy. It shows only logged UFW block/reject records, not every packet or connection. Its count is logged records, not unique attacks or a complete traffic census; rate limiting can hide additional attempts.
- Successful inbound connections are not all represented by UFW block records. SSH/Caddy show only their own logged events. No packet capture or broad system journal collection is enabled.
- The dashboard provides investigation views only. Alert delivery/webhooks are deliberately out of scope; no authorized new destination was supplied.

## Security boundary

This is private network exposure, **not isolation from other host software**.
Loki's loopback HTTP API has no authentication: any local user/process can query
or inject logs. The services therefore assume trusted local accounts/processes;
Grafana login protects the UI, not direct local Loki access. `ops` already has
sudo/Docker privileges. Do not use this deployment on a multi-tenant host, expose
Loki through a tunnel/reverse proxy to untrusted users, or treat it as tamper-proof
forensics. That would need a separately reviewed authenticated boundary/off-host
storage design.

The separate Grafana service reuses the package's `grafana` OS account. Its
config, database and admin credential are separate from the existing instance
(to avoid overwrites), but processes under that same UID can access both data
sets. Similarly the dedicated collector uses the package's `alloy` UID with
journal-reading groups. These are operationally separate instances, not strong
OS-level security isolation.

## Rollback and recovery

Stop and disable only these dedicated services:

```bash
sudo systemctl disable --now hermes-security-grafana hermes-security-alloy hermes-security-loki
```

Existing Grafana, Alloy, Caddy, Netdata and firewall services remain untouched.
Keep `/etc/hermes-security-dashboard` and `/var/lib/hermes-security-dashboard`
if you want to reinstall without losing credentials or history. Do not remove
the shared Grafana/Alloy packages. The existing backup covers `/etc`, but not
the new `/var/lib/hermes-security-dashboard` databases/history; provisioned
config and dashboards are reproducible from Git, while log history and UI
changes are not restore-tested. Local logs are not tamper-proof evidence and
are lost with the host. Seven-day compactor retention is asynchronous (including
a deletion delay), not a strict disk-size quota; monitor disk usage in Netdata.

## Verify in an isolated sandbox

The smoke test starts actual Loki, Alloy, and Grafana processes with temporary storage/config under `$TMPDIR`. It uses `systemd-journal-remote` to build an isolated test-only journal, retaining the production journal selectors and processing stages; only input paths, ports, storage and the test host label change. It verifies SSH daemon/session events, UFW filtering, Fail2Ban file ingestion, named Caddy access loggers, secret/query-string exclusion, low-cardinality labels, and every dashboard panel query through Grafana's real Loki datasource. It never reads production logs, writes production `/etc`, or starts system services.

```bash
python3 -m unittest discover -s tests -p 'test_security_dashboard*.py' -v
LOKI_BIN=/path/to/loki-linux-amd64 TMPDIR=/path/to/scratch python3 tests/security_dashboard_smoke.py
```

Without `LOKI_BIN`, the smoke test fetches the pinned official Loki asset and verifies its SHA-256. Alloy and Grafana native binaries are required. CI installs those binaries from Grafana's signed APT repository and runs this same smoke test without Terraform credentials.
