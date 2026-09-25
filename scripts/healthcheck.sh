#!/usr/bin/env bash
# One line per check; exit 1 if anything failed so `systemctl status healthcheck` shows it.
set -uo pipefail

readonly DISK_USAGE_LIMIT=80
readonly MEMORY_USAGE_LIMIT=90
readonly RESTART_LOOP_THRESHOLD=3
failures=0

ok()   { printf '✅ %s\n' "$*"; }
bad()  { printf '❌ %s\n' "$*" >&2; failures=$((failures + 1)); }

check_raid() {
  if [[ ! -r /proc/mdstat ]] || ! grep -q '^md' /proc/mdstat; then
    bad "raid: no md array found"
    return
  fi
  if grep -qE '\[.*_.*\]|recovery|degraded' /proc/mdstat; then
    bad "raid: degraded or rebuilding — $(grep -E '^md|\[' /proc/mdstat | tr '\n' ' ')"
  else
    ok "raid: all arrays healthy"
  fi
}

check_smart() {
  local disk status
  for disk in /dev/sda /dev/sdb; do
    [[ -b "${disk}" ]] || continue
    status="$(smartctl -H "${disk}" 2>/dev/null | awk -F: '/overall-health/ {gsub(/ /,"",$2); print $2}')"
    if [[ "${status}" == "PASSED" ]]; then
      ok "smart ${disk}: PASSED"
    else
      bad "smart ${disk}: ${status:-unknown}"
    fi
  done
}

check_disk_usage() {
  local usage
  usage="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
  if (( usage > DISK_USAGE_LIMIT )); then
    bad "disk /: ${usage}% used (limit ${DISK_USAGE_LIMIT}%)"
  else
    ok "disk /: ${usage}% used"
  fi
}

check_memory() {
  local usage
  usage="$(free | awk '/^Mem:/ {printf "%d", $3/$2*100}')"
  if (( usage > MEMORY_USAGE_LIMIT )); then
    bad "memory: ${usage}% used (limit ${MEMORY_USAGE_LIMIT}%)"
  else
    ok "memory: ${usage}% used"
  fi
}

check_containers() {
  local looping
  looping="$(docker ps -q | xargs -r docker inspect --format '{{.Name}} {{.RestartCount}}' \
    | awk -v t="${RESTART_LOOP_THRESHOLD}" '$2 > t {print $1}' | tr '\n' ' ')"
  if [[ -n "${looping}" ]]; then
    bad "containers restarting: ${looping}"
  else
    ok "containers: $(docker ps -q | wc -l) running, none looping"
  fi
}

check_ras() {
  command -v ras-mc-ctl >/dev/null || { ok "ras: rasdaemon not installed"; return; }
  local errors
  errors="$(ras-mc-ctl --error-count 2>/dev/null | awk 'NR>1 {s+=$2+$3} END {print s+0}')"
  if (( errors > 0 )); then
    bad "ras: ${errors} memory errors logged"
  else
    ok "ras: no memory errors"
  fi
}

check_last_backup() {
  local result
  result="$(systemctl show restic-backup.service --property=Result --value 2>/dev/null)"
  case "${result}" in
    success) ok "backup: last run succeeded" ;;
    "")      ok "backup: never run" ;;
    *)       bad "backup: last run result=${result}" ;;
  esac
}

check_dashboard_proxy() {
  systemctl list-unit-files caddy.service >/dev/null 2>&1 || { ok "dashboard proxy: not installed"; return; }
  if ! systemctl is-active --quiet caddy.service; then
    bad "dashboard proxy: caddy not active"
    return
  fi
  if curl -fsS --max-time 5 http://127.0.0.1:9119/api/status \
      | grep -Eq '"auth_required"[[:space:]]*:[[:space:]]*true'; then
    ok "dashboard proxy: caddy active, hermes auth required"
  else
    bad "dashboard proxy: PUBLIC WITHOUT AUTH or hermes down — check /api/status; stop caddy if auth is off"
  fi
}

main() {
  check_raid
  check_smart
  check_disk_usage
  check_memory
  check_containers
  check_ras
  check_last_backup
  check_dashboard_proxy
  (( failures == 0 )) || { printf '❌ %d check(s) failed\n' "${failures}" >&2; exit 1; }
  ok "all checks passed"
}

main "$@"
