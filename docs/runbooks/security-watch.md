# Server Watch — inbound network metadata

An opt-in watch view in the existing private Grafana. It records externally
observable TCP SYN attempts, first-observed UDP flows, and ICMP echo requests on
one explicitly selected public Ethernet interface, including IPv4 and IPv6.
The map and event feed show source IP, approximate country/city, ASN/provider,
destination port/protocol, and observation time. This is not an attribution of
people or proof that a connection was malicious or successfully established.

## Scope and privacy

- Capture is read-only, non-promiscuous and pre-firewall. It does not change UFW,
  Fail2Ban, SSH, Caddy or listening ports. A capture event has an **unknown**
  allowed/blocked outcome. Use the linked Host Security dashboard for firewall,
  SSH, Fail2Ban and application evidence; existing firewall logs are rate-limited.
- Only the selected physical interface is covered. Loopback, Docker traffic and
  decrypted VPN interfaces are not collected. Packets discarded upstream or
  before the packet socket are not observable here.
- TCP SYN retransmissions and repeated UDP/ICMP tuples collapse within 30 seconds.
  UDP first-observed traffic may be a response to an outbound request, not a
  remote-initiated connection. Flow counts are not packet or attacker counts.
- Only canonical header metadata is retained: no packet dumps, payloads, HTTP
  headers, cookies or request bodies. IPv6 extension parsing and the 256-byte
  capture snapshot are bounded. Unsupported/truncated headers are not invented.
- Default output is capped at 200 events/second. Collector health exposes kernel
  drops, output-limit losses and deduplication; coverage is broader than UFW
  logging but **not exhaustive**, particularly during a flood. Absence of data
  is not evidence that the host was not contacted.
- Alloy emits only `job` and `host` stream labels; Loki may add the fixed
  `service_name=security-watch` and `detected_level=unknown` defaults. IPs, ports
  and coordinates remain log fields, not high-cardinality indexed labels. Logs
  share the existing seven-day Loki retention, starting at installation; there
  is no historical backfill.
- The source-IP textbox accepts RE2 regular expressions. Use `.*` for all
  addresses or `1\.1\.1\.1` for an exact IPv4 match. The query uses a LogQL
  raw string so regex backslashes are preserved; backticks are not supported
  in this IP-filter textbox.

## Geolocation

Lookups use local [DB-IP City Lite](https://db-ip.com/db/download/ip-to-city-lite)
and [ASN Lite](https://db-ip.com/db/download/ip-to-asn-lite) MMDB snapshots. There
is no paid subscription, account or per-IP online lookup. The dashboard links to
[DB-IP](https://db-ip.com) for **CC BY 4.0 attribution**. Locations are approximate
network allocations, and may represent a VPN, proxy or hosting provider, not the
actual sender. Unknown/private sources remain in the feed and totals but cannot
be plotted on the map. Browser map tiles may be fetched from the map provider;
source IPs are not submitted to a geolocation API.

The monthly updater downloads both free databases, checks size budgets and MMDB
metadata, and records SHA-256 integrity hashes. Failed candidates preserve the
previous valid data. The persistent systemd timer runs on the third of each month
at 04:10 UTC, with up to two hours of randomized delay. An unavailable provider
must not trigger a paid fallback. DB-IP's published SHA1 sums describe the
**decompressed MMDB**, not its downloaded gzip archive.

## Install only after approval

Requires the existing managed private security dashboard. Review the branch/PR
first, then use the repository installer; do not run it simply to test code.
Discover the public Ethernet interface with `ip -brief address` (this host uses
`eno1`). The interface must be chosen explicitly:

```bash
sudo bash scripts/install-security-watch.sh --preflight --interface eno1
sudo bash scripts/install-security-watch.sh --interface eno1
```

The collector runs as `hermes-watch` with only `CAP_NET_RAW`, a 256 MiB memory
limit and 30% of one CPU. It opens no TCP/UDP service port. Dependencies are native
distro libraries, `python3-maxminddb` and libpcap. The existing Alloy gains one
unit-specific journal reader; the new dashboard is provisioned separately from
existing dashboards. Ownership guards reject unmanaged targets and unexpected
Alloy edits rather than silently overwriting them.

Use the existing SSH tunnel to `http://127.0.0.1:3001/d/server-watch`; Grafana,
Loki, Netdata and the Prometheus bridge keep their previous private bindings.
After maintenance that reinstalls the base security stack's Alloy configuration,
rerun the watch installer deliberately so its reader is restored, rather than
assuming the base installer preserved the optional extension.

## Verify and operate

```bash
systemctl status hermes-security-watch.service
systemctl status hermes-security-watch-geoip-update.timer
systemctl list-timers hermes-security-watch-geoip-update.timer
sudo journalctl -u hermes-security-watch.service -n 10 --no-pager
```

Check the heartbeat, drop/output-loss counters, geography and recent events in
Server Watch, and confirm the original Host Security and Netdata dashboards still
work. A running service alone is not proof of ingestion. Inspect the updater's
journal and manifest if geolocation is stale; its monthly failure leaves the old
snapshot available. To stop recording without changing network policy:

```bash
sudo systemctl disable --now hermes-security-watch.service hermes-security-watch-geoip-update.timer
```

Retained Loki data expires under the existing retention policy. Do not delete
shared Loki/Grafana/Alloy state to remove this optional dashboard.

## Repeatable verification (not deployment)

Run unit tests and the actual packet smoke inside an isolated network namespace.
The packet smoke refuses PID 1's network namespace and does not create interfaces
or listeners in the host network. Its test packets and apparent source IPs are
explicit fixtures, not real probes against the public server.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/update-security-watch-geoip.py --directory "$TMPDIR/watch-geoip"
sudo --preserve-env=TMPDIR unshare -n -- /usr/bin/python3 tests/security_watch_capture_smoke.py --allow-isolated-root --city-db "$TMPDIR/watch-geoip/city.mmdb" --asn-db "$TMPDIR/watch-geoip/asn.mmdb" --events-output "$TMPDIR/watch-events.jsonl"
SECURITY_WATCH_EVENTS="$TMPDIR/watch-events.jsonl" python3 tests/security_dashboard_smoke.py
```

The final smoke builds an isolated systemd journal from capture output, runs the
production Alloy selectors/allowlist and real Loki/Grafana processes on temporary
loopback ports, checks privacy/labels and executes every provisioned target via
Grafana's actual datasource. The existing security-only smoke also remains
available without `SECURITY_WATCH_EVENTS`. CI runs these independently of
Terraform credentials and paid provider resources.
