#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/../scripts/install-netdata.sh"

dpkg-query() {
  if [[ "${MOCK_NETDATA_INSTALLED}" == yes ]]; then
    printf 'install ok installed\n'
  else
    return 1
  fi
}

marker_dir="$(mktemp -d)"
trap 'rm -rf "${marker_dir}"' EXIT
marker="${marker_dir}/managed"

MOCK_NETDATA_INSTALLED=yes
if result="$(assert_managed_netdata "${marker}" 2>&1)"; then
  printf 'expected unmanaged Netdata to be refused\n' >&2
  exit 1
fi
[[ "${result}" == *'existing Netdata was not installed by this script'* ]]

install -m 0644 /dev/null "${marker}"
assert_managed_netdata "${marker}"

rm -f "${marker}"
MOCK_NETDATA_INSTALLED=no
assert_managed_netdata "${marker}"

config="$(dirname "$0")/../config/netdata/netdata.conf"
grep -Fxq '    bind to = 127.0.0.1:19999' "${config}"
grep -Fxq '    allow connections from = localhost' "${config}"
printf 'Netdata management guard and loopback config: PASS\n'
