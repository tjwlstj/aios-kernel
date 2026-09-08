"""Rehashed MAIN v4 records must retain their explicit execution contract."""
import copy
import unittest

import test_hosted_agent_verifier as legacy
from verify_agent import MANAGED_SOURCES, digest


class ManagedAgentVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy.AgentVerifierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        for name in MANAGED_SOURCES:
            path = f.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(('retained fixture ' + name).encode())
        f.start['schema_version'] = f.result['schema_version'] = 4
        f.start['source_hashes'] = {name: digest((f.sources / name).read_bytes()) for name in MANAGED_SOURCES}
        for event in f.events:
            event.update(schema_version=4, resource_file=None)
        for receipt in (f.warmup, f.user):
            receipt.update(schema_version=2, backend_execution=None)
        (f.run / 'resources').mkdir()
        self.binding = {'schema_version': 1, 'capture_kind': 'fixture', 'initial_proof': None, 'descriptor': None}
        self.save()

    def save(self):
        f = self.fixture
        f.save()
        (f.run / 'backend-binding.json').write_bytes(legacy.encoded(self.binding))
        f.result['files']['backend-binding.json'] = digest((f.run / 'backend-binding.json').read_bytes())
        (f.run / 'result.json').write_bytes(legacy.encoded(f.result))

    def rejected(self, reason):
        self.save()
        verdict = self.fixture.verify()
        self.assertEqual(verdict['outcome'], 'FAIL', verdict)
        self.assertIn(reason, verdict['reasons'][0])

    def test_explicit_null_fixture_replays_but_cannot_be_live(self):
        result = self.fixture.verify()
        self.assertEqual(result['outcome'], 'PASS', result)
        self.assertEqual(self.fixture.verify(require_live=True)['outcome'], 'FAIL')

    def test_all_live_labels_cannot_hide_missing_execution_binding(self):
        self.fixture.start['capture_kind'] = self.fixture.result['capture_kind'] = self.binding['capture_kind'] = 'live'
        self.rejected('execution_binding_missing')

    def test_legacy_success_receipt_cannot_replace_v4_execution(self):
        self.fixture.warmup['schema_version'] = 1
        self.fixture.warmup.pop('backend_execution')
        self.rejected('execution_receipt_schema')

    def test_receipt_execution_field_is_mandatory_even_for_fixture(self):
        self.fixture.user.pop('backend_execution')
        self.rejected('receipt_keys')

    def test_invalidation_requires_a_selected_backend(self):
        event = self.fixture.events[4]
        event.update(event='BACKEND_INVALIDATED', action=None, outcome='ERROR', error='backend-changed', receipt_file=None)
        self.rejected('unexpected_backend_invalidation')

    def test_binding_capture_cannot_be_relabelled_after_rehash(self):
        self.binding['capture_kind'] = 'live'
        self.rejected('execution_binding_schema')

    def test_source_list_cannot_omit_execution_or_controller_code(self):
        for name in ('aios_agent/backend_binding.py', 'aios_backend/client.py'):
            original = copy.deepcopy(self.fixture.start['source_hashes'])
            self.fixture.start['source_hashes'].pop(name)
            self.rejected('agent_sources')
            self.fixture.start['source_hashes'] = original


if __name__ == '__main__':
    unittest.main()
