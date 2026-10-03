#!/usr/bin/env python3
"""Real packet-capture smoke test; must be run inside an explicitly isolated netns."""
import argparse
import fcntl
import ipaddress
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts" / "security-watch-collector.py"
SECRET = b"SMOKE_SECRET_MUST_NEVER_APPEAR"


def run(*argv):
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def interface_mac(name):
    request = struct.pack("256s", name.encode()[:15])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        return fcntl.ioctl(control.fileno(), 0x8927, request)[18:24]


def ethernet(dst_mac, src_mac, ethertype, payload):
    return dst_mac + src_mac + struct.pack("!H", ethertype) + payload


def ipv4(src, dst, proto, body, ident=22):
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), ident, 0, 64, proto, 0,
                        ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed) + body


def tcp(sport, dport, flags):
    return struct.pack("!HHIIHHHH", sport, dport, 1, 0, (5 << 12) | flags, 4096, 0, 0) + SECRET


def ipv6(src, dst, next_header, body):
    return struct.pack("!IHBB16s16s", 6 << 28, len(body), next_header, 55,
                       ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed) + body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-isolated-root", action="store_true")
    parser.add_argument("--city-db", required=True)
    parser.add_argument("--asn-db", required=True)
    parser.add_argument("--events-output", type=Path)
    args = parser.parse_args()
    if not args.allow_isolated_root or os.geteuid() != 0:
        parser.error("requires root and explicit --allow-isolated-root (invoke within unshare -n)")
    if os.stat("/proc/1/ns/net").st_ino == os.stat("/proc/self/ns/net").st_ino:
        parser.error("refusing to run: this process shares PID 1's network namespace")
    for path in (args.city_db, args.asn_db):
        if not Path(path).is_file():
            parser.error(f"missing geolocation database: {path}")

    run("ip", "link", "add", "watch-a", "type", "veth", "peer", "name", "watch-b")
    run("ip", "link", "set", "lo", "up")
    run("ip", "link", "set", "watch-a", "up")
    run("ip", "link", "set", "watch-b", "up")
    watch_mac = interface_mac("watch-a")
    sender_mac = interface_mac("watch-b")
    proc = subprocess.Popen([sys.executable, str(COLLECTOR), "--interface", "watch-a", "--city-db", args.city_db,
                             "--asn-db", args.asn_db, "--health-interval", "0.25"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    records = []
    try:
        time.sleep(0.25)
        sender = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
        sender.bind(("watch-b", 0))
        ipv4src, ipv4dst = "1.1.1.1", "192.0.2.11"
        syn = ethernet(watch_mac, sender_mac, 0x0800, ipv4(ipv4src, ipv4dst, 6, tcp(40231, 443, 2)))
        udp_body = struct.pack("!HHHH", 40232, 53, 8 + len(SECRET), 0) + SECRET
        udp = ethernet(watch_mac, sender_mac, 0x0800, ipv4("1.0.0.1", ipv4dst, 17, udp_body))
        ack = ethernet(watch_mac, sender_mac, 0x0800, ipv4("1.1.1.1", ipv4dst, 6, tcp(40233, 443, 0x10)))
        v6tcp = struct.pack("!HHIIHHHH", 40234, 443, 1, 0, (5 << 12) | 2, 4096, 0, 0)
        v6ext = bytes([6, 0, 0, 0, 0, 0, 0, 0])
        v6 = ethernet(watch_mac, sender_mac, 0x86dd,
                      ipv6("2606:4700:4700::1111", "2001:db8::11", 0, v6ext + v6tcp))
        # Real >256-byte datagrams exercise snaplen handling; several observations
        # deliberately share a source IP so distinct-IP and flow stats differ.
        large_payload = SECRET + bytes(1024)
        large_body = struct.pack("!HHHH", 40235, 123, 8 + len(large_payload), 0) + large_payload
        large4 = ethernet(watch_mac, sender_mac, 0x0800, ipv4(ipv4src, ipv4dst, 17, large_body))
        large6 = ethernet(watch_mac, sender_mac, 0x86dd,
                          ipv6("2606:4700:4700::1111", "2001:db8::11", 17, large_body))
        echo = ethernet(watch_mac, sender_mac, 0x0800,
                        ipv4(ipv4src, ipv4dst, 1, struct.pack("!BBHHH", 8, 0, 0, 42, 1) + SECRET))
        for packet in (syn, syn, udp, ack, v6, large4, large6, echo):
            sender.send(packet)
        sender.close()
        time.sleep(0.8)
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise RuntimeError("collector did not stop within bounded smoke-test timeout")
        if proc.returncode not in (0, -15):
            raise RuntimeError(f"collector exited unexpectedly: {stderr.strip()}")
        records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        attempts = [r for r in records if r.get("kind") == "attempt"]
        health = [r for r in records if r.get("kind") == "health"]
        assert len(attempts) == 6, f"expected TCP/UDP/IPv6/ICMP attempts including large datagrams, got {len(attempts)}"
        by_type = {r["attempt_type"]: r for r in attempts}
        assert set(by_type) == {"tcp_syn", "udp_flow", "icmp_echo"}
        assert sum(r["ip_version"] == 6 for r in attempts) == 2
        assert len({r["src_ip"] for r in attempts}) == 3
        assert sum(r["dst_port"] == 123 for r in attempts) == 2, "large IPv4/IPv6 UDP metadata missing"
        tcp4 = next(r for r in attempts if r["attempt_type"] == "tcp_syn" and r["ip_version"] == 4)
        assert tcp4["src_ip"] == ipv4src and tcp4["dst_port"] == 443
        assert tcp4["decision"] == "unknown"
        for event in attempts:
            assert event["interface"] == "watch-a"
            assert all(field in event for field in ("geo_status", "country", "country_code", "city", "latitude", "longitude", "asn", "as_org"))
        assert health and health[0]["interface"] == "watch-a"
        assert health[-1]["tcp_ack_seen"] == 0, "IPv4 ACK-only packet passed the kernel filter"
        assert health[-1]["packets_deduplicated"] == 1, f"duplicate accounting mismatch: {health[-1]}"
        safe_output = json.dumps(records)
        assert SECRET.decode() not in safe_output and "payload" not in safe_output.lower()
        if args.events_output:
            args.events_output.parent.mkdir(parents=True, exist_ok=True)
            args.events_output.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
        print(f"isolated capture smoke PASS: {len(attempts)} attempts, {len(health)} health records, duplicate/ACK/privacy checks")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()
