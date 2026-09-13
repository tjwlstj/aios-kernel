"""File-only exports of generated MAIN evidence; no live runtime is claimed."""
from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_hosted_managed_agent_verifier as managed
import test_hosted_space_verifier as space_fixture
from test_hosted_agent_verifier import encoded
from verify_agent import _verify_export_inventory, digest, verify_run


OPTIONAL = ('requests', 'resources', 'spaces')


def file_map(directory):
    return {path.relative_to(directory).as_posix(): digest(path.read_bytes())
            for path in directory.rglob('*') if path.is_file() and not path.is_symlink()}


def export_files(source, target):
    """Mirror export_guest's regular-file inventory, including absent empty dirs."""
    target.mkdir(parents=True)
    for path in source.rglob('*'):
        if path.is_file() and not path.is_symlink():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())


class ExportInventoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = space_fixture.SpaceVerifierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.exports = Path(self.temp.name)
        self.export_count = 0

    def prepare(self, *, requests=True, spaces=False, resources=False):
        f = self.f
        if not spaces:
            f.events = [row for row in f.events if row['action'] != 'space']
        if not requests:
            f.events = [row for row in f.events if row['action'] != 'ask']

            def remove_completion(value):
                if type(value) is dict:
                    for key, item in value.items():
                        if key == 'completed_requests' and item == 2:
                            value[key] = 1
                        else:
                            remove_completion(item)
                elif type(value) is list:
                    for item in value:
                        remove_completion(item)

            for value in (f.source, f.events, f.result, vars(f.authority)):
                remove_completion(value)
        resource_path = 'resources/' + str(uuid.uuid4()) + '.json'
        if resources:
            row = copy.deepcopy(f.events[3])
            row.update(action='resources-status', receipt_file=None, resource_file=resource_path)
            f.events.insert(4, row)
            value = {'schema_version': 1, 'action': 'status', 'outcome': 'OK', 'error': None,
                     'relation': None, 'relation_current': False, 'observation': None,
                     'observation_only': True, 'ownership_valid': False,
                     'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture'}
            (f.run / resource_path).write_bytes(encoded(value))
        for index, row in enumerate(f.events, 1):
            row['sequence'] = index
        # This explicitly constructs a fixture; its events and inventories are
        # finalized before export, never repaired while verifying hostile data.
        self.fixture.fixture.save()
        if not requests:
            (f.run / f.user_path).unlink()
            f.result['files'].pop(f.user_path)
        if spaces:
            f.result['files'][self.fixture.space_path] = digest((f.run / self.fixture.space_path).read_bytes())
        else:
            (f.run / self.fixture.space_path).unlink()
        if resources:
            f.result['files'][resource_path] = digest((f.run / resource_path).read_bytes())
        (f.run / 'result.json').write_bytes(encoded(f.result))
        self.assertEqual(f.verify()['outcome'], 'PASS', f.verify())

    def export(self, source=None):
        source = source or self.f.run
        before = file_map(source)
        target = self.exports / str(self.export_count) / source.name
        self.export_count += 1
        export_files(source, target)
        self.assertEqual(file_map(target), before)
        self.assertEqual(file_map(source), before)
        return target

    def check(self, target, *, outcome='PASS', reason=None, sources=None):
        before = file_map(target)
        result = verify_run(target, sources or self.f.sources)
        self.assertEqual(file_map(target), before, 'verification must not rewrite evidence')
        self.assertEqual(result['outcome'], outcome, result)
        if reason:
            self.assertIn(reason, result['reasons'][0])
        return result

    def symlink(self, link, target, *, directory=True):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            self.skipTest('symlink privilege unavailable: ' + str(exc))

    def test_main5_implicit_observation_survives_file_only_export(self):
        self.prepare()
        target = self.export()
        result = self.check(target)
        self.assertEqual(len(result['requests']), 1)
        self.assertEqual(result['space_results'], [])
        for name in ('resources', 'spaces'):
            self.assertFalse((target / name).exists(), 'verifier must not recreate empty directories')
        self.assertFalse(result['process_exit_verified'])
        self.assertFalse(result['model_bytes_verified'])

    def test_all_empty_optional_directories_can_be_absent(self):
        self.prepare(requests=False)
        target = self.export()
        self.assertTrue(all(not (target / name).exists() for name in OPTIONAL))
        self.assertEqual(self.check(target)['requests'], [])
        self.assertTrue(all(not (target / name).exists() for name in OPTIONAL))

    def test_present_empty_directories_remain_valid(self):
        self.prepare(requests=False)
        target = self.export()
        for name in OPTIONAL:
            (target / name).mkdir()
        self.check(target)

    def test_historical_main4_empty_resources_export_remains_valid(self):
        fixture = managed.ManagedAgentVerifierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture.fixture
        target = self.export(f.run)
        self.assertFalse((target / 'resources').exists())
        self.check(target, sources=f.sources)

    def test_existing_file_cannot_replace_empty_directory(self):
        self.prepare(requests=False)
        for name in OPTIONAL:
            with self.subTest(name=name):
                target = self.export()
                (target / name).write_bytes(b'')
                self.check(target, outcome='FAIL', reason=name + '_directory')

    def test_extra_regular_file_or_empty_subdirectory_is_not_filtered_out(self):
        self.prepare(requests=False)
        for name in OPTIONAL:
            for is_directory in (False, True):
                with self.subTest(name=name, is_directory=is_directory):
                    target = self.export()
                    (target / name).mkdir()
                    extra = target / name / 'unrecorded'
                    extra.mkdir() if is_directory else extra.write_bytes(b'{}\n')
                    self.check(target, outcome='FAIL', reason='unaccounted_' + name)

    def test_directory_symlink_including_dangling_is_never_absence(self):
        self.prepare(requests=False)
        for name in OPTIONAL:
            for dangling in (False, True):
                with self.subTest(name=name, dangling=dangling):
                    target = self.export()
                    destination = target.parent / 'external-empty'
                    if not dangling:
                        destination.mkdir()
                    self.symlink(target / name, destination)
                    self.check(target, outcome='FAIL', reason=name + '_directory')

    def test_extra_dangling_child_is_in_inventory(self):
        self.prepare(requests=False)
        for name in OPTIONAL:
            with self.subTest(name=name):
                target = self.export()
                (target / name).mkdir()
                self.symlink(target / name / 'unrecorded', target.parent / 'absent')
                self.check(target, outcome='FAIL', reason='unaccounted_' + name)

    def test_recorded_requests_resources_and_spaces_are_required(self):
        self.prepare(spaces=True, resources=True)
        self.check(self.export())
        for name in OPTIONAL:
            for whole_directory in (False, True):
                with self.subTest(name=name, whole_directory=whole_directory):
                    target = self.export()
                    child = next((target / name).iterdir())
                    child.unlink()
                    if whole_directory:
                        (target / name).rmdir()
                    self.check(target, outcome='FAIL', reason='artifact_file:')

    def test_expected_leaf_directory_or_symlink_is_not_a_record(self):
        self.prepare(spaces=True, resources=True)
        for name in OPTIONAL:
            for is_link in (False, True):
                with self.subTest(name=name, is_link=is_link):
                    target = self.export()
                    child = next((target / name).iterdir())
                    raw = child.read_bytes()
                    child.unlink()
                    if is_link:
                        other = target.parent / 'external-record.json'
                        other.write_bytes(raw)
                        self.symlink(child, other, directory=False)
                    else:
                        child.mkdir()
                    self.check(target, outcome='FAIL', reason='artifact_file:')

    def test_result_only_extra_file_cannot_join_without_event_after_rehash(self):
        self.prepare(requests=False)
        for name in OPTIONAL:
            with self.subTest(name=name):
                target = self.export()
                (target / name).mkdir()
                relative = name + '/' + str(uuid.uuid4()) + '.json'
                (target / relative).write_bytes(b'{}\n')
                result = json.loads((target / 'result.json').read_bytes())
                result['files'][relative] = digest((target / relative).read_bytes())
                (target / 'result.json').write_bytes(encoded(result))
                self.check(target, outcome='FAIL', reason='result_files')

    def test_recorded_hashes_and_event_inventory_are_still_exact(self):
        self.prepare(spaces=True, resources=True)
        for name in OPTIONAL:
            with self.subTest(name=name):
                target = self.export()
                child = next((target / name).iterdir())
                child.write_bytes(child.read_bytes() + b' ')
                self.check(target, outcome='FAIL', reason='artifact_hash:')
                target = self.export()
                result = json.loads((target / 'result.json').read_bytes())
                relative = next(path for path in result['files'] if path.startswith(name + '/'))
                result['files'].pop(relative)
                (target / 'result.json').write_bytes(encoded(result))
                self.check(target, outcome='FAIL', reason='result_files')

    def test_private_inventory_rejects_absence_for_nonempty_expected_set(self):
        for name in OPTIONAL:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name + '_directory'):
                _verify_export_inventory(self.exports, name, {name + '/record.json'})

    def test_lstat_symlink_seam_is_rejected_even_when_exists_is_false(self):
        """Portable branch coverage; actual symlink tests above need OS privilege."""
        self.prepare(requests=False)
        original = Path.lstat
        for name in OPTIONAL:
            target = self.export()
            optional = target / name
            self.assertFalse(optional.exists())

            def lstat(path):
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777) if path == optional else original(path)

            with self.subTest(name=name), patch.object(Path, 'lstat', lstat):
                self.check(target, outcome='FAIL', reason=name + '_directory')

    def test_inventory_seam_counts_dangling_child_without_following_it(self):
        """A copied directory entry remains extra even if its target is absent."""
        self.prepare(requests=False)
        original = Path.iterdir
        for name in OPTIONAL:
            target = self.export()
            optional = target / name
            optional.mkdir()
            dangling = optional / 'unrecorded-dangling'

            def iterdir(path):
                return iter([dangling]) if path == optional else original(path)

            with self.subTest(name=name), patch.object(Path, 'iterdir', iterdir):
                self.check(target, outcome='FAIL', reason='unaccounted_' + name)

    def test_expected_leaf_symlink_seam_cannot_bypass_regular_file_read(self):
        self.prepare(spaces=True, resources=True)
        original = Path.is_symlink
        for name in OPTIONAL:
            target = self.export()
            child = next((target / name).iterdir())

            def is_symlink(path):
                return True if path == child else original(path)

            with self.subTest(name=name), patch.object(Path, 'is_symlink', is_symlink):
                self.check(target, outcome='FAIL', reason='artifact_file:')


if __name__ == '__main__':
    unittest.main()
