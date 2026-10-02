#!/usr/bin/env bash
# Install an isolated, loopback-only Loki/Alloy/Grafana security dashboard.
set -Eeuo pipefail

readonly LOKI_VERSION=3.7.8
readonly LOKI_SHA256=62aea42c9cba52cd1642b3666ab37019a0ce4c24ab50b07e85dccc8d812f7d61
readonly LOKI_URL="https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/loki-linux-amd64.zip"
readonly ROOT="${SECURITY_DASHBOARD_ROOT:-/}"
readonly STATE_DIR="${ROOT%/}/var/lib/hermes-security-dashboard"
readonly ETC_DIR="${ROOT%/}/etc/hermes-security-dashboard"
readonly UNIT_DIR="${ROOT%/}/etc/systemd/system"
readonly MARKER="${STATE_DIR}/managed-by-hermes"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly CONFIG_DIR="${REPO_DIR}/config/security-dashboard"
POLICY_RC_CREATED=0
cleanup_policy() {
  if (( POLICY_RC_CREATED )); then rm -f /usr/sbin/policy-rc.d; fi
}
trap cleanup_policy EXIT

log() { printf '🔐 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

assert_safe_to_manage() {
  local indicator
  if [[ -f "${MARKER}" ]]; then
    [[ "$(<"${MARKER}")" == "hermes-security-dashboard-managed-v1" ]] \
      || fail "security-dashboard ownership marker is invalid; inspect before adoption"
    return 0
  fi
  for indicator in \
    "${ROOT%/}/usr/local/bin/hermes-security-loki" \
    "${ETC_DIR}" \
    "${UNIT_DIR}/hermes-security-loki.service" \
    "${UNIT_DIR}/hermes-security-alloy.service" \
    "${UNIT_DIR}/hermes-security-grafana.service" \
    "${STATE_DIR}"; do
    [[ ! -e "${indicator}" ]] || fail "existing security-dashboard state is unmanaged; inspect it before adoption"
  done
}

install_apt_repository() {
  local keyring=/etc/apt/keyrings/grafana.asc source=/etc/apt/sources.list.d/grafana.list
  install -d -m 0755 /etc/apt/keyrings
  if [[ ! -e "${keyring}" ]]; then
    curl -fsSL https://apt.grafana.com/gpg-full.key -o "${keyring}"
    chmod 0644 "${keyring}"
  fi
  local expected='deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main'
  if [[ -e "${source}" ]]; then
    grep -Fxq "${expected}" "${source}" || fail "unexpected ${source}; inspect before continuing"
  else
    printf '%s\n' "${expected}" > "${source}"
    chmod 0644 "${source}"
  fi
  apt-get update
}

install_package_without_autostart() {
  local package="$1" status=0 policy=/usr/sbin/policy-rc.d
  [[ ! -e "${policy}" ]] || fail "${policy} exists; inspect before installing packages to avoid starting unmanaged services"
  printf '%s\n' '#!/bin/sh' 'exit 101' > "${policy}"
  chmod 0755 "${policy}"
  POLICY_RC_CREATED=1
  apt-get install -y "${package}" || status=$?
  rm -f "${policy}"
  POLICY_RC_CREATED=0
  (( status == 0 )) || fail "failed to install package ${package} (exit ${status})"
}

ensure_packages() {
  local need_repo=0
  command -v alloy >/dev/null 2>&1 || need_repo=1
  command -v grafana-server >/dev/null 2>&1 || need_repo=1
  if (( need_repo )); then
    install_apt_repository
    command -v alloy >/dev/null 2>&1 || install_package_without_autostart alloy
    command -v grafana-server >/dev/null 2>&1 || install_package_without_autostart grafana
  fi
  id alloy >/dev/null 2>&1 || fail "Alloy package must provide its unprivileged alloy user"
  id grafana >/dev/null 2>&1 || fail "Grafana package must provide its unprivileged grafana user"
}

install_if_changed() {
  local src="$1" dst="$2" mode="$3" group="$4"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    chown "root:${group}" "${dst}" || fail "failed to set ownership on ${dst}"
    chmod "${mode}" "${dst}" || fail "failed to set mode on ${dst}"
    return 1
  fi
  install -o root -g "${group}" -m "${mode}" -D "${src}" "${dst}" \
    || fail "failed to install ${dst}"
  return 0
}

install_loki_binary() {
  local marker="${STATE_DIR}/loki-${LOKI_VERSION}.sha256" tempdir
  if [[ -x /usr/local/bin/hermes-security-loki ]] && [[ -f "${marker}" ]] && [[ "$(<"${marker}")" == "${LOKI_SHA256}" ]]; then
    return 1
  fi
  tempdir="$(mktemp -d "${STATE_DIR}/download.XXXXXX")"
  if ! curl -fsSL "${LOKI_URL}" -o "${tempdir}/loki.zip"; then rm -rf "${tempdir}"; fail "failed to download Loki ${LOKI_VERSION}"; fi
  printf '%s  %s\n' "${LOKI_SHA256}" "${tempdir}/loki.zip" | sha256sum -c - >/dev/null || { rm -rf "${tempdir}"; fail "Loki archive checksum mismatch"; }
  unzip -q "${tempdir}/loki.zip" loki-linux-amd64 -d "${tempdir}"
  install -o root -g root -m 0755 "${tempdir}/loki-linux-amd64" /usr/local/bin/hermes-security-loki
  printf '%s\n' "${LOKI_SHA256}" > "${marker}"
  chmod 0644 "${marker}"
  rm -rf "${tempdir}"
  return 0
}

prepare_secret() {
  local env_file="${ETC_DIR}/grafana.env"
  if [[ ! -e "${env_file}" ]]; then
    umask 077
    {
      printf '%s=%s\n' GF_SECURITY_ADMIN_PASSWORD "$(openssl rand -hex 32)"
      printf '%s=%s\n' GF_SECURITY_SECRET_KEY "$(openssl rand -hex 32)"
    } > "${env_file}"
  fi
  chmod 0600 "${env_file}"
  chown root:grafana "${env_file}"
}

check_port_available() {
  local port="$1" unit="$2"
  if ! systemctl is-active --quiet "${unit}" && [[ -n "$(ss -H -ltn "( sport = :${port} )")" ]]; then
    fail "port ${port} is already in use; inspect listeners before installing"
  fi
}

verify_listener() {
  local port="$1" output address
  output="$(ss -H -ltn "( sport = :${port} )")" || fail "could not inspect listeners"
  [[ -n "${output}" ]] || fail "port ${port} is not listening"
  while read -r _ _ _ address _; do
    [[ "${address}" == "127.0.0.1:${port}" ]] || fail "port ${port} listener is not confined to loopback"
  done <<< "${output}"
}

start_service() {
  local unit="$1" changed="$2"
  systemctl enable "${unit}" >/dev/null
  if systemctl is-active --quiet "${unit}"; then
    if [[ "${changed}" == 1 ]]; then systemctl restart "${unit}"; fi
  else
    systemctl start "${unit}"
  fi
}

main() {
  if [[ "${1:-}" == --preflight ]]; then assert_safe_to_manage; return 0; fi
  [[ "${ROOT}" == / ]] || fail "test root is only supported for --preflight"
  [[ "${EUID}" -eq 0 ]] || fail "must run as root (for example: sudo bash scripts/install-security-dashboard.sh)"
  assert_safe_to_manage
  # Check prerequisites and ports before creating ownership state.
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID}" == ubuntu && "${VERSION_ID}" == 26.04 && "$(dpkg --print-architecture)" == amd64 ]] \
    || fail "expected Ubuntu 26.04 amd64"
  local dependency
  for dependency in curl openssl unzip ss; do
    command -v "${dependency}" >/dev/null 2>&1 || fail "missing prerequisite ${dependency}; install it before retrying"
  done
  check_port_available 3100 hermes-security-loki.service
  check_port_available 9096 hermes-security-loki.service
  check_port_available 12346 hermes-security-alloy.service
  check_port_available 3001 hermes-security-grafana.service
  install -d -m 0755 -o root -g root "${STATE_DIR}"
  install -d -m 0755 -o root -g root "${ETC_DIR}"
  # Mark ownership before writing anything so an interrupted install remains recoverable.
  printf '%s\n' "hermes-security-dashboard-managed-v1" > "${MARKER}"
  chmod 0600 "${MARKER}"
  ensure_packages
  groupadd --system loki-security 2>/dev/null || true
  id loki-security >/dev/null 2>&1 || useradd --system --gid loki-security --home-dir /nonexistent --shell /usr/sbin/nologin loki-security
  install -d -m 0750 -o loki-security -g loki-security "${STATE_DIR}/loki"
  install -d -m 0750 -o alloy -g alloy "${STATE_DIR}/alloy"
  install -d -m 0750 -o grafana -g grafana "${STATE_DIR}/grafana" "${ROOT%/}/var/log/hermes-security-dashboard/grafana"
  install -d -m 0750 -o root -g grafana "${ETC_DIR}/provisioning/datasources" "${ETC_DIR}/provisioning/dashboards" "${ETC_DIR}/dashboards"

  local loki_changed=0 alloy_changed=0 grafana_changed=0
  if install_if_changed "${CONFIG_DIR}/loki.yaml" "${ETC_DIR}/loki.yaml" 0640 loki-security; then loki_changed=1; fi
  if install_if_changed "${CONFIG_DIR}/config.alloy" "${ETC_DIR}/config.alloy" 0640 alloy; then alloy_changed=1; fi
  if install_if_changed "${CONFIG_DIR}/grafana.ini" "${ETC_DIR}/grafana.ini" 0640 grafana; then grafana_changed=1; fi
  if install_if_changed "${CONFIG_DIR}/provisioning/datasources/loki.yaml" "${ETC_DIR}/provisioning/datasources/loki.yaml" 0640 grafana; then grafana_changed=1; fi
  if install_if_changed "${CONFIG_DIR}/provisioning/dashboards/security.yaml" "${ETC_DIR}/provisioning/dashboards/security.yaml" 0640 grafana; then grafana_changed=1; fi
  if install_if_changed "${CONFIG_DIR}/dashboard.json" "${ETC_DIR}/dashboards/security.json" 0640 grafana; then grafana_changed=1; fi
  if install_if_changed "${REPO_DIR}/systemd/hermes-security-loki.service" "${UNIT_DIR}/hermes-security-loki.service" 0644 root; then loki_changed=1; fi
  if install_if_changed "${REPO_DIR}/systemd/hermes-security-alloy.service" "${UNIT_DIR}/hermes-security-alloy.service" 0644 root; then alloy_changed=1; fi
  if install_if_changed "${REPO_DIR}/systemd/hermes-security-grafana.service" "${UNIT_DIR}/hermes-security-grafana.service" 0644 root; then grafana_changed=1; fi
  prepare_secret
  if install_loki_binary; then loki_changed=1; fi
  alloy validate "${ETC_DIR}/config.alloy"
  /usr/local/bin/hermes-security-loki -verify-config=true -config.file="${ETC_DIR}/loki.yaml"
  systemctl daemon-reload
  start_service hermes-security-loki.service "${loki_changed}"
  start_service hermes-security-alloy.service "${alloy_changed}"
  start_service hermes-security-grafana.service "${grafana_changed}"
  curl -fsS --retry 20 --retry-delay 1 --retry-connrefused http://127.0.0.1:3100/ready >/dev/null
  curl -fsS --retry 20 --retry-delay 1 --retry-connrefused http://127.0.0.1:3001/api/health >/dev/null
  curl -fsS --retry 20 --retry-delay 1 --retry-connrefused http://127.0.0.1:12346/-/ready >/dev/null
  local unit port
  for unit in hermes-security-loki hermes-security-alloy hermes-security-grafana; do
    systemctl is-active --quiet "${unit}" || fail "${unit} inactive"
  done
  for port in 3100 9096 3001 12346; do verify_listener "${port}"; done
  log "ready: http://127.0.0.1:3001 (SSH tunnel only); Loki 127.0.0.1:3100; no firewall/Caddy changes"
  log "Grafana credentials are in ${ETC_DIR}/grafana.env (root:grafana, mode 0600); never print that file"
}

main "$@"
