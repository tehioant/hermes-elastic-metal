#!/usr/bin/python3
"""Derive a private passive IDS configuration from the installed distro defaults."""
import argparse
import copy
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import yaml


def render(base, interface, addresses):
    if interface != 'eno1':
        raise ValueError('only the approved eno1 interface is supported')
    home = []
    for link in addresses:
        for address in link.get('addr_info', []):
            if address.get('scope') == 'global' and address.get('family') in ('inet', 'inet6'):
                ip = ipaddress.ip_address(address['local'])
                home.append(f'{ip}/{ip.max_prefixlen}')
    if not home:
        raise ValueError('eno1 has no global IPv4/IPv6 addresses')
    config = copy.deepcopy(base)
    groups = config.setdefault('vars', {}).setdefault('address-groups', {})
    groups.update(HOME_NET='[' + ','.join(home) + ']', EXTERNAL_NET='!$HOME_NET')
    config['runmode'] = 'workers'
    config['af-packet'] = [{'interface': interface, 'threads': 2, 'cluster-id': 187,
                            'cluster-type': 'cluster_flow', 'defrag': True,
                            'tpacket-v3': True, 'use-mmap': True, 'disable-promisc': True}]
    alert = {key: False for key in ('payload', 'payload-printable', 'payload-length',
              'packet', 'http-body', 'http-body-printable', 'metadata',
              'websocket-payload', 'websocket-payload-printable', 'tagged-packets')}
    config['outputs'] = [{'eve-log': {'enabled': True, 'filetype': 'regular',
                           'filename': 'eve.json', 'threaded': False, 'metadata': False,
                           'pcap-file': False, 'community-id': False,
                           'types': [{'alert': alert}, {'stats': {'totals': True,
                                           'threads': False, 'deltas': False}}]}}]
    config['stats'] = {'enabled': True, 'interval': 30}
    config['logging'] = {'default-log-level': 'notice', 'outputs': [
        {'file': {'enabled': True, 'level': 'notice', 'filename': 'suricata.log'}}]}
    config['default-log-dir'] = '/var/log/hermes-suricata'
    config['default-rule-path'] = '/var/lib/hermes-suricata/rules'
    config['rule-files'] = ['suricata.rules', '/etc/hermes-suricata/local.rules']
    config['unix-command'] = {'enabled': True, 'filename': '/run/hermes-suricata/suricata-command.socket'}
    config.setdefault('flow', {}).update(memcap='128mb')
    stream = config.setdefault('stream', {})
    stream.update(memcap='64mb', inline=False)
    stream.setdefault('reassembly', {}).update(memcap='128mb')
    config.setdefault('threading', {})['detect-thread-ratio'] = 1.0
    return config


def check_rules(paths):
    """Reject any active rule whose action is not alert (including continuations)."""
    count = 0
    for path in paths:
        continued = False
        for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if not continued:
                if not re.match(r'^alert\s+', line):
                    raise ValueError(f'{path}:{number}: only alert rules permitted')
                count += 1
            continued = line.endswith('\\')
        if continued:
            raise ValueError(f'{path}: incomplete continued rule')
    if not count:
        raise ValueError('empty rule set')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('render')
    r.add_argument('--base', required=True)
    r.add_argument('--output', required=True)
    r.add_argument('--interface', default='eno1', choices=['eno1'])
    r.add_argument('--addresses-json', help='isolated validation fixture; otherwise ip -j addr')
    g = sub.add_parser('check-rules')
    g.add_argument('paths', nargs='+')
    args = parser.parse_args()
    if args.command == 'check-rules':
        check_rules(args.paths)
        return
    data = (Path(args.addresses_json).read_text() if args.addresses_json else
            subprocess.check_output(['ip', '-j', 'addr', 'show', 'dev', args.interface], text=True))
    config = render(yaml.safe_load(Path(args.base).read_text()), args.interface, json.loads(data))
    Path(args.output).write_text('%YAML 1.1\n---\n' + yaml.safe_dump(config, sort_keys=False))


if __name__ == '__main__':
    main()
