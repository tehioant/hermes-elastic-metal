#!/usr/bin/env bash
# Archive every Docker volume, push volumes + /etc to restic, apply retention, verify.
# Env from /etc/restic/env: RESTIC_REPOSITORY RESTIC_PASSWORD_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
set -Eeuo pipefail

: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD_FILE:?}" "${AWS_ACCESS_KEY_ID:?}" "${AWS_SECRET_ACCESS_KEY:?}"

readonly STAGING="/var/backups/docker"
readonly TAG="${BACKUP_TAG:-$(hostname -s)}"
readonly LOCK="/run/lock/restic.lock"

log()  { printf '💾 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
trap 'fail "backup failed at line ${LINENO}"' ERR

acquire_lock() {
  exec 9>"${LOCK}"
  flock -n 9 || fail "another restic job is running"
}

dump_volumes() {
  rm -rf "${STAGING}"
  install -d -m 0700 "${STAGING}"
  local volume
  while read -r volume; do
    log "archiving volume ${volume}"
    docker run --rm \
      -v "${volume}:/source:ro" \
      -v "${STAGING}:/backup" \
      busybox:1.37 tar czf "/backup/${volume}.tar.gz" -C /source .
  done < <(docker volume ls --quiet)
}

snapshot() {
  log "pushing snapshot tagged ${TAG}"
  restic backup --quiet --tag "${TAG}" --one-file-system "${STAGING}" /etc
}

retain() {
  log "applying retention (7d / 4w / 12m)"
  restic forget --quiet --tag "${TAG}" --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune
}

verify() {
  log "verifying repository (5% data sample)"
  restic check --quiet --read-data-subset=5%
}

main() {
  acquire_lock
  dump_volumes
  snapshot
  retain
  verify
  rm -rf "${STAGING}"
  log "backup completed"
}

main "$@"
