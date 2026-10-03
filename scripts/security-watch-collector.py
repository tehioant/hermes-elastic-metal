#!/usr/bin/env python3
"""Metadata-only inbound network attempt collector."""
import argparse
import ctypes
import ctypes.util
import ipaddress
import json
import os
import socket
import struct
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone


class BoundedDeduplicator:
    def __init__(self, window=30.0, capacity=65536):
        self.window, self.capacity = window, capacity
        self.seen = OrderedDict()
        self.evictions = 0

    def first(self, event, now=None):
        now = time.monotonic() if now is None else now
        key = tuple(event.get(k) for k in ("src_ip", "dst_ip", "src_port", "dst_port", "protocol", "attempt_type", "interface"))
        previous = self.seen.get(key)
        if previous is not None and now - previous < self.window:
            self.seen.move_to_end(key)
            return False
        self.seen[key] = now
        self.seen.move_to_end(key)
        while len(self.seen) > self.capacity:
            self.seen.popitem(last=False)
            self.evictions += 1
        return True


def _event(src, dst, sport, dport, proto, attempt_type, interface, ttl, now):
    src_ip, dst_ip = ipaddress.ip_address(src), ipaddress.ip_address(dst)
    return {
        "schema": 1, "kind": "attempt", "ts": now, "interface": interface,
        "src_ip": str(src_ip), "dst_ip": str(dst_ip), "src_port": sport, "dst_port": dport,
        "protocol": proto, "attempt_type": attempt_type,
        "ip_version": src_ip.version, "ttl": ttl, "decision": "unknown",
        "geo_status": "private" if not src_ip.is_global else "unmapped", "country": "Unknown",
        "country_code": "", "city": "", "latitude": None, "longitude": None,
        "asn": 0, "as_org": "",
    }


def _is_ipv4_ack_only(frame):
    """Detect ACK-only IPv4 TCP if an ostensibly filtered frame reaches userspace."""
    if len(frame) < 34 or struct.unpack_from("!H", frame, 12)[0] != 0x0800:
        return False
    offset = 14
    if frame[offset] >> 4 != 4:
        return False
    ihl = (frame[offset] & 15) * 4
    if ihl < 20 or len(frame) < offset + ihl + 20 or frame[offset + 9] != 6:
        return False
    tcp = offset + ihl
    flags = frame[tcp + 13]
    return bool(flags & 0x10) and not bool(flags & 0x02)


def parse_frame(frame, interface, now):
    """Parse one Ethernet frame; return a safe metadata event or None."""
    if len(frame) < 14:
        return None
    ethertype = struct.unpack_from("!H", frame, 12)[0]
    offset = 14
    if ethertype == 0x8100 or ethertype == 0x88a8:
        if len(frame) < 18:
            return None
        ethertype = struct.unpack_from("!H", frame, 16)[0]
        offset = 18
    if ethertype == 0x0800:
        if len(frame) < offset + 20:
            return None
        version_ihl = frame[offset]
        ihl = (version_ihl & 15) * 4
        total = struct.unpack_from("!H", frame, offset + 2)[0]
        if version_ihl >> 4 != 4 or ihl < 20 or total < ihl or len(frame) < offset + ihl:
            return None
        frag = struct.unpack_from("!H", frame, offset + 6)[0]
        if frag & 0x1fff:
            return None
        proto, ttl = frame[offset + 9], frame[offset + 8]
        src, dst = frame[offset + 12:offset + 16], frame[offset + 16:offset + 20]
        l4 = offset + ihl
        end = offset + total
        ip_version = 4
    elif ethertype == 0x86dd:
        if len(frame) < offset + 40 or frame[offset] >> 4 != 6:
            return None
        payload_len = struct.unpack_from("!H", frame, offset + 4)[0]
        end = offset + 40 + payload_len
        ttl = frame[offset + 7]
        src, dst = frame[offset + 8:offset + 24], frame[offset + 24:offset + 40]
        proto, l4, ip_version = frame[offset + 6], offset + 40, 6
        for _ in range(8):
            if proto in (0, 43, 60, 135):
                if len(frame) < l4 + 2 or end < l4 + 2:
                    return None
                next_proto, ext_len = frame[l4], (frame[l4 + 1] + 1) * 8
                if len(frame) < l4 + ext_len or end < l4 + ext_len:
                    return None
                proto, l4 = next_proto, l4 + ext_len
            elif proto == 44:
                if len(frame) < l4 + 8 or end < l4 + 8:
                    return None
                next_proto, fragment = frame[l4], struct.unpack_from("!H", frame, l4 + 2)[0]
                if fragment & 0xfff8:
                    return None
                proto, l4 = next_proto, l4 + 8
            elif proto == 51:
                if len(frame) < l4 + 2 or end < l4 + 2:
                    return None
                next_proto, ext_len = frame[l4], (frame[l4 + 1] + 2) * 4
                if len(frame) < l4 + ext_len or end < l4 + ext_len:
                    return None
                proto, l4 = next_proto, l4 + ext_len
            else:
                break
        else:
            return None
    else:
        return None
    if proto == 6:
        if len(frame) < l4 + 20 or end < l4 + 20:
            return None
        sport, dport = struct.unpack_from("!HH", frame, l4)
        tcp_hlen = (frame[l4 + 12] >> 4) * 4
        flags = frame[l4 + 13]
        if tcp_hlen < 20 or len(frame) < l4 + tcp_hlen or end < l4 + tcp_hlen or not (flags & 0x02) or (flags & 0x10):
            return None
        proto_name, kind = "TCP", "tcp_syn"
    elif proto == 17:
        if len(frame) < l4 + 8 or end < l4 + 8:
            return None
        sport, dport = struct.unpack_from("!HH", frame, l4)
        udp_len = struct.unpack_from("!H", frame, l4 + 4)[0]
        if udp_len < 8 or l4 + udp_len > end:
            return None
        proto_name, kind = "UDP", "udp_flow"
    elif proto in (1, 58):
        echo_request_type = 8 if proto == 1 else 128
        if len(frame) < l4 + 8 or end < l4 + 8 or frame[l4] != echo_request_type or frame[l4 + 1] != 0:
            return None
        sport, dport, proto_name, kind = 0, 0, "ICMP", "icmp_echo"
    else:
        return None
    return _event(src, dst, sport, dport, proto_name, kind, interface, ttl, now)


