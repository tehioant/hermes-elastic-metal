"""Behavioral tests for the metadata-only packet collector."""
import importlib.util
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "security-watch-collector.py"
spec = importlib.util.spec_from_file_location("security_watch_collector", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def ipv4_tcp(src="198.51.100.9", dst="192.0.2.4", sport=43123, dport=443, flags=0x02):
    tcp = struct.pack("!HHIIHHHH", sport, dport, 1, 0, (5 << 12) | flags, 4096, 0, 0)
    src_b = bytes(map(int, src.split(".")))
    dst_b = bytes(map(int, dst.split(".")))
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 7, 0, 61, 6, 0, src_b, dst_b)
    return bytes.fromhex("00112233445566778899aabb0800") + ip + tcp


def ipv4_udp(body=b""):
    udp = struct.pack("!HHHH", 43123, 53, 8 + len(body), 0) + body
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 7, 0, 61, 17, 0,
                     bytes([198, 51, 100, 9]), bytes([192, 0, 2, 4]))
    return bytes.fromhex("00112233445566778899aabb0800") + ip + udp


class PacketParsingTests(unittest.TestCase):
    def test_ipv4_syn_is_emitted_as_metadata_without_packet_bytes(self):
        frame = ipv4_tcp()
        event = collector.parse_frame(frame, "eth-test", now="2026-10-02T12:00:00Z")
        self.assertEqual(event["kind"], "attempt")
        self.assertEqual((event["src_ip"], event["dst_ip"], event["src_port"], event["dst_port"]),
                         ("198.51.100.9", "192.0.2.4", 43123, 443))
        self.assertEqual(event["attempt_type"], "tcp_syn")
        self.assertNotIn(frame.hex(), repr(event))

    def test_snaplen_truncated_ipv4_tcp_udp_and_ipv6_packets_keep_header_events(self):
        large4_tcp = ipv4_tcp() + bytes(400)
        large4_tcp = bytearray(large4_tcp)
        struct.pack_into("!H", large4_tcp, 14 + 2, len(large4_tcp) - 14)
        large4_udp = ipv4_udp(bytes(400))
        large4_udp = bytearray(large4_udp)
        struct.pack_into("!H", large4_udp, 14 + 2, len(large4_udp) - 14)

        tcp6 = struct.pack("!HHIIHHHH", 51234, 22, 1, 0, (5 << 12) | 2, 4096, 0, 0)
        large6 = bytes.fromhex("00112233445566778899aabb86dd") + struct.pack(
            "!IHBB16s16s", 6 << 28, len(tcp6) + 400, 6, 52,
            bytes.fromhex("20010db8000000000000000000000001"),
            bytes.fromhex("20010db8000000000000000000000002")) + tcp6 + bytes(400)
        for frame, kind in ((bytes(large4_tcp[:256]), "tcp_syn"),
                            (bytes(large4_udp[:256]), "udp_flow"),
                            (large6[:256], "tcp_syn")):
            with self.subTest(kind=kind):
                self.assertEqual(collector.parse_frame(frame, "eth-test", "now")["attempt_type"], kind)

    def test_truncated_required_headers_and_inconsistent_declared_lengths_are_rejected(self):
        frame = ipv4_tcp()
        self.assertIsNone(collector.parse_frame(frame[:14 + 20 + 12], "eth-test", "now"))
        frame6 = bytearray(bytes.fromhex("00112233445566778899aabb86dd") + struct.pack(
            "!IHBB16s16s", 6 << 28, 1, 0, 52, bytes(16), bytes(16)) + bytes([6, 255]))
        self.assertIsNone(collector.parse_frame(bytes(frame6), "eth-test", "now"))
        bad_udp = bytearray(ipv4_udp())
        struct.pack_into("!H", bad_udp, 14 + 20 + 4, 7)
        self.assertIsNone(collector.parse_frame(bytes(bad_udp), "eth-test", "now"))
        bad_udp = bytearray(ipv4_udp())
        struct.pack_into("!H", bad_udp, 14 + 20 + 4, 600)
        self.assertIsNone(collector.parse_frame(bytes(bad_udp), "eth-test", "now"))

    def test_ipv4_ack_only_is_not_an_attempt(self):
        self.assertIsNone(collector.parse_frame(ipv4_tcp(flags=0x10), "eth-test", "2026-10-02T12:00:00Z"))

    def test_ipv6_hop_by_hop_extension_precedes_tcp_syn(self):
        tcp = struct.pack("!HHIIHHHH", 51234, 22, 1, 0, (5 << 12) | 2, 4096, 0, 0)
        src = bytes.fromhex("20010db8000000000000000000000001")
        dst = bytes.fromhex("20010db8000000000000000000000002")
        ext = bytes([6, 0, 0, 0, 0, 0, 0, 0])
        ip = struct.pack("!IHBB16s16s", 6 << 28, len(ext) + len(tcp), 0, 52, src, dst)
        frame = bytes.fromhex("00112233445566778899aabb86dd") + ip + ext + tcp
        event = collector.parse_frame(frame, "eth-test", "2026-10-02T12:00:00Z")
        self.assertEqual(event["ip_version"], 6)
        self.assertEqual(event["src_port"], 51234)
        self.assertEqual(event["attempt_type"], "tcp_syn")

    def test_non_initial_ipv4_fragment_never_invents_ports(self):
        frame = bytearray(ipv4_tcp())
        struct.pack_into("!H", frame, 14 + 6, 1)
        self.assertIsNone(collector.parse_frame(bytes(frame), "eth-test", "2026-10-02T12:00:00Z"))

    def test_ipv4_icmp_echo_requires_request_type_and_zero_code(self):
        icmp = bytes([8, 1]) + bytes(6)
        src, dst = bytes([203, 0, 113, 8]), bytes([192, 0, 2, 8])
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 1, 0, 48, 1, 0, src, dst)
        frame = bytes.fromhex("00112233445566778899aabb0800") + ip + icmp
        self.assertIsNone(collector.parse_frame(frame, "eth-test", "2026-10-02T12:00:00Z"))

    def test_collector_cli_exposes_database_validation(self):
        result = __import__("subprocess").run(["python3", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--validate-databases", result.stdout)

    def test_deduplicator_collapses_retry_within_window(self):
        event = collector.parse_frame(ipv4_tcp(), "eth-test", "2026-10-02T12:00:00Z")
        dedup = collector.BoundedDeduplicator(window=30, capacity=2)
        self.assertTrue(dedup.first(event, now=100.0))
        self.assertFalse(dedup.first(event, now=129.0))
        self.assertTrue(dedup.first(event, now=130.1))

    def test_health_deadline_is_checked_after_every_early_continue_path(self):
        class FakeSocket:
            def __init__(self):
                self.frames = [(ipv4_tcp(), ("eth-test", 0, 0)),
                               (ipv4_tcp(), ("eth-test", 0, 0)),
                               (ipv4_tcp(), ("eth-test", 0, 4)),
                               (b"bad", ("eth-test", 0, 0)),
                               (ipv4_tcp(sport=43124), ("eth-test", 0, 0))]
                self.statistics = 0
            def bind(self, *_): pass
            def setsockopt(self, *_): pass
            def settimeout(self, *_): pass
            def fileno(self): return 1
            def getsockopt(self, *_):
                self.statistics += 1
                return struct.pack("=II", 0, 0)
            def recvfrom(self, _):
                clock[0] += 0.1
                if self.frames:
                    return self.frames.pop(0)
                raise KeyboardInterrupt
            def close(self): pass

        clock = [0.0]
        fake_socket = FakeSocket()
        geo = Mock()
        geo.lookup.return_value = {}
        geo.evictions = 0
        args = SimpleNamespace(city_db="city", asn_db="asn", interface="eth-test",
                               health_interval=0.25, dedup_seconds=30, max_events_per_second=1)
        with patch.object(collector, "GeoLookup", return_value=geo), \
             patch.object(collector.socket, "socket", return_value=fake_socket), \
             patch.object(collector, "_attach_filter"), \
             patch.object(collector.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(collector, "_emit"):
            with self.assertRaises(KeyboardInterrupt):
                collector.run(args)
        self.assertGreater(fake_socket.statistics, 1, "health statistics were not checked during sustained early continues")


if __name__ == "__main__":
    unittest.main()
