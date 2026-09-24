import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShipTest(unittest.TestCase):
    def test_uses_supplied_restic_password_without_creating_a_new_one(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for name in ("scripts", "config", "systemd", "terraform", "bin"):
                (root / name).mkdir()
            shutil.copy(ROOT / "scripts/ship.sh", root / "scripts/ship.sh")
            terraform = root / "bin/terraform"
            terraform.write_text("#!/bin/sh\nfor last; do :; done\ncase \"$last\" in\n"
                                 "  admin_cidrs) printf '192.0.2.1/32';;\n"
                                 "  restic_repository) printf 's3:example';;\n"
                                 "  backup_access_key) printf 'public';;\n"
                                 "  backup_secret_key) printf 'private';;\n"
                                 "esac\n")
            ssh = root / "bin/ssh"
            ssh.write_text('#!/bin/sh\ncase "$*" in\n'
                           '  *"tar -xzf"*) cat >/dev/null;;\n'
                           '  *) cat > "$CAPTURE_ENV_FILE";;\n'
                           'esac\n')
            terraform.chmod(0o700)
            ssh.chmod(0o700)
            env = os.environ.copy()
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", RESTIC_PASSWORD="from-ci",
                       CAPTURE_ENV_FILE=str(root / "remote.env"))
            result = subprocess.run(["bash", str(root / "scripts/ship.sh"), "ops@example.test"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESTIC_PASSWORD=from-ci", (root / "remote.env").read_text())
            self.assertFalse((root / ".restic-password").exists())

            env["RESTIC_PASSWORD"] = "quote' dollar$ space"
            result = subprocess.run(["bash", str(root / "scripts/ship.sh"), "ops@example.test"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            decoded = subprocess.run(["bash", "-c", 'source "$1"; printf "%s" "$RESTIC_PASSWORD"',
                                      "--", str(root / "remote.env")], capture_output=True, text=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(decoded.stdout, env["RESTIC_PASSWORD"])

    def test_ops_can_replace_a_root_owned_stage_after_initial_bootstrap(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for name in ("scripts", "config", "systemd", "terraform", "bin"):
                (root / name).mkdir()
            shutil.copy(ROOT / "scripts/ship.sh", root / "scripts/ship.sh")
            terraform = root / "bin/terraform"
            terraform.write_text("#!/bin/sh\nfor last; do :; done\n"
                                 "case \"$last\" in\n"
                                 "  admin_cidrs) printf '192.0.2.1/32';;\n"
                                 "  restic_repository) printf 's3:example';;\n"
                                 "  backup_access_key) printf 'public';;\n"
                                 "  backup_secret_key) printf 'private';;\n"
                                 "esac\n")
            ssh = root / "bin/ssh"
            ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CAPTURE_COMMANDS"\n'
                           'case "$*" in *"tar -xzf"*) cat >/dev/null;; *) cat >/dev/null;; esac\n')
            terraform.chmod(0o700)
            ssh.chmod(0o700)
            env = os.environ.copy()
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", RESTIC_PASSWORD="stable",
                       CAPTURE_COMMANDS=str(root / "ssh-commands"))
            for target in ("root@example.test", "ops@example.test"):
                result = subprocess.run(["bash", str(root / "scripts/ship.sh"), target],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            root_transfer, _, ops_transfer, _ = (root / "ssh-commands").read_text().splitlines()
            self.assertIn("root@example.test rm -rf /tmp/hermes", root_transfer)
            self.assertIn("ops@example.test sudo rm -rf /tmp/hermes", ops_transfer)
            self.assertIn("sudo mkdir -m 0700 /tmp/hermes", ops_transfer)
            self.assertIn("sudo tar -xzf - -C /tmp/hermes", ops_transfer)


if __name__ == "__main__":
    unittest.main()
