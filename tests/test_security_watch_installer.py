import hashlib
import gzip
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-security-watch.sh"
UPDATER = ROOT / "scripts/update-security-watch-geoip.py"


class SecurityWatchInstallerTest(unittest.TestCase):
    def run_preflight(self, root, *args):
        return subprocess.run(
            ["bash", str(INSTALLER), "--preflight", *args],
            env=os.environ | {"SECURITY_WATCH_ROOT": str(root)},
            text=True, capture_output=True,
        )

    def test_preflight_requires_explicit_interface_and_valid_parent_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--interface", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_preflight_requires_parent_stack_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(directory, "--interface", "eth0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("security-dashboard ownership marker", result.stderr)

    def test_preflight_accepts_owned_parent_and_empty_watch_targets_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "var/lib/hermes-security-dashboard/managed-by-hermes"
            marker.parent.mkdir(parents=True)
            marker.write_text("hermes-security-dashboard-managed-v1\n")
            before = sorted(str(p.relative_to(directory)) for p in Path(directory).rglob("*"))
            result = self.run_preflight(directory, "--interface", "enp2s0")
            after = sorted(str(p.relative_to(directory)) for p in Path(directory).rglob("*"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(after, before)

    def test_preflight_refuses_symlink_watch_target_even_with_watch_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "var/lib/hermes-security-dashboard"
            parent.mkdir(parents=True)
            (parent / "managed-by-hermes").write_text("hermes-security-dashboard-managed-v1\n")
            state = root / "var/lib/hermes-security-watch"
            state.mkdir(parents=True)
            (state / "managed-by-hermes").write_text("hermes-security-watch-managed-v1\n")
            (root / "etc/hermes-security-watch").mkdir(parents=True)
            victim = root / "operator-file"
            victim.write_text("keep me")
            (root / "etc/hermes-security-watch/collector.env").symlink_to(victim)
            result = self.run_preflight(root, "--interface", "eth0")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(victim.read_text(), "keep me")

    def test_preflight_rejects_wrong_watch_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "var/lib/hermes-security-dashboard"
            parent.mkdir(parents=True)
            (parent / "managed-by-hermes").write_text("hermes-security-dashboard-managed-v1\n")
            state = root / "var/lib/hermes-security-watch"
            state.mkdir()
            marker = state / "managed-by-hermes"
            marker.write_text("operator-owned\n")
            result = self.run_preflight(root, "--interface", "eth0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ownership marker is invalid", result.stderr)
            self.assertEqual(marker.read_text(), "operator-owned\n")

    def test_fake_root_install_is_idempotent_and_preserves_original_stack(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as tools:
            root = Path(directory)
            tool_dir = Path(tools)
            marker = root / "var/lib/hermes-security-dashboard/managed-by-hermes"
            marker.parent.mkdir(parents=True)
            marker.write_text("hermes-security-dashboard-managed-v1\n")
            alloy = root / "etc/hermes-security-dashboard/config.alloy"
            alloy.parent.mkdir(parents=True)
            original = subprocess.run(["git", "show", "origin/main:config/security-dashboard/config.alloy"],
                                      cwd=ROOT, check=True, text=True, capture_output=True).stdout
            alloy.write_text(original)
            old_dashboard = root / "etc/hermes-security-dashboard/dashboards/operator.json"
            old_dashboard.parent.mkdir(parents=True)
            old_dashboard.write_text("operator-owned dashboard")
            state = root / "var/lib/hermes-security-watch"
            state.mkdir(parents=True)
            (state / "managed-by-hermes").write_text("hermes-security-watch-managed-v1\n")
            (state / "alloy-origin.sha256").write_text(hashlib.sha256(original.encode()).hexdigest() + "\n")
            geo = state / "geoip"
            geo.mkdir()
            (geo / "city.mmdb").write_bytes(b"test city database")
            (geo / "asn.mmdb").write_bytes(b"test asn database")

            install_stub = tool_dir / "install"
            install_stub.write_text('''#!/bin/bash
set -e
mode=
group=root
mkdir_only=0
while (($#)); do
  case "$1" in
    -d) mkdir_only=1; shift ;;
    -D) shift ;;
    -o) shift 2 ;;
    -g) group="$2"; shift 2 ;;
    -m) mode="$2"; shift 2 ;;
    *) break ;;
  esac
done
if (( mkdir_only )); then
  mkdir -p "$@"
  [[ -z "$mode" ]] || chmod "$mode" "$@"
  for dst in "$@"; do printf '%s %s\n' "$group" "$dst" >> "${SECURITY_WATCH_TEST_LOG:?}"; done
else
  src="${@: -2:1}"; dst="${@: -1}"
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  [[ -z "$mode" ]] || chmod "$mode" "$dst"
fi
''')
            install_stub.chmod(0o755)
            chown_stub = tool_dir / "chown"
            chown_stub.write_text("#!/bin/sh\nexit 0\n")
            chown_stub.chmod(0o755)
            py_stub = tool_dir / "python3"
            py_stub.write_text("#!/bin/sh\nexit 0\n")
            py_stub.chmod(0o755)
            env = os.environ | {
                "SECURITY_WATCH_ROOT": str(root), "SECURITY_WATCH_TEST_MODE": "1",
                "PATH": f"{tool_dir}:{os.environ['PATH']}",
                "SECURITY_WATCH_TEST_LOG": str(root / "install.log"),
            }
            command = ["bash", str(INSTALLER), "--interface", "eno1"]
            first = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            installed_alloy = alloy.read_text()
            self.assertIn("loki.source", installed_alloy)
            self.assertEqual((root / "etc/hermes-security-watch/collector.env").read_text(),
                             f"WATCH_INTERFACE=eno1\nCITY_DB={geo}/city.mmdb\nASN_DB={geo}/asn.mmdb\n")
            self.assertEqual((root / "var/lib/hermes-security-watch/managed-by-hermes").read_text(),
                             "hermes-security-watch-managed-v1\n")
            self.assertEqual(((root / "var/lib/hermes-security-watch/managed-by-hermes").stat().st_mode & 0o777), 0o600)
            self.assertEqual((root / "etc/hermes-security-dashboard/config.alloy").read_text(),
                             (ROOT / "config/security-dashboard/config.alloy").read_text())
            watch_provider = root / "etc/hermes-security-dashboard/provisioning/dashboards/watch.yaml"
            self.assertIn("/etc/hermes-security-dashboard/watch-dashboards", watch_provider.read_text())
            self.assertTrue((root / "etc/hermes-security-dashboard/watch-dashboards/server-watch.json").is_file())
            self.assertEqual((state.stat().st_mode & 0o777), 0o750)
            library_dir = root / "usr/local/lib/hermes-security-watch"
            self.assertEqual((library_dir.stat().st_mode & 0o777), 0o750)
            group_requests = (root / "install.log").read_text()
            self.assertIn(f"hermes-watch {state}", group_requests)
            self.assertIn(f"hermes-watch {library_dir}", group_requests)
            self.assertEqual(old_dashboard.read_text(), "operator-owned dashboard")
            env_file = root / "etc/hermes-security-watch/collector.env"
            env_file.chmod(0o666)
            unit_file = root / "etc/systemd/system/hermes-security-watch.service"
            unit_file.chmod(0o600)
            second = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(alloy.read_text(), installed_alloy)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(unit_file.stat().st_mode & 0o777, 0o644)

    def test_install_source_updates_restart_collector_and_uses_direct_dashboard_path(self):
        installer = INSTALLER.read_text()
        self.assertIn('local collector_code_changed=0', installer)
        self.assertIn('collector_code_changed=1', installer)
        self.assertIn('collector_unit_changed || collector_config_changed || collector_code_changed', installer)
        self.assertNotIn("sed 's|/etc/hermes-security-dashboard/dashboards/server-watch", installer)

    def test_installer_requires_active_collector_stack_before_mutating(self):
        installer = INSTALLER.read_text()
        self.assertIn('hermes-security-alloy.service', installer)
        self.assertIn('hermes-security-grafana.service', installer)
        self.assertIn('hermes-security-loki.service', installer)
        self.assertIn('prerequisite services must be active', installer)

    def test_updater_keeps_live_database_paths_present_during_promotion(self):
        import importlib.util
        from unittest import mock

        spec = importlib.util.spec_from_file_location("watch_geoip_updater", UPDATER)
        assert spec is not None and spec.loader is not None
        updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(updater)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "city.mmdb").write_bytes(b"old-city")
            (data_dir / "asn.mmdb").write_bytes(b"old-asn")
            calls = []

            def fake_download(url, destination):
                destination.write_bytes(gzip.compress(b"compressed"))

            original_replace = os.replace

            def watched_replace(source, target):
                target_path = Path(target)
                if target_path.parent == data_dir and target_path.name in {"city.mmdb", "asn.mmdb"}:
                    self.assertTrue(target_path.exists(), f"live path vanished before atomic replacement: {target_path}")
                    calls.append(target_path.name)
                return original_replace(source, target)

            with mock.patch.object(updater, "download", side_effect=fake_download), \
                 mock.patch.object(updater, "validate_mmdb"), \
                 mock.patch.object(updater.os, "replace", side_effect=watched_replace):
                updater.run(data_dir, "2026-10")
            self.assertEqual(calls, ["city.mmdb", "asn.mmdb"])
            self.assertEqual((data_dir / "city.mmdb").read_bytes(), b"compressed")
            self.assertEqual((data_dir / "asn.mmdb").read_bytes(), b"compressed")

    def test_updater_rolls_back_city_when_asn_promotion_fails(self):
        import importlib.util
        from unittest import mock

        spec = importlib.util.spec_from_file_location("watch_geoip_updater_rollback", UPDATER)
        assert spec is not None and spec.loader is not None
        updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(updater)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            originals = {"city.mmdb": b"old-city", "asn.mmdb": b"old-asn", "manifest.json": b"old-manifest\n"}
            for name, content in originals.items():
                (data_dir / name).write_bytes(content)

            def fake_download(url, destination):
                destination.write_bytes(gzip.compress(b"new-database"))

            original_replace = os.replace

            def fail_asn_promotion(source, target):
                if Path(source).name == "asn.mmdb" and Path(target) == data_dir / "asn.mmdb":
                    raise OSError("injected ASN promotion failure")
                return original_replace(source, target)

            with mock.patch.object(updater, "download", side_effect=fake_download), \
                 mock.patch.object(updater, "validate_mmdb"), \
                 mock.patch.object(updater.os, "replace", side_effect=fail_asn_promotion):
                with self.assertRaisesRegex(OSError, "injected ASN promotion failure"):
                    updater.run(data_dir, "2026-10")
            for name, content in originals.items():
                self.assertEqual((data_dir / name).read_bytes(), content)
            self.assertFalse(list(data_dir.glob(".*.last-good")))

    def test_preflight_rejects_modified_managed_alloy_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "var/lib/hermes-security-dashboard/managed-by-hermes"
            marker.parent.mkdir(parents=True)
            marker.write_text("hermes-security-dashboard-managed-v1\n")
            watch_marker = root / "var/lib/hermes-security-watch/managed-by-hermes"
            watch_marker.parent.mkdir(parents=True)
            watch_marker.write_text("hermes-security-watch-managed-v1\n")
            alloy = root / "etc/hermes-security-dashboard/config.alloy"
            alloy.parent.mkdir(parents=True)
            alloy.write_text("operator-edited Alloy config\n")
            result = self.run_preflight(root, "--interface", "eth0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("drifted", result.stderr)
            self.assertEqual(alloy.read_text(), "operator-edited Alloy config\n")

    def test_preflight_rejects_invalid_interface_name(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "var/lib/hermes-security-dashboard/managed-by-hermes"
            marker.parent.mkdir(parents=True)
            marker.write_text("hermes-security-dashboard-managed-v1\n")
            result = self.run_preflight(directory, "--interface", "eth0;touch")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("interface", result.stderr.lower())

    def test_updater_rejects_non_calendar_month_before_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["python3", str(UPDATER), "--month", "2026-13", "--directory", directory],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_units_are_monthly_persistent_and_privilege_bounded(self):
        updater = (ROOT / "systemd/hermes-security-watch-geoip-update.timer").read_text()
        service = (ROOT / "systemd/hermes-security-watch-geoip-update.service").read_text()
        collector = (ROOT / "systemd/hermes-security-watch.service").read_text()
        self.assertIn("OnCalendar=*-*-03 04:10:00 UTC", updater)
        self.assertIn("Persistent=true", updater)
        self.assertIn("RandomizedDelaySec=2h", updater)
        self.assertIn("CapabilityBoundingSet=", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("CAP_NET_RAW", collector)
        self.assertIn("AF_PACKET", collector)
        self.assertIn("MemoryMax=256M", collector)
        self.assertIn("CPUQuota=30%", collector)


if __name__ == "__main__":
    unittest.main()
