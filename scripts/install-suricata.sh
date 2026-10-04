#!/usr/bin/env bash
# Opt-in native passive IDS. Never changes firewall, dashboard or packet routing.
set -Eeuo pipefail
ROOT="${SURICATA_ROOT:-/}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ETC="${ROOT%/}/etc/hermes-suricata"
STATE="${ROOT%/}/var/lib/hermes-suricata"
LOG="${ROOT%/}/var/log/hermes-suricata"
LIB="${ROOT%/}/usr/local/lib/hermes-suricata"
MARKER="${ETC}/managed-by-hermes"
PACKAGE_MARKER="${ETC}/package-managed"
POLICY="${ROOT%/}/usr/sbin/policy-rc.d"
POLICY_BACKUP=""
POLICY_ACTIVE=0
UNITS=(hermes-suricata.service hermes-suricata-alerts.service
  hermes-suricata-rules-update.service hermes-suricata-rules-update.timer
  hermes-suricata-logrotate.service hermes-suricata-logrotate.timer)
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
restore_policy() {
  if (( POLICY_ACTIVE )); then
    rm -f "${POLICY}" || return 1
    if [[ -n "${POLICY_BACKUP}" ]]; then mv "${POLICY_BACKUP}" "${POLICY}" || return 1; fi
    POLICY_ACTIVE=0
  fi
}
trap restore_policy EXIT
safe_path() {
  local path="$1"
  while [[ "${path}" != / && -n "${path}" ]]; do
    [[ ! -L "${path}" ]] || fail "symlink refused: ${path}"
    path="$(dirname "${path}")"
  done
}
preflight() {
  local path unit
  for path in "${ETC}" "${STATE}" "${LOG}" "${LIB}" "${POLICY}"; do safe_path "${path}"; done
  for unit in "${UNITS[@]}"; do safe_path "${ROOT%/}/etc/systemd/system/${unit}"; done
  # Descendant symlinks can redirect privileged writes even on owned retries.
  /usr/bin/python3 - "${ETC}" "${STATE}" "${LOG}" "${LIB}" <<'PY'
import os, sys
for directory in sys.argv[1:]:
    if os.path.isdir(directory):
        for root, dirs, files in os.walk(directory):
            for name in dirs + files:
                p = os.path.join(root, name)
                if os.path.islink(p):
                    sys.exit('ERROR: symlink refused: ' + p)
PY
  if [[ -e "${MARKER}" ]]; then
    [[ "$(<"${MARKER}")" == hermes-suricata-managed-v1 ]] || fail 'invalid ownership marker'
    [[ "$(stat -c '%u:%a' "${ETC}")" == 0:750 && "$(stat -c '%u:%a' "${MARKER}")" == 0:600 ]] \
      || fail 'ownership marker must be root-protected (etc 0750, marker 0600)'
  else
    for path in "${ETC}" "${STATE}" "${LOG}" "${LIB}"; do
      [[ ! -e "${path}" ]] || fail "unmanaged path: ${path}"
    done
    for unit in "${UNITS[@]}"; do
      [[ ! -e "${ROOT%/}/etc/systemd/system/${unit}" ]] || fail "unmanaged unit: ${unit}"
    done
  fi
  if [[ -e "${ROOT%/}/usr/bin/suricata" || -e "${ROOT%/}/etc/suricata" ]]; then
    [[ -f "${PACKAGE_MARKER}" ]] || fail 'unmanaged Suricata package; explicit migration required'
  fi
  if [[ -e "${PACKAGE_MARKER}" ]]; then
    [[ "$(<"${PACKAGE_MARKER}")" == hermes-suricata-package-v1 ]] || fail 'invalid package ownership marker'
  fi
}
protect_stock() {
  systemctl disable --now suricata.service || fail 'stock Suricata disable failed'
  systemctl mask suricata.service || fail 'stock Suricata mask failed'
  local timer
  for timer in suricata-update.timer suricata-update.service suricata-rules-update.timer suricata-rules-update.service; do
    if [[ -e "${ROOT%/}/usr/lib/systemd/system/${timer}" || -e "${ROOT%/}/lib/systemd/system/${timer}" ]]; then
      systemctl disable --now "${timer}" || fail "stock updater disable failed: ${timer}"
      systemctl mask "${timer}" || fail "stock updater mask failed: ${timer}"
    fi
  done
}
ensure_packages() {
  local status=0
  if [[ -f "${PACKAGE_MARKER}" && -e "${ROOT%/}/usr/bin/suricata" ]]; then protect_stock; fi
  printf '%s\n' hermes-suricata-package-v1 > "${PACKAGE_MARKER}"
  chown root:root "${PACKAGE_MARKER}"; chmod 0600 "${PACKAGE_MARKER}"
  if [[ -e "${POLICY}" ]]; then
    POLICY_BACKUP="$(mktemp "${POLICY}.hermes.XXXXXX")"
    mv "${POLICY}" "${POLICY_BACKUP}"
  fi
  POLICY_ACTIVE=1
  printf '%s\n' '#!/bin/sh' 'exit 101' > "${POLICY}"
  chmod 0755 "${POLICY}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    suricata suricata-update python3-yaml logrotate || status=$?
  restore_policy || fail 'failed to restore policy-rc.d'
  if [[ -e "${ROOT%/}/usr/bin/suricata" ]]; then
    protect_stock
  else
    rm -f "${PACKAGE_MARKER}"
  fi
  (( status == 0 )) || fail "apt failed (${status}); ownership preserved if binary introduced"
}
main() {
  local only_preflight=0 interface=eno1 arg unit candidate validation
  while (( $# )); do
    arg="$1"; shift
    case "${arg}" in
      --preflight) only_preflight=1;;
      --interface) (( $# )) || fail 'missing interface'; interface="$1"; shift;;
      *) fail "unknown argument: ${arg}";;
    esac
  done
  [[ "${interface}" == eno1 ]] || fail 'only approved interface eno1 is supported'
  preflight
  if (( only_preflight )); then printf 'Suricata ownership/path preflight passed; no changes.\n'; return; fi
  [[ "${ROOT}" == / ]] || fail 'custom root is preflight-only'
  [[ "${EUID}" == 0 ]] || fail 'run as root'
  for arg in apt-get ip systemctl flock; do command -v "${arg}" >/dev/null || fail "missing ${arg}"; done
  [[ -f "${REPO_DIR}/scripts/suricata-alerts.py" && -f "${REPO_DIR}/systemd/hermes-suricata-alerts.service" ]] \
    || fail 'strict metadata filter and unit required in repository'
  ip link show dev "${interface}" >/dev/null
  exec 9>/run/lock/hermes-suricata-install.lock
  flock -n 9 || fail 'another installer is running'
  install -d -o root -g root -m 0750 "${ETC}"
  printf '%s\n' hermes-suricata-managed-v1 > "${MARKER}"
  chown root:root "${MARKER}"
  chmod 0600 "${MARKER}"
  ensure_packages
  if ! getent passwd hermes-suricata >/dev/null; then
    useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin hermes-suricata
  fi
  install -d -o root -g hermes-suricata -m 0750 "${ETC}" "${LIB}"
  install -d -o root -g hermes-suricata -m 0750 "${STATE}"
  install -d -o hermes-suricata -g hermes-suricata -m 0750 "${STATE}/filter"
  install -d -o hermes-suricata -g hermes-suricata -m 0750 "${LOG}"
  install -d -o root -g hermes-suricata -m 0750 "${STATE}/rules" "${STATE}/update"
  install -o root -g hermes-suricata -m 0640 "${REPO_DIR}/config/suricata/local.rules" "${ETC}/local.rules"
  install -o root -g root -m 0644 "${REPO_DIR}/config/suricata/logrotate.conf" "${ETC}/logrotate.conf"
  install -o root -g root -m 0755 "${REPO_DIR}/scripts/suricata-config.py" "${LIB}/config.py"
  install -o root -g root -m 0755 "${REPO_DIR}/scripts/update-suricata-rules.sh" "${LIB}/update-rules.sh"
  install -o root -g hermes-suricata -m 0640 "${REPO_DIR}/scripts/suricata-alerts.py" "${LIB}/alerts.py"
  candidate="$(mktemp "${ETC}/suricata.yaml.XXXXXX")"
  validation="$(mktemp -d "${ETC}/.validation.XXXXXX")"
  /usr/bin/python3 "${LIB}/config.py" render --base /etc/suricata/suricata.yaml \
    --interface "${interface}" --output "${candidate}"
  /usr/bin/suricata -T --init-errors-fatal -c "${candidate}" -S "${ETC}/local.rules" -l "${validation}"
  chown root:hermes-suricata "${candidate}"; chmod 0640 "${candidate}"
  mv -f "${candidate}" "${ETC}/suricata.yaml"
  "${LIB}/update-rules.sh"
  /usr/bin/suricata -T --init-errors-fatal -c "${ETC}/suricata.yaml" -l "${validation}"
  rm -rf "${validation}"
  for unit in "${UNITS[@]}"; do
    install -o root -g root -m 0644 "${REPO_DIR}/systemd/${unit}" "/etc/systemd/system/${unit}"
  done
  systemctl daemon-reload
  systemctl enable hermes-suricata.service hermes-suricata-alerts.service \
    hermes-suricata-rules-update.timer hermes-suricata-logrotate.timer
  systemctl restart hermes-suricata.service hermes-suricata-alerts.service
  systemctl start hermes-suricata-rules-update.timer hermes-suricata-logrotate.timer
  systemctl is-active --quiet hermes-suricata.service hermes-suricata-alerts.service
  [[ "$(systemctl is-enabled suricata.service)" == masked ]] || fail 'stock service not masked'
  printf 'Passive eno1 IDS active; verify alert ingestion separately. Firewall and Grafana unchanged.\n'
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
