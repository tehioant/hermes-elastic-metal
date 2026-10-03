#!/usr/bin/env bash
# Opt-in selective network metadata collector. Never changes public networking.
set -Eeuo pipefail
ROOT="${SECURITY_WATCH_ROOT:-/}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly STATE_DIR="${ROOT%/}/var/lib/hermes-security-watch"
readonly ETC_DIR="${ROOT%/}/etc/hermes-security-watch"
readonly UNIT_DIR="${ROOT%/}/etc/systemd/system"
readonly PARENT_MARKER="${ROOT%/}/var/lib/hermes-security-dashboard/managed-by-hermes"
readonly MARKER="${STATE_DIR}/managed-by-hermes"
readonly ALLOY="${ROOT%/}/etc/hermes-security-dashboard/config.alloy"
readonly ALLOY_HASH="${STATE_DIR}/alloy-origin.sha256"
readonly DASH_DIR="${ROOT%/}/etc/hermes-security-dashboard/watch-dashboards"
readonly PROVISION_DIR="${ROOT%/}/etc/hermes-security-dashboard/provisioning/dashboards"
INTERFACE=""

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
exists_or_link() { [[ -e "$1" || -L "$1" ]]; }

parse_args() {
  PREFLIGHT=0
  while (($#)); do
    case "$1" in
      --preflight) PREFLIGHT=1; shift ;;
      --interface) (($# >= 2)) || fail "--interface requires a name"; INTERFACE="$2"; shift 2 ;;
      *) fail "unknown argument: $1" ;;
    esac
  done
  [[ -n "$INTERFACE" ]] || fail "--interface NAME is required"
  [[ "$INTERFACE" =~ ^[A-Za-z0-9_.:-]{1,15}$ && "$INTERFACE" != *..* ]] || fail "invalid interface name"
}

assert_safe_to_manage() {
  [[ -f "$PARENT_MARKER" && ! -L "$PARENT_MARKER" ]] \
    && [[ "$(<"$PARENT_MARKER")" == hermes-security-dashboard-managed-v1 ]] \
    || fail "valid security-dashboard ownership marker is required"
  if exists_or_link "$MARKER"; then
    [[ -f "$MARKER" && ! -L "$MARKER" ]] || fail "watch ownership marker is unsafe"
    [[ "$(<"$MARKER")" == hermes-security-watch-managed-v1 ]] || fail "security-watch ownership marker is invalid"
    if exists_or_link "$ALLOY"; then
      [[ ! -L "$ALLOY" ]] || fail "Alloy config must not be a symlink"
      local live_hash source_hash recorded_hash
      live_hash="$(sha256sum "$ALLOY" | cut -d' ' -f1)"
      source_hash="$(sha256sum "$REPO_DIR/config/security-dashboard/config.alloy" | cut -d' ' -f1)"
      recorded_hash=""
      [[ ! -f "$ALLOY_HASH" ]] || recorded_hash="$(<"$ALLOY_HASH")"
      if [[ "$live_hash" != "$source_hash" && "$live_hash" != "$recorded_hash" ]]; then fail "managed Alloy config has drifted; refusing to overwrite"; fi
    fi
  else
    local path
    for path in \
      "$STATE_DIR" "$ETC_DIR" "$UNIT_DIR/hermes-security-watch.service" \
      "$UNIT_DIR/hermes-security-watch-geoip-update.service" "$UNIT_DIR/hermes-security-watch-geoip-update.timer" \
      "$ROOT/usr/local/lib/hermes-security-watch" "$ALLOY_HASH" \
      "$PROVISION_DIR/watch.yaml" "$DASH_DIR"; do
      exists_or_link "$path" && fail "existing security-watch target is unmanaged: $path"
    done
    if exists_or_link "$ALLOY"; then
      [[ ! -L "$ALLOY" ]] || fail "Alloy config must not be a symlink"
      local current_hash
      current_hash="$(sha256sum "$ALLOY" | cut -d' ' -f1)"
      if [[ "$current_hash" != 5e4f0e91604eaa7b930ff7f03dae98e27dd78b1cb61024a8dd86a61defcbae43 && "$current_hash" != "$(sha256sum "$REPO_DIR/config/security-dashboard/config.alloy" | cut -d' ' -f1)" ]]; then
        fail "existing Alloy config is modified or unrecognized; refusing to replace it"
      fi
    fi
  fi
  local path
  for path in "$ETC_DIR" "$ETC_DIR/collector.env" "$STATE_DIR" "$STATE_DIR/geoip" \
    "$STATE_DIR/geoip/city.mmdb" "$STATE_DIR/geoip/asn.mmdb" "$STATE_DIR/geoip/manifest.json" \
    "$UNIT_DIR/hermes-security-watch.service" "$UNIT_DIR/hermes-security-watch-geoip-update.service" \
    "$UNIT_DIR/hermes-security-watch-geoip-update.timer" "$ROOT/usr/local/lib/hermes-security-watch" \
    "$ROOT/usr/local/lib/hermes-security-watch/collector.py" "$ROOT/usr/local/lib/hermes-security-watch/update-security-watch-geoip.py" \
    "$ALLOY_HASH" "$PROVISION_DIR/watch.yaml" "$DASH_DIR" "$DASH_DIR/server-watch.json"; do
    [[ ! -L "$path" ]] || fail "security-watch target must not be a symlink: $path"
  done
}

install_file() {
  local src="$1" dst="$2" mode="$3" group="${4:-root}"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    chown "root:${group}" "$dst" || fail "failed to repair ownership on $dst"
    chmod "$mode" "$dst" || fail "cannot repair mode on $dst"
    return 1
  fi
  install -D -o root -g "$group" -m "$mode" "$src" "$dst" || fail "failed to install $dst"
  return 0
}

main() {
  parse_args "$@"
  assert_safe_to_manage
  (( PREFLIGHT )) && return 0
  local file
  for file in "$REPO_DIR/scripts/security-watch-collector.py" \
    "$REPO_DIR/scripts/update-security-watch-geoip.py" \
    "$REPO_DIR/config/security-watch/dashboard.json" \
    "$REPO_DIR/config/security-watch/provisioning.yaml"; do
    [[ -f "$file" ]] || fail "required security-watch source is missing: $file"
  done
  if [[ "$ROOT" != / ]]; then
    [[ "${SECURITY_WATCH_TEST_MODE:-}" == 1 ]] || fail "custom root is supported only for --preflight"
  else
    [[ "$EUID" -eq 0 ]] || fail "must run as root"
  fi
  local test_mode=0
  [[ "$ROOT" == / ]] || test_mode=1
  if (( ! test_mode )); then
    . /etc/os-release
    [[ "${ID}" == ubuntu && "${VERSION_ID}" == 26.04 && "$(dpkg --print-architecture)" == amd64 ]] || fail "expected Ubuntu 26.04 amd64"
    for cmd in apt-get systemctl id alloy python3; do command -v "$cmd" >/dev/null || fail "missing prerequisite $cmd"; done
    for service in hermes-security-alloy.service hermes-security-grafana.service hermes-security-loki.service; do
      systemctl is-active --quiet "$service" || fail "prerequisite services must be active: $service"
    done
    apt-get install -y --no-install-recommends python3-maxminddb libpcap0.8t64 || fail "failed to install collector runtime libraries"
    getent group hermes-watch >/dev/null 2>&1 || groupadd --system hermes-watch || fail "cannot create hermes-watch group"
    id hermes-watch >/dev/null 2>&1 || useradd --system --gid hermes-watch --home-dir /nonexistent --shell /usr/sbin/nologin hermes-watch || fail "cannot create hermes-watch account"
  fi
  install -d -m 0750 -o root -g hermes-watch "$STATE_DIR" "${ROOT%/}/usr/local/lib/hermes-security-watch"
  install -d -m 0750 -o root -g root "$ETC_DIR"
  install -d -m 0750 -o root -g hermes-watch "$STATE_DIR/geoip"
  if [[ ! -e "$MARKER" ]]; then printf '%s\n' hermes-security-watch-managed-v1 > "$MARKER"; fi
  chmod 0600 "$MARKER"
  if (( ! test_mode )); then chown root:root "$MARKER"; fi
  local city="$STATE_DIR/geoip/city.mmdb" asn="$STATE_DIR/geoip/asn.mmdb"
  if [[ ! -f "$city" || ! -f "$asn" ]]; then
    python3 "$REPO_DIR/scripts/update-security-watch-geoip.py" --directory "$STATE_DIR/geoip" || fail "initial GeoIP download failed"
  fi
  [[ -f "$city" && -f "$asn" ]] || fail "validated GeoIP databases are required in $STATE_DIR/geoip"
  python3 "$REPO_DIR/scripts/security-watch-collector.py" --validate-databases "$city" "$asn" || fail "GeoIP database validation failed"
  if (( ! test_mode )); then chown root:hermes-watch "$city" "$asn"; fi
  chmod 0640 "$city" "$asn"
  local collector_config_stage="$STATE_DIR/collector.env"
  printf 'WATCH_INTERFACE=%s\nCITY_DB=%s\nASN_DB=%s\n' "$INTERFACE" "$city" "$asn" > "$collector_config_stage"
  chmod 0600 "$collector_config_stage"
  local collector_config_changed=0
  if install_file "$collector_config_stage" "$ETC_DIR/collector.env" 0600; then collector_config_changed=1; fi
  local collector_code_changed=0
  if install_file "$REPO_DIR/scripts/security-watch-collector.py" "${ROOT%/}/usr/local/lib/hermes-security-watch/collector.py" 0755; then collector_code_changed=1; fi
  install_file "$REPO_DIR/scripts/update-security-watch-geoip.py" "${ROOT%/}/usr/local/lib/hermes-security-watch/update-security-watch-geoip.py" 0755 || :
  local alloy_changed=0 grafana_changed=0 collector_unit_changed=0 timer_changed=0 old_hash new_hash
  new_hash="$(sha256sum "$REPO_DIR/config/security-dashboard/config.alloy" | cut -d' ' -f1)"
  if (( ! test_mode )); then alloy validate "$REPO_DIR/config/security-dashboard/config.alloy" || fail "Alloy validation failed"; fi
  if [[ -f "$MARKER" && -f "$ALLOY_HASH" ]]; then
    old_hash="$(<"$ALLOY_HASH")"
    [[ "$(sha256sum "$ALLOY" | cut -d' ' -f1)" == "$old_hash" || "$(sha256sum "$ALLOY" | cut -d' ' -f1)" == "$new_hash" ]] \
      || fail "managed Alloy config has drifted; refusing to overwrite"
  fi
  if install_file "$REPO_DIR/config/security-dashboard/config.alloy" "$ALLOY" 0640 alloy; then alloy_changed=1; else :; fi
  printf '%s\n' "$new_hash" > "$ALLOY_HASH"; chmod 0600 "$ALLOY_HASH"
  install -d -m 0750 -o root -g grafana "$PROVISION_DIR" "$DASH_DIR"
  if install_file "$REPO_DIR/config/security-watch/provisioning.yaml" "$PROVISION_DIR/watch.yaml" 0640 grafana; then grafana_changed=1; else :; fi
  if install_file "$REPO_DIR/config/security-watch/dashboard.json" "$DASH_DIR/server-watch.json" 0640 grafana; then grafana_changed=1; else :; fi
  local unit
  for unit in hermes-security-watch.service hermes-security-watch-geoip-update.service hermes-security-watch-geoip-update.timer; do
    if install_file "$REPO_DIR/systemd/$unit" "$UNIT_DIR/$unit" 0644; then
      case "$unit" in
        hermes-security-watch.service) collector_unit_changed=1 ;;
        hermes-security-watch-geoip-update.timer) timer_changed=1 ;;
      esac
    fi
  done
  if (( ! test_mode )); then
    alloy validate "$ALLOY" || fail "Alloy validation failed"
    systemctl daemon-reload
    systemctl enable hermes-security-watch.service hermes-security-watch-geoip-update.timer >/dev/null
    if systemctl is-active --quiet hermes-security-alloy.service; then
      if (( alloy_changed )); then systemctl restart hermes-security-alloy.service; fi
    fi
    if systemctl is-active --quiet hermes-security-grafana.service; then
      if (( grafana_changed )); then systemctl restart hermes-security-grafana.service; fi
    fi
    if systemctl is-active --quiet hermes-security-watch.service; then
      if (( collector_unit_changed || collector_config_changed || collector_code_changed )); then systemctl restart hermes-security-watch.service; fi
    else
      systemctl start hermes-security-watch.service
    fi
    if systemctl is-active --quiet hermes-security-watch-geoip-update.timer; then
      if (( timer_changed )); then systemctl restart hermes-security-watch-geoip-update.timer; fi
    else
      systemctl start hermes-security-watch-geoip-update.timer
    fi
  fi
  printf 'Security Watch installed for interface %s. No listeners or firewall rules added.\n' "$INTERFACE"
}
main "$@"
