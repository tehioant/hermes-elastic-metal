#!/usr/bin/env python3
"""Offline real Suricata smoke test. Generated packets never touch a network."""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import uuid


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    value = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while value >> 16:
        value = (value & 65535) + (value >> 16)
    return (~value) & 65535


def write_pcap(path, destination, marker):
    payload = marker.encode()
    udp = struct.pack('!HHHH', 44444, 55991, len(payload) + 8, 0) + payload
    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(udp) + 20, 1, 0, 64, 17, 0,
                         socket.inet_aton('192.0.2.123'), socket.inet_aton(destination))
    header = header[:10] + struct.pack('!H', checksum(header)) + header[12:]
    packet = bytes.fromhex('0200000000010200000000020800') + header + udp
    global_header = struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    record = struct.pack('<IIII', 1, 0, len(packet), len(packet)) + packet
    path.write_bytes(global_header + record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hermes-suricata/suricata.yaml')
    parser.add_argument('--rules', default='/etc/hermes-suricata/local.rules')
    parser.add_argument('--destination', required=True)
    parser.add_argument('--alerts-output', help='Save detector-produced filtered test alerts')
    args = parser.parse_args()
    ipaddress.IPv4Address(args.destination)
    marker = 'HERMES_IDS_SMOKE_' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='suricata-smoke-') as directory:
        root = Path(directory)
        write_pcap(root / 'test.pcap', args.destination, marker)
        subprocess.run(['suricata', '-c', args.config, '-r', str(root / 'test.pcap'),
                        '-S', args.rules, '-l', str(root), '--runmode', 'single',
                        '--set', 'unix-command.enabled=no'], check=True, timeout=60,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        records = [json.loads(line) for line in (root / 'eve.json').read_text().splitlines()]
        alerts = [event for event in records if event.get('event_type') == 'alert'
                  and event.get('alert', {}).get('signature_id') == 9900001]
        if not alerts:
            raise RuntimeError('No detector-produced local smoke signature alert')
        forbidden = {'payload', 'payload_printable', 'packet', 'http', 'dns', 'tls',
                     'files', 'fileinfo', 'http_body', 'websocket', 'email'}
        for event in alerts:
            if forbidden & set(event) or marker in json.dumps(event):
                raise RuntimeError('EVE retained application content')
        spec = importlib.util.spec_from_file_location('alert_filter', Path(__file__).resolve().parents[1] / 'scripts/suricata-alerts.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sanitized = [module.sanitize(event) for event in alerts]
        if any(event is None for event in sanitized):
            raise RuntimeError('Actual detector output rejected by filter')
        if args.alerts_output:
            with open(args.alerts_output, 'w', encoding='utf-8') as output:
                for event in sanitized:
                    output.write(json.dumps(event, separators=(',', ':')) + '\n')
        print(json.dumps({'passed': True, 'detector_alerts': len(alerts),
                          'signature_id': 9900001, 'payload_retained': False,
                          'network_packets_sent': 0}))


if __name__ == '__main__':
    main()
