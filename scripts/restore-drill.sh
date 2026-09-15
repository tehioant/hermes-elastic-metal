#!/usr/bin/env bash
# Prove the latest backup is restorable: restore to a scratch dir, unpack one
# volume archive into a throwaway Docker volume, list it, clean up.
# Usage: sudo bash -c 'set -a; . /etc/restic/env; restore-drill.sh [volume-name]'
set -Eeuo pipefail

: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD_FILE:?}" "${AWS_ACCESS_KEY_ID:?}" "${AWS_SECRET_ACCESS_KEY:?}"

readonly TAG="${BACKUP_TAG:-$(hostname -s)}"
RESTORE_DIR="$(mktemp -d /tmp/restore-drill.XXXXXX)"
readonly RESTORE_DIR
readonly TEST_VOLUME="restore-drill-$$"
readonly LOCK="/run/lock/restic.lock"

log()  { printf '🧪 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
cleanup() {
  rm -rf "${RESTORE_DIR}"
  docker volume rm -f "${TEST_VOLUME}" >/dev/null 2>&1 || true
}
trap 'fail "restore drill failed at line ${LINENO}"' ERR
trap cleanup EXIT

acquire_lock() {
  exec 9>"${LOCK}"
  flock -w 600 9 || fail "could not acquire restic lock"
}

restore_latest() {
  log "restoring latest snapshot (tag ${TAG}) to ${RESTORE_DIR}"
  restic restore latest --tag "${TAG}" --target "${RESTORE_DIR}" --include /var/backups/docker
}

pick_archive() {
  local requested="${1:-}"
  if [[ -n "${requested}" ]]; then
    printf '%s\n' "${RESTORE_DIR}/var/backups/docker/${requested}.tar.gz"
  else
    find "${RESTORE_DIR}/var/backups/docker" -name '*.tar.gz' | sort | head -n1
  fi
}

unpack_into_volume() {
  local archive="$1"
  [[ -f "${archive}" ]] || fail "no volume archive found in snapshot (${archive})"
  log "unpacking $(basename "${archive}") into volume ${TEST_VOLUME}"
  docker volume create "${TEST_VOLUME}" >/dev/null
  docker run --rm \
    -v "${TEST_VOLUME}:/target" \
    -v "$(dirname "${archive}"):/src:ro" \
    busybox:1.37 tar xzf "/src/$(basename "${archive}")" -C /target
  docker run --rm -v "${TEST_VOLUME}:/data:ro" busybox:1.37 sh -c 'ls -la /data && echo "files: $(find /data -type f | wc -l)"'
}

main() {
  acquire_lock
  restore_latest
  unpack_into_volume "$(pick_archive "${1:-}")"
  log "✅ restore drill passed"
}

main "$@"
