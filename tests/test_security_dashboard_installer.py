import grp
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-security-dashboard.sh"


class SecurityDashboardInstallerTest(unittest.TestCase):
    def test_preflight_refuses_an_unmanaged_existing_unit_without_touching_it(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_root = Path(directory)
            unit = fake_root / "etc/systemd/system/hermes-security-loki.service"
            unit.parent.mkdir(parents=True)
            unit.write_text("operator-owned unit\n")
            env = os.environ | {"SECURITY_DASHBOARD_ROOT": str(fake_root)}
            result = subprocess.run(["bash", str(INSTALLER), "--preflight"], env=env,
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("existing security-dashboard state is unmanaged", result.stderr)
            self.assertEqual(unit.read_text(), "operator-owned unit\n")

    def test_preflight_refuses_unmanaged_loki_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_root = Path(directory)
            binary = fake_root / "usr/local/bin/hermes-security-loki"
            binary.parent.mkdir(parents=True)
            binary.write_text("operator-owned binary\n")
            result = subprocess.run(["bash", str(INSTALLER), "--preflight"],
                                    env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(binary.read_text(), "operator-owned binary\n")

    def test_non_preflight_rejects_test_root_before_mutating_host(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["bash", str(INSTALLER)],
                                    env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("test root is only supported for --preflight", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_new_service_refuses_an_occupied_port(self):
        with tempfile.TemporaryDirectory() as directory:
            command = f'''source "{INSTALLER}" --preflight
ss() {{ printf 'LISTEN 0 4096 127.0.0.1:3100 0.0.0.0:*\\n'; }}
systemctl() {{ return 1; }}
check_port_available 3100 hermes-security-loki.service
'''
            result = subprocess.run(["bash", "-c", command],
                                    env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("port 3100 is already in use", result.stderr)

    def test_listener_verification_refuses_public_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            command = f'''source "{INSTALLER}" --preflight
ss() {{ printf 'LISTEN 0 4096 0.0.0.0:3100 0.0.0.0:*\\n'; }}
verify_listener 3100
'''
            result = subprocess.run(["bash", "-c", command],
                                    env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("listener is not confined to loopback", result.stderr)

    def test_install_if_changed_sets_and_repairs_root_service_group_metadata(self):
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            self.skipTest("requires noninteractive sudo for root-owned temporary-file metadata")
        group = grp.getgrgid(os.getgid()).gr_name
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "destination"
            source.write_text("config contents\\n")
            command = f'''source "{INSTALLER}" --preflight
install_if_changed "{source}" "{destination}" 0640 {group}
'''
            first = subprocess.run(["sudo", "-n", "bash", "-c", command],
                                   env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                   text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(destination.read_text(), "config contents\\n")
            self.assertEqual(destination.stat().st_uid, 0)
            self.assertEqual(destination.stat().st_gid,
                             os.getgid())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o640)

            unchanged = subprocess.run(["sudo", "-n", "bash", "-c", command],
                                       env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                       text=True, capture_output=True)
            self.assertEqual(unchanged.returncode, 1, unchanged.stderr)
            subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(destination)],
                           check=True)
            subprocess.run(["sudo", "-n", "chmod", "0666", str(destination)], check=True)
            repaired = subprocess.run(["sudo", "-n", "bash", "-c", command],
                                      env=os.environ | {"SECURITY_DASHBOARD_ROOT": directory},
                                      text=True, capture_output=True)
            self.assertEqual(repaired.returncode, 1, repaired.stderr)
            self.assertEqual(destination.stat().st_uid, 0)
            self.assertEqual(destination.stat().st_gid,
                             os.getgid())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o640)

    def test_preflight_allows_empty_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ | {"SECURITY_DASHBOARD_ROOT": directory}
            result = subprocess.run(["bash", str(INSTALLER), "--preflight"], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
