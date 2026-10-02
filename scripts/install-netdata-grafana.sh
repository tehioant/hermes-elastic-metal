#!/usr/bin/env bash
# Opt-in bridge from local Netdata metrics to the existing private security Grafana.
set -Eeuo pipefail
ROOT="${NETDATA_GRAFANA_ROOT:-/}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly STATE_DIR="${ROOT%/}/var/lib/hermes-netdata-metrics"
readonly ETC_DIR="${ROOT%/}/etc/hermes-netdata-metrics"
readonly UNIT="${ROOT%/}/etc/systemd/system/hermes-netdata-prometheus.service"
readonly MARKER="${STATE_DIR}/managed-by-hermes"
readonly SECURITY_MARKER="${ROOT%/}/var/lib/hermes-security-dashboard/managed-by-hermes"
readonly GRAFANA_DATASOURCE="${ROOT%/}/etc/hermes-security-dashboard/provisioning/datasources/netdata-prometheus.yaml"
readonly GRAFANA_PROVIDER="${ROOT%/}/etc/hermes-security-dashboard/provisioning/dashboards/netdata.yaml"
readonly GRAFANA_DASHBOARD_DIR="${ROOT%/}/etc/hermes-security-dashboard/netdata-dashboards"
readonly GRAFANA_DASHBOARD="${GRAFANA_DASHBOARD_DIR}/netdata.json"
POLICY_RC_CREATED=0
cleanup_policy() {
  if (( POLICY_RC_CREATED )); then rm -f /usr/sbin/policy-rc.d; fi
}
trap cleanup_policy EXIT

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

disable_new_stock_prometheus() {
  systemctl disable --now prometheus.service || fail "failed to disable stock prometheus.service"
  systemctl mask prometheus.service || fail "failed to mask stock prometheus.service"
}

assert_safe_to_manage() {
  [[ -f "${SECURITY_MARKER}" ]] \
    && [[ "$(<"${SECURITY_MARKER}")" == hermes-security-dashboard-managed-v1 ]] \
    || fail "existing managed security-dashboard marker required; install that stack first"
  if [[ -e "${MARKER}" ]]; then
    [[ "$(<"${MARKER}")" == netdata-prometheus-managed-v1 ]] \
      || fail "Netdata metrics ownership marker is invalid; inspect before adoption"
    return 0
  fi
  for path in "${ETC_DIR}" "${UNIT}" "${STATE_DIR}" \
    "${GRAFANA_DATASOURCE}" "${GRAFANA_PROVIDER}" "${GRAFANA_DASHBOARD_DIR}" "${GRAFANA_DASHBOARD}"; do
    [[ ! -e "${path}" ]] || fail "existing Netdata metrics state is unmanaged; inspect before adoption"
  done
}

