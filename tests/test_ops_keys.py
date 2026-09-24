import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_ops_keys.sh"


class OpsKeysTest(unittest.TestCase):
    def test_only_explicit_admin_and_deploy_keys_are_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("admin", "deploy"):
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
                               check=True, capture_output=True)
            admin_key = (root / "admin.pub").read_text().strip()
            deploy_key = (root / "deploy.pub").read_text().strip()
            (root / "deploy.pub").write_text("restrict " + deploy_key + "\n")
            (root / "root.keys").write_text('from="192.0.2.1" ' + admin_key + "\n")
            stored_admin = root / "ops.pub"
            authorized = root / "ops.keys"
            env = os.environ.copy()
            env["OPS_SSH_PUBLIC_KEY"] = admin_key
            command = ["bash", str(SCRIPT), str(stored_admin), str(root / "deploy.pub"), str(authorized)]
            first = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(authorized.read_text().splitlines(), [admin_key, "restrict " + deploy_key])
            self.assertEqual(stored_admin.read_text().strip(), admin_key)
            self.assertIn('from="192.0.2.1"', (root / "root.keys").read_text())
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(authorized.read_text().splitlines(), [admin_key, "restrict " + deploy_key])

    def test_rejects_key_options_before_replacing_authorized_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "admin")],
                           check=True, capture_output=True)
            admin_key = (root / "admin.pub").read_text()
            ops_keys = root / "ops.keys"
            ops_keys.write_text(admin_key)
            env = os.environ.copy()
            env["OPS_SSH_PUBLIC_KEY"] = 'from="192.0.2.1" ' + admin_key.strip()
            result = subprocess.run(["bash", str(SCRIPT), str(root / "admin.pub"),
                                     str(root / "missing.pub"), str(ops_keys)],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(ops_keys.read_text(), admin_key)
            self.assertEqual((root / "admin.pub").read_text(), admin_key)

    def test_preserves_explicit_deploy_key_across_syncs_and_revokes_on_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("admin", "deploy"):
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
                               check=True, capture_output=True)
            admin_key = (root / "admin.pub").read_text().strip()
            deploy_key = (root / "deploy.pub").read_text().strip()
            (root / "deploy.pub").write_text("restrict " + deploy_key + "\n")
            ops_keys = root / "ops.keys"
            command = ["bash", str(SCRIPT), str(root / "admin.pub"), str(root / "deploy.pub"), str(ops_keys)]
            for _ in range(2):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(ops_keys.read_text().splitlines(), [admin_key, "restrict " + deploy_key])

            (root / "deploy.pub").unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ops_keys.read_text(), admin_key + "\n")


if __name__ == "__main__":
    unittest.main()
