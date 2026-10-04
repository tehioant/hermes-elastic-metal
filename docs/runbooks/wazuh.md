# Private Wazuh single-node + native host agent

Wazuh 4.14.8 runs as three official, tag-and-digest-pinned containers. The native
4.14.8-1 agent audits the actual host, not the manager container. This is opt-in;
the repository does not deploy Wazuh merely because it is checked out.

## Install / inspect

Run from the authoritative infrastructure checkout, as root on the intended host:

```sh
sudo bash scripts/install-wazuh.sh --preflight
sudo bash scripts/install-wazuh.sh --install
```

The shell wrapper deliberately uses `/usr/bin/python3` (APT's interpreter), not a
user Python environment. Prerequisites: systemd, Docker with Compose, OpenSSL,
GnuPG, Python 3 with `python3-bcrypt`, four logical CPUs, 8 GiB available RAM,
50 GiB free Docker storage, and `vm.max_map_count >= 262144`. The installer does
not change sysctls. On this deployment host the dependencies already exist.

`--preflight` performs read-only ownership/resource/port checks; default invocation
also performs only preflight. `--install` is required to install packages or start
services. Offline review without installing anything:

```sh
fixture=$(mktemp -d "${TMPDIR:?}/wazuh-render.XXXXXX")
bash scripts/install-wazuh.sh --render-only --root "$fixture"
docker compose -f "$fixture/etc/hermes-wazuh/compose.yml" config --quiet
/usr/bin/python3 -m unittest discover -s tests -p 'test_wazuh*.py' -v
```

Fixture roots cannot deploy. The renderer generates real CA/leaf certificates,
bcrypt hashes and nondefault passwords; fixture files are secrets too. Do not
copy generated configuration into Git or print unrestricted `compose config`.

## Exposure, resources, ownership

All containers use host networking, with **no published ports, no new Docker
networks and no firewall commands**. Enabled listeners are explicitly IPv4
loopback: secure agent TCP 1514, API HTTPS 55000, indexer HTTPS 9200 / transport
9300, dashboard HTTPS 8443. Enrollment 1515 and clustering 1516 are disabled;
there is no syslog listener. Active response and remote commands are disabled
on both manager and agent. No Docker socket, host root, host journal or other
host audit mounts are granted to containers.

Memory caps are indexer 3 GiB (1 GiB JVM heap), manager 2 GiB, dashboard 1 GiB,
native agent 768 MiB. Container PID limits and Docker log rotation are explicit.
Existing services, firewall rules and Docker chains are not configured here.

`/etc/hermes-wazuh/.managed` is the ownership marker. Unmarked targets, existing
agent installations and existing Wazuh containers/volumes are refused, not
adopted. An installer lock prevents concurrent live installs. The marker is
persisted before APT, so a partial owned installation can be retried. Credentials,
CA/key and offline `client.keys` are reused on retry, never re-enrolled. Losing
these identity files requires recovery from a secure backup, not marker removal.
Repeated installation restarts only the owned Wazuh stack and native agent.

An existing `policy-rc.d` is preserved by rename (including symlink/inode/mode),
replaced temporarily with exit 101 while APT runs, then restored even on ordinary
failure. If the process is killed or the host loses power mid-install, inspect
`/usr/sbin/policy-rc.d.hermes-wazuh-backup` and restore the original hook before
retrying; do not overwrite that backup. The agent is version-pinned and held.
The signing key is accepted only with full primary fingerprint
`0DCFCA5547B19D2A6099506096B3EE5F29111145`, independently corroborated in
[Wazuh's maintained Ansible deployment tests](https://github.com/wazuh/wazuh/issues/21352)
and the official Wazuh Puppet repository configuration.

Secrets live in root-traversable `/etc/hermes-wazuh` (0700): `credentials.json`,
`client.keys`, generated Compose configuration, bcrypt user database, dashboard
API configuration and TLS keys. CA private key and credentials are 0600. Leaf
keys/config used by unprivileged container users are root:root 0640 and mounted
read-only; indexer and dashboard get supplemental group 0, never the host directory.
Only indexer `admin` and `kibanaserver` users are enabled; demo users are absent.
Official manager initialization sets the generated `wazuh-wui` password and
randomizes the unused default `wazuh` account before starting the API.

TLS certificates cover `localhost` and `127.0.0.1`, plus the official indexer/admin
DNs. Indexer and dashboard readiness validate the generated CA. The upstream
manager API uses its private self-signed certificate; only its localhost
readiness probe skips certificate verification. Do not expose that API remotely.
The installer authenticates indexer/API and validates dashboard TLS before
starting the native agent. Root and Docker access can read container environment
credentials; loopback is not isolation from other privileged local processes.

## Data / privacy

The agent collects:

- SSH journal records only, selected by `_COMM=^sshd(-session)?$`, missing fields
  rejected; `/var/log/fail2ban.log`; and sanitized Suricata alert metadata from
  `/var/log/hermes-suricata/alerts.json` (not raw `eve.json`).
- Package, OS and hardware inventory. Process, port and network inventory are
  disabled. Vulnerability detection downloads the Wazuh CTI feed; this outbound
  feed access is expected, not cloud event shipping.
- Realtime hash/metadata FIM of `/etc/ssh`, `/etc/systemd/system`, `/etc/ufw`, and
  `/var/lib/hermes-wazuh/fim-test`. No file diffs/content, home directories,
  Hermes credentials or application access logs are collected.

Manager archive-all logging, rootcheck, SCA, active response, custom shell
commands, cloud integrations and manager-container inventory are disabled.
Dashboard search usage telemetry is explicitly disabled. SSH usernames, IPs,
package inventory and security-event metadata remain sensitive and local.

The daily UTC retention timer removes only valid `wazuh-alerts-4.x-YYYY.MM.DD`
indices strictly older than 30 days and aged compressed manager alert/archive
files. It never deletes security/system indices, current alert logs or current
vulnerability state. Docker console logs are bounded separately. Indexer disk
watermarks remain enabled. Retention is age-bounded, not a hard total disk quota;
monitor named-volume growth. No backup jobs are modified.

## Verification and access

```sh
sudo systemctl status hermes-wazuh wazuh-agent hermes-wazuh-retention.timer
sudo docker compose -p hermes-wazuh -f /etc/hermes-wazuh/compose.yml ps
sudo ss -lntp
sudo /var/ossec/bin/wazuh-logcollector -t
sudo /var/ossec/bin/wazuh-syscheckd -t
sudo /var/ossec/bin/wazuh-control status
sudo systemctl start hermes-wazuh-retention.service
```

Compare firewall and listener baselines before/after deployment externally. Do
not claim ingestion from service status: verify native agent 001 is connected,
change a harmless FIM test file, and confirm a decoded alert reaches the indexer.
Confirm sanitized Suricata alerts decode under the built-in Suricata rules.
Installer config tests do not replace these end-to-end deployment checks.

SSH-tunnel the dashboard without opening any ingress:

```sh
ssh -N -L 8443:127.0.0.1:8443 emeta-01
```

Open `https://localhost:8443`. Trust the generated public CA on the operator
machine or verify its fingerprint over SSH. Obtain `admin`'s `indexer` credential
from the root-only credentials file using a secure local session, not chat.
Do not publish the Compose config, Docker inspect environment or API tokens.

## Recovery / stop

Inspect locally with `journalctl -u hermes-wazuh` and Compose logs; these can
contain sensitive security events. After fixing the checked-in configuration,
rerun the installer; do not replace existing CA/passwords/client keys.

```sh
sudo systemctl disable --now wazuh-agent.service hermes-wazuh-retention.timer
sudo systemctl disable --now hermes-wazuh.service
```

Stopping preserves named volumes, identity and installed agent. Do not use
`docker compose down -v` unless deletion of all Wazuh data is specifically
approved. Restore root-only configuration/CA/client keys and named volumes as a
consistent set; the CA has a ten-year lifetime, leaves 825 days, and certificates
must be renewed deliberately before expiry (the installer never silently rotates
an existing identity).

## Upstream sources

- [Official Docker single-node deployment](https://documentation.wazuh.com/current/deployment-options/docker/wazuh-container.html)
- [Pinned official 4.14.8 configuration](https://github.com/wazuh/wazuh-docker/tree/v4.14.8/single-node)
- [Native Linux agent installation](https://documentation.wazuh.com/current/installation-guide/wazuh-agent/wazuh-agent-package-linux.html)
- [Journal filters](https://documentation.wazuh.com/current/user-manual/reference/ossec-conf/localfile.html#filter)