class GeoLookup:
    def __init__(self, city_path, asn_path, capacity=32768):
        try:
            import maxminddb
        except ImportError as exc:
            raise RuntimeError("distro maxminddb Python module is required") from exc
        self.maxminddb = maxminddb
        self.paths = {"city": city_path, "asn": asn_path}
        self.readers = {}
        self.identity = {}
        self.cache = OrderedDict()
        self.capacity = capacity
        self.evictions = 0
        self.reopen()

    def reopen(self):
        for kind, path in self.paths.items():
            st = os.stat(path)
            identity = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
            if self.identity.get(kind) != identity:
                fresh = self.maxminddb.open_database(path)
                previous = self.readers.get(kind)
                self.readers[kind], self.identity[kind] = fresh, identity
                if previous:
                    previous.close()
                self.cache.clear()

    def lookup(self, address):
        key = str(ipaddress.ip_address(address))
        if key in self.cache:
            self.cache.move_to_end(key)
            return dict(self.cache[key])
        ip = ipaddress.ip_address(key)
        result = {"geo_status": "private" if not ip.is_global else "unmapped", "country": "Unknown",
                  "country_code": "", "city": "", "latitude": None, "longitude": None, "asn": 0, "as_org": ""}
        if ip.is_global:
            try:
                data = self.readers["city"].get(key) or {}
                country = data.get("country", {}) or data.get("registered_country", {})
                result.update(geo_status="known" if country else "unmapped",
                              country=country.get("names", {}).get("en", "Unknown"),
                              country_code=country.get("iso_code", ""),
                              city=(data.get("city", {}).get("names", {}) or {}).get("en", ""),
                              latitude=(data.get("location") or {}).get("latitude"),
                              longitude=(data.get("location") or {}).get("longitude"))
            except (OSError, ValueError):
                pass
            try:
                asn = self.readers["asn"].get(key) or {}
                result.update(asn=asn.get("autonomous_system_number", 0) or 0,
                              as_org=asn.get("autonomous_system_organization", "") or "")
            except (OSError, ValueError):
                pass
        self.cache[key] = dict(result)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
            self.evictions += 1
        return result

    @staticmethod
    def validate(city_path, asn_path):
        import maxminddb
        result = {}
        for name, path in (("city", city_path), ("asn", asn_path)):
            reader = maxminddb.open_database(path)
            result[name] = {"database_type": reader.metadata().database_type,
                            "build_epoch": reader.metadata().build_epoch}
            reader.close()
        return result


