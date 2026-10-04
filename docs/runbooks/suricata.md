# Native passive Suricata

## Scope and privacy

Opt-in IDS on **eno1 only**, two AF_PACKET workers, bidirectional observation.
No IPS, NFQUEUE, packet forwarding, promiscuous mode, firewall changes, dashboard
changes, extra interfaces, production PCAPs, or paid rule sources. TLS/SSH remain
encrypted; no keys or decryption proxies are configured. HOME_NET is regenerated
from global addresses on eno1 as individual IPv4 /32 and IPv6 /128 hosts (not the
provider's entire subnet). Re-run the installer after host address changes.

EVE contains only alerts and 30-second totals statistics. Payload, printable
payload, packet dumps, HTTP/WebSocket bodies, tagged packets, application metadata
and top-level metadata are disabled. Full raw rules are not retained because
alert metadata is disabled. There are no HTTP, DNS, TLS, flow, file-store, fast,
or PCAP output loggers. Only the strict allowlist filter's `alerts.json` should
be read by the native root Wazuh agent; never ingest raw `eve.json`. Rules inspect
plaintext traffic in memory, but requests, URLs, bodies and DNS queries are not
retained. Alert signature messages/categories are rule authors' text, not proof
of a confirmed attack; local users in the service group can read private logs.

## Preconditions and deployment

Ubuntu native packages: `suricata`, `suricata-update`, `python3-yaml`, `logrotate`.
Use `/usr/bin/python3`, not a `/usr/local` Python lacking distro PyYAML.
From the infrastructure checkout:

```sh
bash scripts/install-suricata.sh --preflight --interface eno1
sudo bash scripts/install-suricata.sh --interface eno1
```

Preflight is read-only and refuses unmanaged packages, paths, units and symlinks.
`SURICATA_ROOT=/isolated/root` is supported **only for preflight**, never deployment.
An existing unrelated Suricata installation requires an explicit migration,
not automatic adoption. Package ownership is recorded in root-only
`/etc/hermes-suricata/package-managed` before apt. The installer temporarily
replaces policy-rc.d to suppress startup and restores the original hook on exit,
including its mode. Owned stock Suricata/updater units are disabled and masked.
A partial apt failure that introduced the binary retains ownership and masks the
stock service; a failure introducing no binary discards the package marker.
Inspect failures before retrying; do not remove markers to bypass checks.

`/etc/hermes-suricata` and `/usr/local/lib/hermes-suricata` are root-owned 0750,
and scripts/configs are not service-writable. `/var/lib/hermes-suricata` is
root:hermes-suricata 0750; only its `filter/` child is service-owned 0750 so the
filter can atomically replace `filter/filter.json`. Rules/update children are
root-owned. `/var/log/hermes-suricata` is service-owned 0750; logs use 0640.
Installer metadata repair failures are fatal. Capture uses only CAP_NET_RAW,
not NET_ADMIN, with runtime sockets in `/run/hermes-suricata`. Limits are 1 GiB,
150% CPU and nice 10; flow/stream/reassembly memcaps are 128/64/128 MiB.
If capture fails, inspect the actual error rather than adding privileges blindly.

## Renderer and validation

The installed distro `/etc/suricata/suricata.yaml` is the base, not a vendored copy.

```sh
/usr/bin/python3 scripts/suricata-config.py render \
  --base /etc/suricata/suricata.yaml --output /path/to/candidate.yaml --interface eno1
# Offline fixtures may add --addresses-json FILE (ip -j addr JSON).
/usr/bin/python3 -m unittest discover -s tests -p 'test_suricata*.py' -v
shellcheck scripts/install-suricata.sh scripts/update-suricata-rules.sh
suricata -T --init-errors-fatal -c /path/to/candidate.yaml \
  -S config/suricata/local.rules -l /private/validation/logs
/usr/bin/python3 tests/suricata_smoke.py --config /path/to/candidate.yaml \
  --rules config/suricata/local.rules --destination HOST_IPV4 \
  --alerts-output /private/validation/alerts.json
```

The smoke test creates a temporary offline PCAP, invokes Suricata `-r`, confirms
SID 9900001 consumes `HERMES_IDS_SMOKE_` without retaining the payload, then runs
the actual strict filter. It sends **zero network packets**. Its exported alert
can be used as a labeled Wazuh ingestion probe, not as evidence of live capture.
For runtime validation, inspect natural AF_PACKET capture/drop counters and
Wazuh's actual indexed alert records separately. Record firewall snapshots and
private Grafana health before/after installation; those must remain unchanged.

## Updates and bounded logs

`hermes-suricata-rules-update.timer` runs daily at 04:17 UTC with up to 30 minutes
jitter and persistent catch-up. A dedicated config selects only free ET Open;
additional data-dir sources are refused. Download/merge happens in staging with
no automatic reload. Every active rule must be `alert`; `pass`, `drop` and
`reject` fail closed. The candidate plus local rules is tested with Suricata
`-T --init-errors-fatal -S` before atomic promotion. Download, action-gate or
validation failure leaves the previous rules untouched. A blocking Unix-socket
reload checks the JSON return and restores the prior on-disk rules on failure;
inspect engine health before claiming the in-memory reload succeeded.

```sh
sudo systemctl start hermes-suricata-rules-update.service
sudo journalctl -u hermes-suricata-rules-update.service --since today
systemctl list-timers 'hermes-suricata-*'
sudo suricatasc -c dump-counters /run/hermes-suricata/suricata-command.socket
```

Suricata 8's native Rust `suricatasc` takes the socket as a positional argument;
legacy `-s` is not supported by Ubuntu's 8.0.3 build.

A separate five-minute timer invokes only `/etc/hermes-suricata/logrotate.conf`
with its own root-protected state file. Each raw log rotates at 50 MiB with seven
archives, compression and one delayed-compression generation. Before renaming,
logrotate records which services were active, stops the filter, then synchronously
stops the sensor. Only after the writer is closed does it publish a device/inode
quiescence witness. After renaming it starts only previously active services,
sensor first. This deliberately accepts a **brief capture gap at raw-log rotation**:
traffic during sensor stop/start is not observed. A socket reopen acknowledgement
is not proof that the old writer FD has closed; stopping is the simpler safe bound.
This is not lossless capture or an exhaustive recovery coordinator.

The root-protected pending marker is `/var/lib/hermes-suricata/rotation-active`;
it is not under `/run` (the sensor's RuntimeDirectory disappears on stop).
The persistent witness is `rotation.json` in the same root-owned 0750 parent,
readable but not writable by the filter. Neither file belongs in the writable
`filter/` child. **Deployment prerequisite:** the logrotate service's
`ReadWritePaths` must include `/var/lib/hermes-suricata`; otherwise its
`ProtectSystem=strict` sandbox prevents marker writes and rotation aborts. The reader resumes the checkpoint's device/inode from local,
numeric **uncompressed** EVE archives, drains unread records, and checkpoints the
old inode until switching. EOF alone never retires a rotated FD: without the
matching witness it retains that FD, accepts late appends, and warns in the
journal. New-inode ingestion then waits for coordinated recovery. Manual rename,
copytruncate, compressed/deleted checkpoint archives, and concurrent external
rotators are unsupported; do not claim those paths are lossless.

A subsequent rotation refuses to compress the preceding generation unless the
filter checkpoint has moved onto current EVE. A failed stop, stale pending marker,
or prior-inode backlog aborts rotation visibly; services may remain stopped.
Inspect the logrotate and service journals plus `rotation-active`, `rotation.json`,
`filter/filter.json`, and the actual EVE device/inodes before recovery. Preserve
all archives. If rotation never renamed EVE, remove its stale witness **before**
restarting the sensor; a reopened old inode must not be certified quiescent.
For a completed rename, retain the witness while restarting previously active
services and draining the archive. Clear the pending marker only after resolving
the failure; do not bypass a backlog by deleting the filter checkpoint. Resume
the timer after the filter checkpoints current EVE. These are targeted recovery
checks, not an exhaustive disaster-recovery procedure.

This is a polling bound, not a strict instantaneous disk cap; heavy output can
exceed 50 MiB between checks. The filter independently bounds `alerts.json`.
Unrelated global logrotate failures are not modified or repaired.

## Stop / recovery

```sh
sudo systemctl stop hermes-suricata-alerts.service hermes-suricata.service
sudo systemctl disable --now hermes-suricata-rules-update.timer hermes-suricata-logrotate.timer
```

Keep stock units masked while this deployment owns the package. Stopping IDS
has no firewall/routing side effect. Retain restricted logs/ownership records
for investigation; removal/adoption and retention changes require a separate
review. Installation readiness is not ingestion verification.