main() {
  if [[ "${1:-}" == --preflight ]]; then assert_safe_to_manage; return; fi
  [[ "${ROOT}" == / ]] || fail "custom root is supported only for --preflight"
  [[ "${EUID}" -eq 0 ]] || fail "must run as root"
  assert_safe_to_manage
  for cmd in curl ss systemctl apt-get; do command -v "${cmd}" >/dev/null || fail "missing prerequisite ${cmd}"; done
  systemctl is-active --quiet hermes-security-grafana.service \
    || fail "hermes-security-grafana.service must be active before installing this opt-in integration"
  curl -fsS http://127.0.0.1:3001/api/health >/dev/null \
    || fail "private Grafana at 127.0.0.1:3001 is not healthy"
  curl -fsS 'http://127.0.0.1:19999/api/v1/allmetrics?format=prometheus&source=average&timestamps=no&names=no&filter=system.cpu%20system.ram%20system.load%20disk_space./%20net.eno1%20net.tailscale0&server=hermes-netdata-grafana' \
    | grep -q '^netdata_system_cpu_percentage_average' \
    || fail "Netdata average metrics endpoint is unavailable or missing expected CPU gauge"
  if [[ -z "$(ss -H -ltn '( sport = :9090 )')" ]] || ! systemctl is-active --quiet hermes-netdata-prometheus.service; then
    [[ -z "$(ss -H -ltn '( sport = :9090 )')" ]] || fail "port 9090 is already in use; inspect before installing"
  fi
  install -d -o root -g root -m 0755 "${STATE_DIR}" "${ETC_DIR}"
  printf '%s\n' netdata-prometheus-managed-v1 > "${MARKER}"
  chmod 0600 "${MARKER}"
  if ! command -v prometheus >/dev/null 2>&1; then
    policy=/usr/sbin/policy-rc.d
    [[ ! -e "${policy}" ]] || fail "${policy} exists; inspect before installing packages"
    printf '%s\n' '#!/bin/sh' 'exit 101' > "${policy}"
    chmod 0755 "${policy}"
    POLICY_RC_CREATED=1
    status=0
    apt-get install -y --no-install-recommends prometheus || status=$?
    rm -f "${policy}"
    POLICY_RC_CREATED=0
    (( status == 0 )) || fail "failed to install Prometheus package (exit ${status})"
    disable_new_stock_prometheus
  fi
  id prometheus >/dev/null 2>&1 || fail "Prometheus package must provide the prometheus service account"
  install -d -o prometheus -g prometheus -m 0750 "${STATE_DIR}/prometheus"
  install -d -o root -g prometheus -m 0750 "${ROOT%/}/etc/hermes-netdata-metrics"
  prom_changed=0
  grafana_changed=0
  if ! cmp -s "${REPO_DIR}/config/netdata-grafana/prometheus.yml" "${ETC_DIR}/prometheus.yml"; then prom_changed=1; fi
  install -o root -g prometheus -m 0640 "${REPO_DIR}/config/netdata-grafana/prometheus.yml" "${ETC_DIR}/prometheus.yml"
  install -d -o root -g grafana -m 0750 \
    /etc/hermes-security-dashboard/provisioning/datasources \
    /etc/hermes-security-dashboard/provisioning/dashboards \
    /etc/hermes-security-dashboard/netdata-dashboards
  for pair in \
    "${REPO_DIR}/config/netdata-grafana/provisioning/datasources/prometheus.yaml:/etc/hermes-security-dashboard/provisioning/datasources/netdata-prometheus.yaml" \
    "${REPO_DIR}/config/netdata-grafana/provisioning/dashboards/netdata.yaml:/etc/hermes-security-dashboard/provisioning/dashboards/netdata.yaml" \
    "${REPO_DIR}/config/netdata-grafana/dashboard.json:/etc/hermes-security-dashboard/netdata-dashboards/netdata.json"; do
    src=${pair%%:*}; dst=${pair#*:}
    if ! cmp -s "${src}" "${dst}"; then grafana_changed=1; fi
    install -o root -g grafana -m 0640 "${src}" "${dst}"
  done
  unit_changed=0
  if ! cmp -s "${REPO_DIR}/systemd/hermes-netdata-prometheus.service" "${UNIT}"; then unit_changed=1; fi
  install -o root -g root -m 0644 "${REPO_DIR}/systemd/hermes-netdata-prometheus.service" "${UNIT}"
  command -v promtool >/dev/null 2>&1 || fail "Prometheus package must provide promtool for config validation"
  promtool check config "${ETC_DIR}/prometheus.yml" >/dev/null
  systemctl daemon-reload
  systemctl enable hermes-netdata-prometheus.service >/dev/null
  if systemctl is-active --quiet hermes-netdata-prometheus.service; then
    if (( prom_changed || unit_changed )); then systemctl restart hermes-netdata-prometheus.service; fi
  else
    systemctl start hermes-netdata-prometheus.service
  fi
  curl -fsS --retry 20 --retry-delay 1 --retry-connrefused http://127.0.0.1:9090/-/ready >/dev/null
  if (( grafana_changed )); then systemctl restart hermes-security-grafana.service; fi
  systemctl is-active --quiet hermes-netdata-prometheus.service || fail "Prometheus unit inactive"
  listener="$(ss -H -ltn '( sport = :9090 )')"
  [[ -n "${listener}" ]] || fail "Prometheus port 9090 is not listening"
  while read -r _ _ _ address _; do
    [[ "${address}" == 127.0.0.1:9090 ]] || fail "Prometheus listener is not confined to loopback"
  done <<< "${listener}"
  printf 'Netdata metrics ready: Prometheus 127.0.0.1:9090; Grafana dashboard folder Netdata. No public listener or firewall changes.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
