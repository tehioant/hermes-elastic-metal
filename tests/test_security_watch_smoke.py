import json
import os
import tempfile
import unittest
from pathlib import Path

import tests.security_dashboard_smoke as smoke


class WatchSmokeTest(unittest.TestCase):
    def test_all_journal_readers_are_discovered_for_isolation(self):
        config = '\n'.join([
            'loki.source.journal "ufw" {',
            'loki.source.journal "network_watch" {',
            'loki.source.file "not_a_journal" {',
            'loki.source.journal "caddy" {',
        ])
        self.assertTrue(hasattr(smoke, 'journal_sources'),
                        'Every journal source, including watch, must use the isolated fixture directory')
        self.assertEqual(smoke.journal_sources(config), ['ufw', 'network_watch', 'caddy'])


    def test_watch_fixture_preserves_real_records_and_adds_privacy_probes(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or os.environ.get('RUNNER_TEMP')) as directory:
            path = Path(directory) / 'captured.jsonl'
            # Explicitly test-only records; integration supplies actual isolated captures.
            original = [{'schema': 1, 'kind': 'attempt', 'src_ip': '8.8.8.8',
                         'as_org': 'Test "fixture" with newline\n'},
                        {'schema': 1, 'kind': 'health', 'packets_dropped': 0}]
            path.write_text('\n'.join(json.dumps(record) for record in original))
            self.assertTrue(hasattr(smoke, 'watch_fixture_messages'),
                            'Real capture records must be fed through the production journal selector')
            messages = smoke.watch_fixture_messages(path)
            parsed = [json.loads(line) for line in messages.splitlines() if line.startswith('{')]
            self.assertEqual(parsed[0]['as_org'], original[0]['as_org'])
            self.assertEqual(parsed[1]['kind'], 'health')
            self.assertIn('WATCH_PAYLOAD_SECRET', messages)
            self.assertIn('WATCH_HEADER_SECRET', messages)
            self.assertTrue(any(record.get('kind') == 'debug' for record in parsed))


    def test_incorrect_stat_counts_fail_the_smoke(self):
        result = {'frames': [{'schema': {'fields': [{'name': 'Time', 'type': 'time'}, {'name': 'Value', 'type': 'number'}]},
                              'data': {'values': [[1, 2], [None, 3]]}}]}
        self.assertTrue(hasattr(smoke, 'assert_stat_value'), 'Stats must be checked numerically, not just for nonempty frames')
        smoke.assert_stat_value(result, 3, 'Observed flows')
        with self.assertRaises(AssertionError):
            smoke.assert_stat_value(result, 2, 'Distinct sources')

    def test_watch_labels_allow_only_fixed_loki_generated_metadata(self):
        self.assertTrue(hasattr(smoke, 'assert_watch_labels'),
                        'Watch label checks must distinguish fixed Loki enrichment from promoted IPs')
        smoke.assert_watch_labels({'job': 'security-watch', 'host': 'test-only',
                                   'service_name': 'security-watch', 'detected_level': 'unknown'})
        smoke.assert_watch_labels({'job': 'security-watch', 'host': 'test-only'})
        for extra in ({'src_ip': '8.8.8.8'}, {'watch_schema': '1'},
                      {'service_name': '8.8.8.8'}, {'detected_level': 'injected'}):
            with self.subTest(extra=extra), self.assertRaises(AssertionError):
                smoke.assert_watch_labels({'job': 'security-watch', 'host': 'test-only'} | extra)

    def test_empty_capture_cannot_make_an_end_to_end_smoke_pass(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or os.environ.get('RUNNER_TEMP')) as directory:
            path = Path(directory) / 'empty.jsonl'
            path.write_text(json.dumps({'schema': 1, 'kind': 'health'}) + '\n')
            with self.assertRaisesRegex(ValueError, 'attempt.*health'):
                smoke.watch_fixture_messages(path)


if __name__ == '__main__':
    unittest.main()
