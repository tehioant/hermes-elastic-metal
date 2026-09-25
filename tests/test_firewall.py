import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FirewallTest(unittest.TestCase):
    def test_new_firewall_adds_private_ssh_before_enabling_without_reset(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            binary = root / "ufw"
            binary.write_text('#!/bin/sh\nif [ "$1" = status ]; then printf "Status: %s\\n" "$UFW_STATUS"; else printf "%s\\n" "$*" >> "$UFW_LOG"; fi\n')
            binary.chmod(0o700)
            env = os.environ.copy()
            env.update(PATH=f"{root}:{env['PATH']}", UFW_STATUS="inactive",
                       UFW_LOG=str(root / "calls"), FIREWALL_STATE_FILE=str(root / "state"),
                       ADMIN_CIDRS="192.0.2.4/32,198.51.100.5/32")
            result = subprocess.run(["bash", str(ROOT / "scripts/firewall.sh")],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (root / "calls").read_text()
            self.assertIn("allow in on tailscale0 to any port 22 proto tcp", calls)
            self.assertIn("allow from 192.0.2.4/32 to any port 22 proto tcp", calls)
            self.assertIn("allow from 198.51.100.5/32 to any port 22 proto tcp", calls)
            self.assertNotIn("reset", calls)
            self.assertEqual((root / "state").read_text().strip(), env["ADMIN_CIDRS"])

    def test_active_firewall_without_a_recorded_allowlist_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            binary = root / "ufw"
            binary.write_text('#!/bin/sh\nif [ "$1" = status ]; then printf "Status: active\\n"; else printf "%s\\n" "$*" >> "$UFW_LOG"; fi\n')
            binary.chmod(0o700)
            env = os.environ.copy()
            env.update(PATH=f"{root}:{env['PATH']}", UFW_LOG=str(root / "calls"),
                       FIREWALL_STATE_FILE=str(root / "state"), ADMIN_CIDRS="192.0.2.4/32")
            result = subprocess.run(["bash", str(ROOT / "scripts/firewall.sh")],
                                    env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "calls").exists())

    def test_active_firewall_replaces_old_cidr_after_allowing_new_one(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            binary = root / "ufw"
            binary.write_text('#!/bin/sh\nif [ "$1" = status ]; then printf "Status: active\\n"; else printf "%s\\n" "$*" >> "$UFW_LOG"; fi\n')
            binary.chmod(0o700)
            (root / "state").write_text("192.0.2.4/32,198.51.100.5/32\n")
            env = os.environ.copy()
            env.update(PATH=f"{root}:{env['PATH']}", UFW_LOG=str(root / "calls"),
                       FIREWALL_STATE_FILE=str(root / "state"), ADMIN_CIDRS="198.51.100.5/32,203.0.113.9/32")
            result = subprocess.run(["bash", str(ROOT / "scripts/firewall.sh")],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (root / "calls").read_text().splitlines()
            self.assertLess(calls.index("allow from 203.0.113.9/32 to any port 22 proto tcp"),
                            calls.index("--force delete allow from 192.0.2.4/32 to any port 22 proto tcp"))
            self.assertEqual((root / "state").read_text().strip(), env["ADMIN_CIDRS"])
            self.assertFalse(any("reset" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
