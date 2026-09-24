import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShipAdminKeyTest(unittest.TestCase):
    def test_ships_explicit_admin_key_from_terraform_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for name in ("scripts", "config", "systemd", "terraform", "bin"):
                (root / name).mkdir()
            shutil.copy(ROOT / "scripts/ship.sh", root / "scripts/ship.sh")
            admin_key = "ssh-ed25519 AAAAEXPLICIT test-admin"
            terraform = root / "bin/terraform"
            terraform.write_text("#!/bin/sh\nfor last; do :; done\n"
                                 "case \"$last\" in\n"
                                 "  admin_cidrs) printf '192.0.2.1/32';;\n"
                                 "  ops_ssh_public_key) printf 'ssh-ed25519 AAAAEXPLICIT test-admin';;\n"
                                 "  restic_repository) printf 's3:example';;\n"
                                 "  backup_access_key) printf 'public';;\n"
                                 "  backup_secret_key) printf 'private';;\n"
                                 "esac\n")
            ssh = root / "bin/ssh"
            ssh.write_text('#!/bin/sh\ncase "$*" in *"tar -xzf"*) cat >/dev/null;; '
                           '*) cat > "$CAPTURE_ENV_FILE";; esac\n')
            terraform.chmod(0o700)
            ssh.chmod(0o700)
            env = os.environ.copy()
            env.pop("OPS_SSH_PUBLIC_KEY", None)
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", RESTIC_PASSWORD="stable",
                       CAPTURE_ENV_FILE=str(root / "remote.env"))
            result = subprocess.run(["bash", str(root / "scripts/ship.sh"), "ops@example.test"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            decoded = subprocess.run(["bash", "-c", 'source "$1"; printf "%s" "${OPS_SSH_PUBLIC_KEY:-}"',
                                      "--", str(root / "remote.env")], capture_output=True, text=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(decoded.stdout, admin_key)


if __name__ == "__main__":
    unittest.main()
