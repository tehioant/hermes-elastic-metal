"""Offline dependency trust-boundary checks; no installers or services run."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DependencyIntegrityTests(unittest.TestCase):
    def run_kickstart_fixture(self, payload, download_status=0, installer_status=0):
        expected = b"reviewed installer fixture\n"
        source = (ROOT / "scripts/install-netdata.sh").read_text()
        source = re.sub(r"(?m)^readonly KICKSTART_SHA256=.*$",
                        "readonly KICKSTART_SHA256=" + hashlib.sha256(expected).hexdigest(), source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "installer.sh"
            script.write_text(source)
            (root / "payload").write_bytes(payload)
            command = '''source "$1"
curl() { cp "$FIXTURE/payload" "${@: -1}"; return "$DOWNLOAD_STATUS"; }
bash() { printf '%s\\n' "$*" > "$FIXTURE/executed"; return "$INSTALLER_STATUS"; }
run_kickstart
'''
            result = subprocess.run(["bash", "-c", command, "test", str(script)],
                                    env=os.environ | {"FIXTURE": directory, "TMPDIR": directory,
                                                      "DOWNLOAD_STATUS": str(download_status),
                                                      "INSTALLER_STATUS": str(installer_status)},
                                    text=True, capture_output=True, timeout=10)
            executed = (root / "executed").read_text() if (root / "executed").exists() else None
            leftovers = sorted(p.name for p in root.iterdir() if p.name.startswith("tmp."))
            return result, executed, leftovers

    def test_netdata_rejects_checksum_mismatch_before_execution(self):
        result, executed, leftovers = self.run_kickstart_fixture(b"tampered installer\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(executed)
        self.assertEqual(leftovers, [])


    def test_netdata_accepts_only_reviewed_bytes_and_keeps_install_options(self):
        result, executed, leftovers = self.run_kickstart_fixture(b"reviewed installer fixture\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(executed, str)
        self.assertIn("--non-interactive --release-channel stable --native-only --auto-update", executed or "")
        self.assertEqual(leftovers, [])

    def test_netdata_rejects_failed_partial_download_before_execution(self):
        result, executed, leftovers = self.run_kickstart_fixture(b"reviewed installer fixture\n", download_status=22)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(executed)
        self.assertEqual(leftovers, [])

    def test_netdata_reports_installer_failure_and_cleans_download(self):
        result, executed, leftovers = self.run_kickstart_fixture(b"reviewed installer fixture\n", installer_status=17)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exit 17", result.stderr)
        self.assertIsNotNone(executed)
        self.assertEqual(leftovers, [])

    def test_netdata_download_has_immutable_commit_and_sha256(self):
        source = (ROOT / "scripts/install-netdata.sh").read_text()
        self.assertRegex(source, r"(?m)^readonly KICKSTART_COMMIT=[0-9a-f]{40}$")
        self.assertRegex(source, r"(?m)^readonly KICKSTART_SHA256=[0-9a-f]{64}$")
        self.assertIn("https://raw.githubusercontent.com/netdata/netdata/${KICKSTART_COMMIT}/packaging/installer/kickstart.sh", source)

    def test_github_cli_is_not_in_the_published_attestation_and_symlink_vulnerable_range(self):
        import tomllib
        tools = tomllib.loads((ROOT / ".mise.toml").read_text())["tools"]
        self.assertGreaterEqual(tuple(map(int, tools["gh"].split("."))), (2, 102, 0))

    def test_oauth2_proxy_uses_reviewed_credential_logging_security_release(self):
        source = (ROOT / "scripts/install-oauth2-proxy.sh").read_text()
        self.assertIn("readonly OAUTH2_PROXY_VERSION=v7.15.5\n", source)
        self.assertIn("readonly OAUTH2_PROXY_SHA256=f63f94bf72c5f46ab002a0a275aa8b3cf19b4d828aed08a13978cb9a62c3a1fd\n", source)
        self.assertLess(source.index("sha256sum --check --quiet -"), source.index("tar -xzf"))
        config = (ROOT / "config/oauth2-proxy/netdata.cfg").read_text()
        self.assertNotRegex(config, r"(?m)^\s*(skip_auth_routes|skip_auth_regex|trusted_ips)\s*=")


if __name__ == "__main__":
    unittest.main()
