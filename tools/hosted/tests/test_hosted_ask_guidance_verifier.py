"""Exact CLI7/8/9 output families; synthetic fixtures are not live model proof."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/hosted'))
sys.path.insert(0, str(ROOT / 'hosted/linux'))
from aios_console import shell
from aios_management.binding import Authority
import verify_console as verifier
from test_hosted_console import agent, fixture
from test_hosted_console_verifier import historical_console_session

OLD8 = b'  No verified answer is available; check agent status and room status before retrying.\n'


class AskGuidanceVerifierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc, self.sysfs = fixture(self.root)
        self.serial = 0

    @staticmethod
    def failed_receipt():
        value = agent('ask', bound=True, prompt='hello')
        receipt = value['inference_receipt']
        before = receipt['source_before']
        after = {**before, 'model_ready': False, 'source_generation': before['source_generation'] + 1}
        authority = Authority(receipt['authority_instance'])
        authority.initialize()
        authority.discover([before])
        authority.bind(before)
        authority.observe(after)
        receipt.update(outcome='ERROR', error='backend-timeout', response_body=None, response_sha256=None,
                       content=None, tokens_predicted=0, source_after=after)
        value.update(outcome='ERROR', error='backend-timeout', source_record=after,
                     management_snapshot=authority.snapshot(), management_outcome='rejected')
        return value

    @staticmethod
    def seal(session, sources):
        """Rehash only; never repair a hostile schema, version, response or display."""
        path = session / 'session-result.json'
        result = json.loads(path.read_bytes())
        result['source_hashes'] = {name: hashlib.sha256((sources / name).read_bytes()).hexdigest()
                                   for name in result['source_hashes']}
        result['files'] = {name: hashlib.sha256((session / name).read_bytes()).hexdigest()
                           for name in result['files']}
        path.write_text(json.dumps(result), encoding='utf-8')

    @staticmethod
    def guidance(raw):
        start = raw.index(b'MAIN ask failed: ')
        start = raw.index(b'\n', start) + 1
        end = raw.index(b'aios> ', start)
        return start, end, raw[start:end]

    def family(self, schema, value=None):
        """Construct an explicit historical format fixture, not a historic capture.

        CLI8 and CLI9 share MAIN5 and source names. Each fixture retains its own
        literal version/source hashes; later seal operations do not relabel it.
        """
        self.serial += 1
        parent = self.root / ('case-' + str(self.serial))
        session, sources = parent / 'session', parent / 'sources'
        response = copy.deepcopy(value or agent('ask', error='unbound'))
        if schema == 7:
            response['schema_version'] = 4
            response.pop('space_context')
        before = copy.deepcopy(response)
        controller = mock.Mock(return_value=response)
        code, _ = historical_console_session(session, ['ask hello', 'exit'], schema=schema,
            input_stream=io.StringIO('ask hello\nexit\n'),
            output_stream=io.StringIO(), proc_root=self.proc, sys_root=self.sysfs,
            test_system='Linux', agent_control=controller)
        self.assertEqual(code, 0)
        controller.assert_called_once()
        self.assertEqual(response, before)
        version = {7: '0.7.0', 8: '0.8.0', 9: '0.9.0'}[schema]
        for name in verifier.SCHEMA_SOURCES[schema]:
            destination = sources / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / 'hosted/linux' / name).read_bytes())
        (sources / 'aios_console/__init__.py').write_text('VERSION = ' + repr(version) + '\n', encoding='utf-8')
        events_path = session / 'session.events.jsonl'
        events = [json.loads(line) for line in events_path.read_bytes().splitlines()]
        for event in events:
            event['schema_version'] = schema
        events[0]['data']['runtime_version'] = version
        events_path.write_text(''.join(json.dumps(row) + '\n' for row in events), encoding='utf-8')
        console = session / 'console.log'
        raw = console.read_bytes().replace(('AIOS Console ' + shell.VERSION).encode(), ('AIOS Console ' + version).encode())
        if schema in (7, 8):
            start, end, _ = self.guidance(raw)
            raw = raw[:start] + (OLD8 if schema == 8 else b'') + raw[end:]
        console.write_bytes(raw)
        path = session / 'session-result.json'
        result = json.loads(path.read_bytes())
        result.update(schema_version=schema, source_hashes=dict.fromkeys(verifier.SCHEMA_SOURCES[schema], ''))
        path.write_text(json.dumps(result), encoding='utf-8')
        self.seal(session, sources)
        return session, sources

    def check(self, session, sources, reason=None):
        result = verifier.verify_session(session, source_root=sources, expected_commands=['ask hello', 'exit'])
        self.assertEqual(result['outcome'], 'FAIL' if reason else 'PASS', result)
        if reason:
            self.assertIn(reason, str(result['reasons']))
            self.assertNotIn('artifact_hash:', str(result['reasons']))
            self.assertNotIn('source_hash:', str(result['reasons']))
        return result

    def test_cli7_8_9_retained_families_keep_distinct_error_display(self):
        for schema in (7, 8, 9):
            with self.subTest(schema=schema):
                session, sources = self.family(schema)
                self.check(session, sources)
                shown = self.guidance((session / 'console.log').read_bytes())[2]
                if schema == 7:
                    self.assertEqual(shown, b'')
                elif schema == 8:
                    self.assertEqual(shown, OLD8)
                else:
                    self.assertIn(b'before model execution', shown)
                    self.assertNotEqual(shown, OLD8)

    def test_rehashed_cross_version_guidance_is_rejected_in_both_directions(self):
        families = {schema: self.family(schema) for schema in (7, 8, 9)}
        original = {schema: (pair[0] / 'console.log').read_bytes() for schema, pair in families.items()}
        for schema, (session, sources) in families.items():
            self.check(session, sources)
            start, end, _ = self.guidance(original[schema])
            for other in families.keys() - {schema}:
                with self.subTest(schema=schema, other=other):
                    wrong = self.guidance(original[other])[2]
                    (session / 'console.log').write_bytes(original[schema][:start] + wrong + original[schema][end:])
                    self.seal(session, sources)
                    self.check(session, sources, 'transcript_mismatch')

    def test_rehashed_source_version_and_session_schema_must_match(self):
        for schema, other in ((8, '0.9.0'), (9, '0.8.0')):
            with self.subTest(schema=schema):
                session, sources = self.family(schema)
                self.check(session, sources)
                (sources / 'aios_console/__init__.py').write_text('VERSION = ' + repr(other) + '\n', encoding='utf-8')
                self.seal(session, sources)
                self.check(session, sources, 'console_source_version')

    def test_source_version_is_a_unique_literal_and_never_executed(self):
        session, sources = self.family(9)
        for source in (b"VERSION = '0.9.0'\nVERSION = '0.9.0'\n", b"VERSION: str = '0.9.0'\n",
                       b"VERSION = str('0.9.0')\n", b"VERSION =\n", b'VERSION = True\n',
                       b"VERSION = '0.9.0'\nfrom x import y as VERSION\n",
                       b"VERSION = '0.9.0'\nimport x as VERSION\n",
                       b"VERSION = '0.9.0'\nexec(\"VERSION = '0.8.0'\")\n",
                       b"VERSION = '0.9.0'\nglobals()['VERSION'] = '0.8.0'\n",
                       b"VERSION = '0.9.0'\nif True:\n VERSION = '0.8.0'\n"):
            with self.subTest(source=source):
                (sources / 'aios_console/__init__.py').write_bytes(source)
                self.seal(session, sources)
                self.check(session, sources, 'console_source_version')
        marker = self.root / 'must-not-exist'
        raw = ("VERSION = '0.9.0'\nraise RuntimeError('must not execute')\n"
               "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n").encode()
        with self.assertRaisesRegex(ValueError, 'console_source_version'):
            verifier.source_version(raw)
        self.assertFalse(marker.exists())
        self.assertEqual(verifier.source_version(b'"""Retained console."""\nVERSION = "0.8.0"\n'), '0.8.0')
        self.assertEqual(verifier.source_version(b'VERSION = "0.9.0"\n'), '0.9.0')

    def test_cli9_exact_reason_guidance_keeps_existing_errors_and_receipts(self):
        cases = ['prompt-invalid', 'space-budget', 'space-invalid', 'space-overflow', 'space-future',
                 'space-source-mismatch', 'orphan', 'unbound', 'stale', 'model-not-ready', 'source-exited',
                 'retired-instance', 'process-not-running', 'request-limit', 'unsupported-platform',
                 'rpc-timeout', 'protocol-error', 'peer-mismatch', 'state-io', 'backend-timeout', 'backend-failed']
        for error in cases:
            with self.subTest(error=error):
                state = 'UNSUPPORTED' if error == 'unsupported-platform' else 'ABSENT' if error == 'process-not-running' else 'RUNNING'
                session, sources = self.family(9, agent('ask', error=error, state=state))
                self.check(session, sources)
        session, sources = self.family(9, self.failed_receipt())
        self.check(session, sources)

    def test_unknown_outcome_cannot_be_rehashed_into_unsent_or_answer(self):
        for response in (agent('ask', error='rpc-timeout'), self.failed_receipt()):
            session, sources = self.family(9, response)
            self.check(session, sources)
            path = session / 'console.log'
            original = path.read_bytes()
            self.assertIn(b'the request outcome is unknown', original)
            start, end, _ = self.guidance(original)
            for wrong in (b'  The question was rejected before model execution.\n', b'MAIN answer:\n  It worked.\n'):
                with self.subTest(receipt=response['inference_receipt'] is not None, wrong=wrong):
                    path.write_bytes(original[:start] + wrong + original[end:])
                    self.seal(session, sources)
                    self.check(session, sources, 'transcript_mismatch')


if __name__ == '__main__':
    unittest.main()