def _attach_filter(sock):
    """Attach a libpcap-compiled BPF; permit all IPv6 and only candidate v4 traffic."""
    libname = ctypes.util.find_library("pcap")
    if not libname:
        raise RuntimeError("libpcap is required")
    pcap = ctypes.CDLL(libname, use_errno=True)
    pcap.pcap_open_dead.argtypes = [ctypes.c_int, ctypes.c_int]
    pcap.pcap_open_dead.restype = ctypes.c_void_p
    class BpfInsn(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
    class BpfProgram(ctypes.Structure):
        _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.POINTER(BpfInsn))]
    dead = pcap.pcap_open_dead(1, 256)
    if not dead:
        raise RuntimeError("libpcap pcap_open_dead failed")
    program = BpfProgram()
    error = ctypes.create_string_buffer(256)
    pcap.pcap_close.argtypes = [ctypes.c_void_p]
    pcap.pcap_close.restype = None
    pcap.pcap_freecode.argtypes = [ctypes.POINTER(BpfProgram)]
    pcap.pcap_freecode.restype = None
    pcap.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(BpfProgram), ctypes.c_char_p, ctypes.c_int, ctypes.c_uint32]
    pcap.pcap_compile.restype = ctypes.c_int
    expr = b"ip6 or (ip and ((tcp and (tcp[13] & 2 != 0) and (tcp[13] & 16 = 0)) or udp or icmp))"
    if pcap.pcap_compile(dead, ctypes.byref(program), expr, 1, 0xffffffff) != 0:
        pcap.pcap_close(dead)
        raise RuntimeError("libpcap rejected inbound capture filter")
    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]
    filters = ctypes.cast(program.bf_insns, ctypes.POINTER(SockFilter))
    fprog = SockFprog(program.bf_len, filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setsockopt(sock.fileno(), socket.SOL_SOCKET, 26, ctypes.byref(fprog), ctypes.sizeof(fprog)) != 0:
        pcap.pcap_freecode(ctypes.byref(program)); pcap.pcap_close(dead)
        raise OSError(ctypes.get_errno(), "SO_ATTACH_FILTER failed")
    pcap.pcap_freecode(ctypes.byref(program)); pcap.pcap_close(dead)


def _emit(event):
    sys.stdout.write(json.dumps(event, separators=(",", ":"), ensure_ascii=True) + "\n")
    sys.stdout.flush()


def run(args):
    geo = GeoLookup(args.city_db, args.asn_db)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    try:
        sock.bind((args.interface, 0))
        sock.setsockopt(263, 23, 1)  # PACKET_IGNORE_OUTGOING
        _attach_filter(sock)
        sock.settimeout(min(args.health_interval, 1.0))
        dedup = BoundedDeduplicator(args.dedup_seconds)
        counts = dict(packets_received=0, packets_dropped=0, events_emitted=0, events_limited=0,
                      packets_deduplicated=0, parse_errors=0, frames_unclassified=0, tcp_ack_seen=0, cache_evictions=0)
        start, next_health, rate_start, rate_count = time.monotonic(), 0.0, time.monotonic(), 0
        def health():
            stats = sock.getsockopt(263, 6, 8)  # PACKET_STATISTICS, resets interval counters
            _, kernel_drops = struct.unpack("=II", stats)
            counts["packets_dropped"] += kernel_drops
            geo.reopen()
            counts["cache_evictions"] = geo.evictions + dedup.evictions
            _emit({"schema": 1, "kind": "health", "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                   "interface": args.interface, **counts, "coverage": "inbound-only selected interface; Ethernet; IPv4 candidate TCP-SYN/UDP/ICMP and IPv6 to parser; IPv6 extension chain limited to 8; snaplen 256",
                   "started_ts": datetime.fromtimestamp(time.time() - (time.monotonic()-start), timezone.utc).isoformat().replace("+00:00", "Z")})
        health()
        next_health = time.monotonic() + args.health_interval
        def check_health_deadline():
            nonlocal next_health
            if time.monotonic() >= next_health:
                health()
                next_health = time.monotonic() + args.health_interval
        while True:
            try:
                frame, address = sock.recvfrom(256)
                counts["packets_received"] += 1
                if len(address) > 2 and address[2] == 4:  # PACKET_OUTGOING
                    check_health_deadline()
                    continue
                if _is_ipv4_ack_only(frame):
                    counts["tcp_ack_seen"] += 1
                event = parse_frame(frame, args.interface, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                if event is None:
                    counts["frames_unclassified"] += 1
                    check_health_deadline()
                    continue
                if not dedup.first(event):
                    counts["packets_deduplicated"] += 1
                    check_health_deadline()
                    continue
                event.update(geo.lookup(event["src_ip"]))
                now = time.monotonic()
                if now - rate_start >= 1:
                    rate_start, rate_count = now, 0
                if rate_count >= args.max_events_per_second:
                    counts["events_limited"] += 1
                    check_health_deadline()
                    continue
                rate_count += 1
                _emit(event)
                counts["events_emitted"] += 1
            except socket.timeout:
                pass
            except OSError:
                counts["parse_errors"] += 1
            check_health_deadline()
    finally:
        sock.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface")
    parser.add_argument("--city-db")
    parser.add_argument("--asn-db")
    parser.add_argument("--dedup-seconds", type=float, default=30)
    parser.add_argument("--max-events-per-second", type=int, default=200)
    parser.add_argument("--health-interval", type=float, default=30)
    parser.add_argument("--validate-databases", nargs=2, metavar=("CITY", "ASN"))
    args = parser.parse_args(argv)
    if args.validate_databases:
        print(json.dumps(GeoLookup.validate(*args.validate_databases), separators=(",", ":")))
        return 0
    if not args.interface or not args.city_db or not args.asn_db:
        parser.error("--interface, --city-db and --asn-db are required")
    if args.dedup_seconds < 0 or args.max_events_per_second < 1 or args.health_interval <= 0:
        parser.error("dedup must be nonnegative; rate and health interval must be positive")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
