import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/suricata-alerts.py'


def witness(path, source):
    stat = source.stat()
    path.write_text(json.dumps({'device': stat.st_dev, 'inode': stat.st_ino}))


def load_script():
    spec = importlib.util.spec_from_file_location('suricata_alerts', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AlertPrivacyTests(unittest.TestCase):
    def test_only_valid_alert_metadata_is_forwarded(self):
        self.assertTrue(SCRIPT.exists(), 'Missing strict alert metadata filter')
        module = load_script()
        event = {
            'timestamp': '2026-10-04T13:00:00.000000+0000',
            'event_type': 'alert', 'src_ip': '192.0.2.1', 'dest_ip': '198.51.100.1',
            'src_port': 12345, 'dest_port': 80, 'proto': 'TCP',
            'payload': 'secret', 'http': {'url': '/?token=secret'},
            'alert': {'signature_id': 1000001, 'signature': 'Local test',
                      'category': 'Test', 'severity': 3, 'action': 'allowed',
                      'metadata': {'secret': ['secret']}, 'rule': 'secret'},
        }
        filtered = module.sanitize(event)
        self.assertEqual(set(filtered), {'timestamp', 'event_type', 'src_ip', 'dest_ip',
                                        'src_port', 'dest_port', 'proto', 'alert'})
        self.assertEqual(set(filtered['alert']), {'signature_id', 'signature', 'category',
                                               'severity', 'action'})
        self.assertNotIn('secret', json.dumps(filtered))


    def test_reader_handles_partial_lines_rotation_and_restart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, state = [root / name for name in ('eve.json', 'alerts.json', 'state.json')]
            module = load_script()
            self.assertTrue(hasattr(module, 'Reader'), 'Missing durable EVE reader')
            event = {'timestamp': '2026-10-04T13:00:00+0000', 'event_type': 'alert',
                     'src_ip': '192.0.2.1', 'dest_ip': '198.51.100.1', 'proto': 'TCP',
                     'alert': {'signature_id': 1, 'signature': 'Test', 'category': 'Test',
                               'severity': 3, 'action': 'allowed'}}
            text = json.dumps(event)
            source.write_text(text[:20])
            reader = module.Reader(source, output, state)
            reader.tick()
            self.assertEqual(output.read_text(), '')
            with source.open('a') as f:
                f.write(text[20:] + '\n')
            reader.tick()
            self.assertEqual(len(output.read_text().splitlines()), 1)
            reader.close()
            reader = module.Reader(source, output, state)
            reader.tick()
            self.assertEqual(len(output.read_text().splitlines()), 1)
            reader.rotation_state = root / 'rotation.json'
            witness(reader.rotation_state, source)  # Fixture writer is already closed.
            source.rename(root / 'eve.json.1')
            source.write_text(text + '\n')
            reader.tick()
            reader.tick()
            self.assertEqual(len(output.read_text().splitlines()), 2)
            reader.close()

    def test_rotated_unterminated_record_does_not_stall_reader(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, state = [root / name for name in ('eve', 'out', 'state')]
            source.write_text('{"partial":')
            reader = load_script().Reader(source, output, state)
            reader.tick()
            reader.rotation_state = root / 'rotation.json'
            witness(reader.rotation_state, source)
            source.rename(root / 'eve.1')
            source.write_text('{"event_type":"stats"}\n')
            reader.tick()
            reader.tick()
            self.assertEqual(reader.file.name, str(source))
            self.assertEqual(reader.file.tell(), source.stat().st_size)
            reader.close()

    def test_logrotate_can_switch_to_configured_log_owner(self):
        text = (ROOT / 'systemd/hermes-suricata-logrotate.service').read_text()
        capabilities = next((line.split('=', 1)[1].split() for line in text.splitlines()
                             if line.startswith('AmbientCapabilities=')), [])
        self.assertIn('CAP_SETUID', capabilities,
                      'Explicit User=root can otherwise lose effective CAP_SETUID under systemd')
        self.assertIn('NoNewPrivileges=true', text)

    def test_logrotate_sandbox_can_write_root_owned_rotation_witness(self):
        text = (ROOT / 'systemd/hermes-suricata-logrotate.service').read_text()
        paths = next(line.split('=', 1)[1].split() for line in text.splitlines()
                     if line.startswith('ReadWritePaths='))
        self.assertIn('/var/lib/hermes-suricata', paths,
                      'Coordinated rotation needs a root-owned writer-quiescence witness')

    def test_invalid_or_non_alert_records_are_rejected(self):
        module = load_script()
        for event in (None, {}, {'event_type': 'stats'}, {'event_type': 'alert', 'alert': 'bad'}):
            self.assertIsNone(module.sanitize(event))


class RotationLossTests(unittest.TestCase):
    def event(self, sid):
        return json.dumps({'timestamp': '2026-10-04T13:00:00+0000', 'event_type': 'alert',
                          'src_ip': '192.0.2.1', 'dest_ip': '198.51.100.1', 'proto': 'TCP',
                          'alert': {'signature_id': sid, 'signature': 'Test', 'category': 'Test',
                                    'severity': 3, 'action': 'allowed'}}) + '\n'

    def test_restart_drains_unread_checkpointed_rotated_inode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, state = [root / name for name in ('eve.json', 'alerts.json', 'state.json')]
            source.write_text(self.event(1))
            reader = load_script().Reader(source, output, state)
            reader.tick()
            reader.close()
            with source.open('a') as writer:
                writer.write(self.event(2))
            proof = root / 'rotation.json'
            witness(proof, source)  # Writer closed before rename.
            source.rename(root / 'eve.json.1')
            source.write_text(self.event(3))
            reader = load_script().Reader(source, output, state, proof)
            try:
                reader.tick()
                ids = [json.loads(line)['alert']['signature_id'] for line in output.read_text().splitlines()]
                self.assertEqual(ids, [1, 2], 'Restart skipped unread checkpointed old inode')
                self.assertEqual(json.loads(state.read_text())['inode'], (root / 'eve.json.1').stat().st_ino)
                reader.tick()
                self.assertEqual([json.loads(line)['alert']['signature_id'] for line in output.read_text().splitlines()], [1, 2, 3])
            finally:
                reader.close()

    def test_temporary_old_eof_preserves_late_writer_append(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, state = [root / name for name in ('eve.json', 'alerts.json', 'state.json')]
            source.write_text(self.event(1))
            reader = load_script().Reader(source, output, state)
            try:
                with source.open('a') as writer:
                    reader.tick()
                    old_inode = source.stat().st_ino
                    source.rename(root / 'eve.json.1')
                    source.write_text(self.event(3))
                    reader.tick()  # Temporary EOF while the writer still owns the old FD.
                    writer.write(self.event(2))
                    writer.flush()
                    reader.tick()
                    ids = [json.loads(line)['alert']['signature_id'] for line in output.read_text().splitlines()]
                    self.assertEqual(ids, [1, 2], 'Old FD retired before writer quiescence')
                    self.assertEqual(json.loads(state.read_text())['inode'], old_inode)
                reader.rotation_state = root / 'rotation.json'
                witness(reader.rotation_state, root / 'eve.json.1')  # Only AFTER closing writer.
                reader.tick()
                reader.tick()
                ids = [json.loads(line)['alert']['signature_id'] for line in output.read_text().splitlines()]
                self.assertEqual(ids, [1, 2, 3])
            finally:
                reader.close()


class RotationProtocolTests(unittest.TestCase):
    def test_hooks_quiesce_real_writer_before_rename_and_preserve_stopped_services(self):
        import os
        import pwd
        import grp
        import shutil
        import signal
        import subprocess
        import tempfile
        import textwrap
        config = (ROOT / 'config/suricata/logrotate.conf').read_text()
        self.assertIn('prerotate', config, 'Rotation still renames before stopping the writer')
        logrotate = shutil.which('logrotate')
        self.assertIsNotNone(logrotate, 'Install the test prerequisite logrotate')
        assert logrotate is not None
        for active, target in (('1', 'eve'), ('0', 'eve'), ('1', 'log'), ('0', 'log')):
            with self.subTest(active=active, target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'filter').mkdir()
                source = root / 'eve.json'
                source.write_text(RotationLossTests().event(1))
                output, state = root / 'alerts.json', root / 'filter/filter.json'
                reader = load_script().Reader(source, output, state, root / 'rotation.json')
                reader.tick()
                reader.close()
                writer = subprocess.Popen(['/usr/bin/python3', '-u', '-c', textwrap.dedent("""
                    import signal, sys, time
                    from pathlib import Path
                    file = Path(sys.argv[1]).open('a')
                    def stop(*args):
                        file.write(sys.argv[3]); file.flush(); file.close()
                        Path(sys.argv[2]).write_text('closed')
                        sys.exit(0)
                    signal.signal(signal.SIGTERM, stop)
                    print('ready', flush=True)
                    while True: time.sleep(0.05)
                    """), str(source), str(root / 'closed'), RotationLossTests().event(2)], stdout=subprocess.PIPE, text=True)
                assert writer.stdout is not None
                self.assertEqual(writer.stdout.readline().strip(), 'ready')
                control = root / 'systemctl'
                control.write_text('#!/usr/bin/python3\n' + textwrap.dedent("""
                    import os, signal, sys, time
                    from pathlib import Path
                    root = Path(os.environ['FIXTURE_ROOT'])
                    args = sys.argv[1:]
                    with (root / 'calls').open('a') as file: file.write(' '.join(args) + '\\n')
                    if args[0] == 'is-active': sys.exit(0 if os.environ['ACTIVE'] == '1' else 3)
                    if args == ['stop', 'hermes-suricata.service'] and not (root / 'closed').exists():
                        os.kill(int(os.environ['WRITER_PID']), signal.SIGTERM)
                        deadline = time.monotonic() + 5
                        while not (root / 'closed').exists():
                            if time.monotonic() > deadline: sys.exit(1)
                            time.sleep(0.01)
                    """))
                control.chmod(0o700)
                def isolated(script):
                    return (script.replace('/bin/systemctl', str(control))
                            .replace('/var/lib/hermes-suricata', str(root))
                            .replace('/var/log/hermes-suricata', str(root))
                            .replace('/bin/chown root:hermes-suricata', '/bin/true')
                            .replace('"0:750"', '"%s:750"' % os.getuid()))
                root.chmod(0o750)
                env = dict(os.environ, FIXTURE_ROOT=str(root), ACTIVE=active, WRITER_PID=str(writer.pid))
                try:
                    old_inode = source.stat().st_ino
                    fixture_config = root / 'logrotate.conf'
                    user = pwd.getpwuid(os.getuid()).pw_name
                    group = grp.getgrgid(os.getgid()).gr_name
                    candidate = config
                    if target == 'log':
                        (root / 'suricata.log').write_text('fixture logger\n')
                        candidate = candidate.replace('/var/log/hermes-suricata/eve.json ', '', 1)
                    fixture_config.write_text(isolated(candidate).replace('hermes-suricata hermes-suricata', user + ' ' + group))
                    result = subprocess.run([logrotate, '--force', '--state', str(root / 'logrotate.state'),
                                             str(fixture_config)], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue((root / 'closed').exists(), 'prerotate returned while writer FD was open')
                    writer.wait(timeout=5)
                    calls = (root / 'calls').read_text().splitlines()
                    self.assertLess(calls.index('stop hermes-suricata-alerts.service'), calls.index('stop hermes-suricata.service'))
                    if target == 'eve':
                        proof = json.loads((root / 'rotation.json').read_text())
                        self.assertEqual(proof['inode'], old_inode)
                        self.assertEqual((root / 'eve.json.1').stat().st_ino, old_inode)
                        self.assertNotEqual(source.stat().st_ino, old_inode)
                    else:
                        self.assertFalse((root / 'rotation.json').exists(), 'Unrotated EVE must not retain stale quiescence proof')
                        self.assertEqual(source.stat().st_ino, old_inode)
                    with source.open('a') as current:
                        current.write(RotationLossTests().event(3))
                    starts = [line for line in (root / 'calls').read_text().splitlines() if line.startswith('start ')]
                    self.assertEqual(starts, ['start hermes-suricata.service', 'start hermes-suricata-alerts.service'] if active == '1' else [])
                    self.assertFalse((root / 'rotation-active').exists())
                    if target == 'eve':
                        refused = subprocess.run([logrotate, '--force', '--state', str(root / 'logrotate.state'),
                                                  str(fixture_config)], env=env, capture_output=True, text=True)
                        self.assertNotEqual(refused.returncode, 0)
                        self.assertIn('filter must drain previous EVE inode first', refused.stderr)
                        self.assertEqual((root / 'eve.json.1').stat().st_ino, old_inode)
                        self.assertTrue((root / 'rotation-active').exists())
                    reader = load_script().Reader(source, output, state, root / 'rotation.json')
                    try:
                        reader.tick(); reader.tick()
                        ids = [json.loads(line)['alert']['signature_id'] for line in output.read_text().splitlines()]
                        self.assertEqual(ids, [1, 2, 3])
                    finally:
                        reader.close()
                finally:
                    if writer.poll() is None:
                        writer.send_signal(signal.SIGTERM)
                        writer.wait(timeout=5)
                    writer.stdout.close()


if __name__ == '__main__':
    unittest.main()
