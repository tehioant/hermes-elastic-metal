"""Public installer boundary: guards, offline rendering, and retry stability."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/install-wazuh.sh"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "etc").mkdir()

    def cli(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args, "--root", str(self.root)],
                              capture_output=True, text=True)

    def assert_strict_tls(self, config):
        import ssl
        ca = config / "certs/root-ca.pem"
        for name in ("indexer", "manager", "dashboard", "admin"):
            verified = subprocess.run(["openssl", "verify", "-x509_strict", "-CAfile", str(ca),
                                       "-verify_ip", "127.0.0.1", str(config / f"certs/{name}.pem")],
                                      capture_output=True, text=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
        # Exercise a real TLS handshake without opening a host listener.
        client_context = ssl.create_default_context(cafile=str(ca))
        client_context.verify_flags |= ssl.VERIFY_X509_STRICT
        self.assertTrue(client_context.check_hostname)
        self.assertEqual(client_context.verify_mode, ssl.CERT_REQUIRED)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(str(config / "certs/indexer.pem"),
                                       str(config / "certs/indexer.key"))
        client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
        client = client_context.wrap_bio(client_in, client_out, server_hostname="127.0.0.1")
        server = server_context.wrap_bio(server_in, server_out, server_side=True)
        done = set()
        for _ in range(20):
            for name, peer in (("client", client), ("server", server)):
                if name not in done:
                    try:
                        peer.do_handshake()
                        done.add(name)
                    except ssl.SSLWantReadError:
                        pass
            server_in.write(client_out.read())
            client_in.write(server_out.read())
            if len(done) == 2:
                break
        self.assertEqual(done, {"client", "server"})
        self.assertIsNotNone(client.version())

    def test_new_render_certificates_pass_strict_tls(self):
        result = self.cli("--render-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_strict_tls(self.root / "etc/hermes-wazuh")

    def test_owned_legacy_ca_repair_preserves_established_identity(self):
        result = self.cli("--render-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.root / "etc/hermes-wazuh"
        ca, key = config / "certs/root-ca.pem", config / "certs/root-ca.key"
        # Reproduce the original openssl req CA: CA:TRUE and SKI, but no KU.
        legacy_config = self.root / "legacy-ca.cnf"
        legacy_config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ca\n"
                                 "[dn]\n[ca]\nbasicConstraints=critical,CA:TRUE\n"
                                 "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
        generated = subprocess.run(["openssl", "req", "-x509", "-key", str(key), "-sha256",
                                    "-days", "3650", "-subj", "/CN=Hermes Wazuh CA/O=Wazuh/C=US",
                                    "-config", str(legacy_config), "-out", str(ca)], capture_output=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        rejected = subprocess.run(["openssl", "verify", "-x509_strict", "-CAfile", str(ca),
                                   str(config / "certs/indexer.pem")], capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("CA cert does not include key usage extension", rejected.stderr)
        identity = ["credentials.json", "client.keys", "certs/root-ca.key"]
        identity += [f"certs/{name}{suffix}" for name in ("indexer", "manager", "dashboard", "admin")
                     for suffix in (".key", ".pem")]
        before = {name: (config / name).read_bytes() for name in identity}
        original_ca = ca.read_bytes()

        def ca_identity():
            inspected = subprocess.run(["openssl", "x509", "-in", str(ca), "-noout",
                                        "-subject", "-issuer", "-serial", "-dates", "-pubkey",
                                        "-ext", "subjectKeyIdentifier"], capture_output=True)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            return inspected.stdout

        original_metadata = ca_identity()
        repaired = self.cli("--render-only")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertTrue(ca.read_bytes() != original_ca, "legacy public CA must be reissued")
        self.assertEqual(ca_identity(), original_metadata,
                         "CA subject/issuer/serial/dates/public key/SKI must not change")
        for name in identity:
            self.assertTrue((config / name).read_bytes() == before[name], f"identity changed: {name}")
        self.assert_strict_tls(config)
        repaired_ca = ca.read_bytes()
        retried = self.cli("--render-only")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertTrue(ca.read_bytes() == repaired_ca, "compliant CA must not be reissued on retry")
        for name in identity:
            self.assertTrue((config / name).read_bytes() == before[name], f"retry changed: {name}")

    def test_builtin_ioc_lists_are_registered(self):
        import xml.etree.ElementTree as ET
        manager = ET.parse(REPO / 'config/wazuh/manager.xml').getroot()
        lists = {item.text for item in manager.findall('ruleset/list')}
        self.assertTrue({'etc/lists/malicious-ioc/malicious-ip',
                         'etc/lists/malicious-ioc/malicious-domains',
                         'etc/lists/malicious-ioc/malware-hashes'} <= lists,
                        'Builtin IOC rules otherwise disable themselves at startup')

    def test_indexer_can_read_root_group_owned_private_mounts(self):
        result = self.cli("--render-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.root / "etc/hermes-wazuh"
        compose = json.loads((config / "compose.yml").read_text())
        indexer = compose["services"]["wazuh.indexer"]
        self.assertEqual(indexer.get("group_add"), ["0"])
        for name in ("certs/indexer.key", "certs/admin.key", "internal_users.yml"):
            with self.subTest(name=name):
                self.assertEqual((config / name).stat().st_mode & 0o777, 0o640)
                mount = next(m for m in indexer["volumes"]
                             if m.get("source") == str(config / name))
                self.assertTrue(mount["read_only"])

    def test_preflight_refuses_unmanaged_agent_without_writing(self):
        agent = self.root / "var/ossec/etc"
        agent.mkdir(parents=True)
        config = agent / "ossec.conf"
        config.write_text("UNMANAGED")
        result = self.cli("--preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged", result.stderr)
        self.assertEqual(config.read_text(), "UNMANAGED")
        self.assertFalse((self.root / "etc/hermes-wazuh").exists())

    def test_preflight_refuses_each_unmanaged_target_and_symlink(self):
        targets = ["etc/hermes-wazuh", "var/lib/hermes-wazuh",
                   "etc/systemd/system/hermes-wazuh.service",
                   "etc/systemd/system/wazuh-agent.service.d",
                   "etc/apt/sources.list.d/hermes-wazuh.list"]
        for target in targets:
            with self.subTest(target=target):
                path = self.root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("unmanaged")
                result = self.cli("--preflight")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unmanaged", result.stderr)
                self.assertEqual(path.read_text(), "unmanaged")
                path.unlink()
        config = self.root / "etc/hermes-wazuh"
        config.symlink_to(self.root / "etc", target_is_directory=True)
        result = self.cli("--render-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(config.is_symlink())

    def test_fixture_install_is_rejected_before_any_write(self):
        result = self.cli("--install")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "etc/hermes-wazuh").exists())
        result = self.cli("--preflight")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "etc/hermes-wazuh").exists())

    def test_autostart_hook_inode_and_mode_survive_failure_and_symlink(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("wazuh_install", REPO / "scripts/wazuh-install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        hook = self.root / "policy-rc.d"
        hook.write_text("#!/bin/sh\nexit 42\n")
        hook.chmod(0o750)
        before = hook.stat()
        with self.assertRaisesRegex(RuntimeError, "apt failed"):
            with installer.block_autostart(hook):
                self.assertEqual(subprocess.run([str(hook)]).returncode, 101)
                raise RuntimeError("apt failed")
        self.assertEqual(hook.stat().st_ino, before.st_ino)
        self.assertEqual(hook.stat().st_mode, before.st_mode)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 42\n")
        hook.unlink()
        target = self.root / "original-hook"
        target.write_text("unchanged")
        hook.symlink_to(target)
        with installer.block_autostart(hook):
            self.assertFalse(hook.is_symlink())
        self.assertTrue(hook.is_symlink())
        self.assertEqual(target.read_text(), "unchanged")

    def test_retry_refuses_missing_established_identity_instead_of_rotating(self):
        first = self.cli("--render-only")
        self.assertEqual(first.returncode, 0, first.stderr)
        config = self.root / "etc/hermes-wazuh"
        original_key = (config / "client.keys").read_bytes()
        (config / "credentials.json").unlink()
        retry = self.cli("--render-only")
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn("recover", retry.stderr)
        self.assertFalse((config / "credentials.json").exists())
        self.assertEqual((config / "client.keys").read_bytes(), original_key)

    def test_offline_render_is_private_and_preserves_identity_on_retry(self):
        import yaml
        import xml.etree.ElementTree as ET
        result = self.cli("--render-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.root / "etc/hermes-wazuh"
        compose = yaml.safe_load((config / "compose.yml").read_text())
        import shutil
        if shutil.which("docker"):
            check = subprocess.run(["docker", "compose", "-f", str(config / "compose.yml"), "config", "--quiet"],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        self.assertNotIn("networks", compose)
        for service in compose["services"].values():
            self.assertEqual(service["network_mode"], "host")
            self.assertNotIn("ports", service)
            self.assertEqual(service["restart"], "always")
            self.assertRegex(service["image"], r"^wazuh/wazuh-(manager|indexer|dashboard):4\.14\.8@sha256:[0-9a-f]{64}$")
            for mount in service["volumes"]:
                if mount["type"] == "bind":
                    self.assertTrue(mount["source"].startswith(str(config) + "/"))
        self.assertEqual(compose["services"]["wazuh.indexer"]["mem_limit"], "3g")
        self.assertEqual(compose["services"]["wazuh.manager"]["mem_limit"], "2g")
        self.assertEqual(compose["services"]["wazuh.dashboard"]["mem_limit"], "1g")
        indexer = yaml.safe_load((config / "indexer.yml").read_text())
        dashboard = yaml.safe_load((config / "dashboard.yml").read_text())
        api = yaml.safe_load((config / "api.yml").read_text())
        self.assertEqual(indexer["network.host"], "127.0.0.1")
        self.assertEqual(dashboard["server.host"], "127.0.0.1")
        self.assertEqual(dashboard["server.port"], 8443)
        self.assertFalse(dashboard["data.search.usageTelemetry.enabled"])
        self.assertEqual(api["host"], ["127.0.0.1"])
        manager = ET.parse(config / "manager.xml").getroot()
        self.assertEqual(manager.findtext("remote/local_ip"), "127.0.0.1")
        self.assertEqual(manager.findtext("auth/disabled"), "yes")
        self.assertEqual(manager.findtext("active-response/disabled"), "yes")
        self.assertEqual(manager.findtext("global/logall_json"), "no")
        agent = ET.parse(config / "agent.xml").getroot()
        self.assertEqual(agent.findtext("active-response/disabled"), "yes")
        self.assertEqual(agent.findtext("client/server/address"), "127.0.0.1")
        self.assertEqual(agent.findtext("wodle[@name='syscollector']/processes"), "no")
        self.assertEqual(agent.findtext("wodle[@name='syscollector']/ports"), "no")
        self.assertEqual([e.text for e in agent.findall("localfile/location")],
                         ["journald", "/var/log/fail2ban.log", "/var/log/hermes-suricata/alerts.json"])
        self.assertEqual(agent.find("localfile/filter").attrib,
                         {"field": "_COMM", "ignore_if_missing": "no"})
        self.assertEqual(agent.findtext("localfile/filter"), "^sshd(-session)?$")
        self.assertEqual({e.text for e in agent.findall("syscheck/directories")},
                         {"/etc/ssh", "/etc/systemd/system", "/etc/ufw", "/var/lib/hermes-wazuh/fim-test"})
        self.assertTrue(all(e.get("report_changes") == "no" for e in agent.findall("syscheck/directories")))
        self.assertIsNone(agent.find(".//command"))
        self.assertIsNone(manager.find(".//command"))
        secrets = ["credentials.json", "client.keys", "certs/root-ca.key"]
        originals = {p: (config / p).read_bytes() for p in secrets}
        self.assertTrue(all((config / p).stat().st_mode & 0o077 == 0 for p in secrets))
        users = yaml.safe_load((config / "internal_users.yml").read_text())
        self.assertEqual(set(users), {"_meta", "admin", "kibanaserver"})
        import bcrypt
        credentials = json.loads((config / "credentials.json").read_text())
        self.assertTrue(bcrypt.checkpw(credentials["indexer"].encode(), users["admin"]["hash"].encode()))
        self.assertTrue(bcrypt.checkpw(credentials["dashboard"].encode(), users["kibanaserver"]["hash"].encode()))
        cert = subprocess.run(["openssl", "verify", "-CAfile", str(config / "certs/root-ca.pem"),
                               "-verify_ip", "127.0.0.1", str(config / "certs/indexer.pem")], capture_output=True)
        self.assertEqual(cert.returncode, 0, cert.stderr)
        result = self.cli("--render-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(originals, {p: (config / p).read_bytes() for p in secrets})
        self.assertFalse((self.root / "var/ossec").exists())


if __name__ == "__main__":
    unittest.main()
