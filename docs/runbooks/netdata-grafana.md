# Netdata metrics in private Grafana

This is a separate, opt-in local metrics bridge: Netdata's existing HTTP API is scraped by a dedicated native Prometheus process; the existing **security Grafana** provisions a Prometheus datasource and the `Netdata Host` dashboard. It does not use Netdata Cloud, cloud tokens, the Netdata Grafana plugin, a second collector/agent, remote write, or public ports. Prometheus listens only on `127.0.0.1:9090`; existing Grafana remains at `127.0.0.1:3001`. No firewall, Caddy, credential, or security-log configuration changes are made, and the existing Grafana database is preserved (provisioning adds the new datasource/dashboard records).

## Install

Prerequisites: existing managed Hermes security dashboard running and healthy on port 3001; Netdata's local API on `127.0.0.1:19999`; Ubuntu's Prometheus package (installer obtains it only if `prometheus` is missing). Run from a checked-out repository on the host:

```bash
sudo bash scripts/install-netdata-grafana.sh --preflight
sudo bash scripts/install-netdata-grafana.sh
```

The preflight is read-only. The installer requires the security-dashboard ownership marker, rejects unmanaged integration files and an occupied port 9090, validates Netdata's expected gauge endpoint and Prometheus config, and uses `apt-get install --no-install-recommends` under `policy-rc.d` so package services do not auto-start and Node Exporter is not pulled in. When it newly installs the Prometheus package, it also disables and administratively masks the stock `prometheus.service`: `policy-rc.d` prevents immediate starts but does not prevent the package from enabling that service at boot. Pre-existing package installations are not changed. It writes root-owned config readable only by the Prometheus/Grafana service groups, enables only `hermes-netdata-prometheus.service`, and restarts the private security Grafana only when its datasource/dashboard provisioning files change. It does not rotate Grafana credentials. Package installation, service enablement, and the intentional Grafana restart happen only when this opt-in installer is explicitly run.

For a new package installation, a root-only `prometheus-package-managed` ownership record is written under `/var/lib/hermes-netdata-metrics` before invoking apt. If apt fails after introducing the binary, the stock service is still disabled/masked before the installer reports failure. Retries consult this record rather than treating the binary's presence as proof of a pre-existing installation, and repeat stock-service protection. If apt fails without introducing the binary, the attempt record is discarded so a later independent installation is not adopted. Invalid records are rejected; installations without this record are left alone. Resolve the reported package-manager error before retrying the installer.

Verify locally:

```bash
systemctl status hermes-netdata-prometheus.service
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsSG http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=netdata_system_cpu_percentage_average{dimension="idle"}'
```

Access Grafana only via the existing SSH tunnel to port 3001; find **Netdata Host** in the **Netdata** folder. Existing default datasource and security dashboard are unchanged.

## Collection and interpretation

Prometheus polls `/api/v1/allmetrics` every 30 seconds with a 10-second timeout. It selects Netdata's `source=average` **gauges**, omits timestamps, and gives this scrape a unique `server=hermes-netdata-grafana` identity. The allowlist is system CPU, RAM and load; root filesystem (`disk_space./`); and `eno1`/`tailscale0` network charts. Responses are limited to 1,000 samples and 10 MB. The live Netdata v2.12.0 API returns one HELP/TYPE metadata pair for each selected metric family (the `help=no`/`types=no` parameters did not suppress these in a direct probe); the restricted filter avoids broad/repeated metric-family output. No `rate()`/`irate()` is applied: these values are already rates/averages, not monotonically increasing Prometheus counters. The Prometheus TSDB uses seven-day and 1 GB persisted-block retention; pruning is asynchronous and the WAL/head data may add space beyond the block limit, so this is not a strict total-directory quota; unit resource limits are 50% CPU and 512 MB memory.

Dashboard calculations use the actual Netdata dimensions: CPU is 100 minus idle percentage; RAM used percentage divides `used` by `free+used+cached+buffers`; root disk percentage is `used/(used+avail)` and does not count reserved-for-root as used; network sent is displayed as an absolute value because Netdata reports sent throughput as a negative kilobits/sec gauge, scaled to bits/sec. Load is the 1/5/15-minute gauge. Prometheus history begins when this bridge is installed; it does not backfill Netdata's retained chart history.

## Smoke and tests

The reusable smoke starts real Prometheus and Grafana processes with temporary config/storage on random loopback ports, reads the local Netdata info endpoint, scrapes the configured Netdata API read-only, checks the scrape target is healthy, and loads the exact dashboard expressions from the provisioned JSON. It then verifies all six panel queries through Grafana's real Prometheus datasource and checks datasource health. It does not install packages, start system services, or modify the existing Grafana instance. The Grafana native binary is required; supply the Prometheus binary path if it is not on `PATH`:

```bash
PROMETHEUS_BIN=/path/to/prometheus python3 tests/netdata_grafana_smoke.py
```

The smoke requires a reachable real Netdata endpoint at `127.0.0.1:19999`; set `NETDATA_URL` for an alternate endpoint. CI checks configuration and installer guards with unit tests plus `promtool`; CI does not claim a real-Netdata smoke because hosted runners have no provisioned Netdata instance.

```bash
python3 -m unittest tests.test_netdata_grafana -v
promtool check config config/netdata-grafana/prometheus.yml
shellcheck scripts/install-netdata-grafana.sh
```

## Security and recovery boundary

The unauthenticated Prometheus query API is loopback-only and trusts local host processes, just like the existing Loki API. Grafana login does not restrict direct local API access. The service reuses the package's Prometheus UID; this is operational separation, not multi-tenant isolation. Do not forward port 9090 to untrusted clients. The new TSDB is not covered by the existing restore-tested backup; it can be rebuilt from fresh scrapes but lost history cannot be restored or backfilled. Config/provisioning is reproducible from Git.

## Rollback

Stop/disable only the new unit with `sudo systemctl disable --now hermes-netdata-prometheus.service`. Keep `/var/lib/hermes-netdata-metrics` if the local TSDB is needed. Remove the Prometheus datasource provisioning file, dashboard provider file and dashboard JSON from `/etc/hermes-security-dashboard/` only after reviewing them; restart `hermes-security-grafana.service` to remove the provisioned dashboard/datasource. Do not delete or alter the security dashboard's shared Grafana database/config or its Loki/Alloy state.
