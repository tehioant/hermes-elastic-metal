#!/usr/bin/env bash
set -Eeuo pipefail

readonly password_path="${1:?restic password path required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"

if [[ -f "${password_path}" && "$(<"${password_path}")" != "${RESTIC_PASSWORD}" ]]; then
  printf 'Existing restic password differs; refusing to change backup configuration\n' >&2
  exit 1
fi
