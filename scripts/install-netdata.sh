#!/usr/bin/env bash
# Install the native Netdata agent with its dashboard bound to 127.0.0.1:19999.
# Independent from bootstrap.sh: a failure here never blocks host setup. Safe to re-run.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly CONFIG_SRC="${REPO_DIR}/config/netdata/netdata.conf"
readonly CONFIG_DST=/etc/netdata/netdata.conf
readonly MANAGED_MARKER=/etc/netdata/.hermes-native-install
readonly KICKSTART_URL=https://get.netdata.cloud/kickstart.sh

log()  { printf '📈 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

install_if_changed() {
  local src="$1" dst="$2"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    return 1
  fi
  install -m 0644 -D "${src}" "${dst}"
}

package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -qx 'install ok installed'
}

assert_managed_netdata() {
  local marker="$1"
  if package_installed netdata; then
    [[ -f "${marker}" ]] \
      || fail "existing Netdata was not installed by this script; inspect it before replacing it"
  fi
}

netdata_listens_beyond_loopback() {
  ss -H -ltn '( sport = :19999 )' | awk '$4 != "127.0.0.1:19999" { found = 1 } END { exit !found }'
}

run_kickstart() {
  local installer status=0
  installer="$(mktemp)"
  curl -fsSL "${KICKSTART_URL}" -o "${installer}" \
    && DISABLE_TELEMETRY=1 bash "${installer}" --non-interactive --release-channel stable --native-only --auto-update \
    || status=$?
  rm -f "${installer}"
  (( status == 0 )) || fail "Netdata kickstart failed (exit ${status})"
}

install_netdata_package() {
  install -m 0644 -D /dev/null "${MANAGED_MARKER}"
  run_kickstart
  package_installed netdata-repo || fail "Netdata's official package repository is not installed"
}

verify_loopback_only() {
  curl -fsS --retry 10 --retry-delay 1 --retry-connrefused http://127.0.0.1:19999/api/v1/info >/dev/null
  if netdata_listens_beyond_loopback; then
    fail "Netdata is listening beyond 127.0.0.1:19999"
  fi
}

main() {
  [[ "${EUID}" -eq 0 ]] || fail "must run as root"
  log "configuring Netdata (localhost only)"
  assert_managed_netdata "${MANAGED_MARKER}"

  local config_changed=0
  install_if_changed "${CONFIG_SRC}" "${CONFIG_DST}" && config_changed=1
  install -m 0644 /dev/null /etc/netdata/.opt-out-from-anonymous-statistics

  if ! package_installed netdata; then
    install_netdata_package
    config_changed=1
  fi
  install_if_changed "${CONFIG_SRC}" "${CONFIG_DST}" && config_changed=1

  systemctl enable --now netdata.service >/dev/null
  if (( config_changed )); then
    systemctl restart netdata.service
  fi
  verify_loopback_only
  log "Netdata ready on 127.0.0.1:19999 (published only via Caddy + Google login)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
