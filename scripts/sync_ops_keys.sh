#!/usr/bin/env bash
set -Eeuo pipefail

root_keys="${1:?root authorized_keys required}"
deploy_key="${2:?deploy public key path required}"
ops_keys="${3:?ops authorized_keys path required}"

if [[ -f "${deploy_key}" ]]; then
  mapfile -t lines < "${deploy_key}"
  if [[ ${#lines[@]} -ne 1 || "${lines[0]}" != restrict\ ssh-ed25519\ * ]]; then
    printf 'Expected exactly one restricted ed25519 key in %s\n' "${deploy_key}" >&2
    exit 1
  fi
fi

umask 077
tmp="$(mktemp "${ops_keys}.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
cat -- "${root_keys}" > "${tmp}"
if [[ -f "${deploy_key}" ]]; then
  if [[ -s "${tmp}" && -n "$(tail -c 1 "${tmp}")" ]]; then
    printf '\n' >> "${tmp}"
  fi
  printf '%s\n' "${lines[0]}" >> "${tmp}"
fi
mv "${tmp}" "${ops_keys}"
