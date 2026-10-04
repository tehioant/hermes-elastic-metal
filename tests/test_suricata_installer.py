"""Isolated Suricata deployment tests; never installs or captures on the host."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / 'scripts/suricata-config.py'


class RendererTests(unittest.TestCase):
    def test_renderer_replaces_outputs_and_limits_capture(self):
        self.assertTrue(RENDERER.exists(), 'renderer not implemented')
        spec = importlib.util.spec_from_file_location('suricata_config', RENDERER)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        base = {'outputs': [{'pcap-log': {'enabled': True}}], 'af-packet': [{'interface': 'any'}],
                'vars': {'address-groups': {'HOME_NET': 'any'}},
                'app-layer': {'protocols': {'tls': {'enabled': True}}}}
        addresses = [{'addr_info': [{'family': 'inet', 'scope': 'global', 'local': '51.159.210.11', 'prefixlen': 24},
                                    {'family': 'inet6', 'scope': 'global', 'local': '2001:bc8:1201:600:2247:47ff:fe7f:68c8', 'prefixlen': 64}]}]
        c = module.render(base, 'eno1', addresses)
        self.assertEqual(c['vars']['address-groups']['HOME_NET'], '[51.159.210.11/32,2001:bc8:1201:600:2247:47ff:fe7f:68c8/128]')
        self.assertEqual(c['vars']['address-groups']['EXTERNAL_NET'], '!$HOME_NET')
        self.assertEqual(len(c['outputs']), 1)
        eve = c['outputs'][0]['eve-log']
        self.assertFalse(eve['metadata'])
        self.assertEqual([next(iter(x)) for x in eve['types']], ['alert', 'stats'])
        alert = eve['types'][0]['alert']
        for flag in ['payload', 'payload-printable', 'packet', 'http-body', 'http-body-printable',
                     'websocket-payload', 'websocket-payload-printable', 'metadata', 'tagged-packets']:
            self.assertIs(alert[flag], False)
        self.assertEqual(c['stats']['interval'], 30)
        self.assertEqual(c['runmode'], 'workers')
        capture = c['af-packet'][0]
        self.assertEqual(capture['interface'], 'eno1')
        self.assertEqual(capture['threads'], 2)
        self.assertEqual(capture['cluster-id'], 187)
        self.assertEqual(capture['cluster-type'], 'cluster_flow')
        self.assertTrue(capture['disable-promisc'])
        self.assertNotIn('copy-mode', capture)
        self.assertEqual(c['app-layer'], base['app-layer'])
        self.assertEqual(c['flow']['memcap'], '128mb')
        self.assertEqual(c['stream']['reassembly']['memcap'], '128mb')


class PreflightTests(unittest.TestCase):
    def run_preflight(self, root):
        return subprocess.run(['bash', str(REPO / 'scripts/install-suricata.sh'),
                               '--preflight', '--interface', 'eno1'],
                              env={**os.environ, 'SURICATA_ROOT': str(root)},
                              text=True, capture_output=True)

    def test_unowned_binary_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'usr/bin/suricata'
            binary.parent.mkdir(parents=True)
            binary.touch()
            result = self.run_preflight(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unmanaged', result.stderr)
            self.assertFalse((root / 'var/lib/hermes-suricata').exists())

    def test_fresh_preflight_and_symlink_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_preflight(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            etc = root / 'etc'
            etc.mkdir()
            (etc / 'hermes-suricata').symlink_to(root / 'outside')
            result = self.run_preflight(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('symlink', result.stderr)


class PackageTests(unittest.TestCase):
    def test_partial_apt_failure_preserves_marker_masks_stock_and_restores_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for p in ['etc/hermes-suricata', 'usr/sbin', 'usr/bin']:
                (root / p).mkdir(parents=True)
            hook = root / 'usr/sbin/policy-rc.d'
            hook.write_text('#!/bin/sh\nexit 0\n')
            hook.chmod(0o751)
            script = r'''source "$1"
chown() { :; }
apt-get() { touch "${ROOT%/}/usr/bin/suricata"; return 42; }
systemctl() { printf '%s\n' "$*" >> "${ROOT%/}/calls"; }
ensure_packages
'''
            result = subprocess.run(['bash', '-c', script, 'test', str(REPO / 'scripts/install-suricata.sh')],
                                    env={**os.environ, 'SURICATA_ROOT': str(root)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(hook.read_text(), '#!/bin/sh\nexit 0\n')
            self.assertEqual(hook.stat().st_mode & 0o777, 0o751)
            self.assertTrue((root / 'etc/hermes-suricata/package-managed').exists())
            self.assertIn('mask suricata.service', (root / 'calls').read_text())


class RuleUpdateTests(unittest.TestCase):
    def test_updater_excludes_non_alert_actions_with_staging_disable_config(self):
        text = (REPO / 'scripts/update-suricata-rules.sh').read_text()
        pattern = r're: ^(?:drop|pass|reject(?:src|dst|both)?)\s+'
        self.assertIn(pattern, text)
        self.assertIn('> "${stage}/disable.conf"', text)
        self.assertIn('--disable-conf "${stage}/disable.conf"', text)
        self.assertLess(text.index('--disable-conf'), text.index('--data-dir'))
        self.assertLess(text.index('--disable-conf'), text.index('check-rules'))

    def test_rule_update_exists_and_rejects_non_alert_before_promotion(self):
        updater = REPO / 'scripts/update-suricata-rules.sh'
        self.assertTrue(updater.exists(), 'atomic rule updater missing')
        text = updater.read_text()
        self.assertLess(text.index('check-rules'), text.index('mv -f'))
        self.assertIn('ruleset-reload-rules', text)
        self.assertNotIn('suricatasc -s', text)
        self.assertIn("['return'] == 'OK'", text)
        self.assertIn('--no-reload', text)
        self.assertIn('-S', text)


if __name__ == '__main__':
    unittest.main()
