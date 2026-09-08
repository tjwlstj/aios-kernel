"""Selection transactions and real host locks, without booting an image.

The image-content contract is deliberately a seam: inspect_user,
inspect_source and validate_entry are patched here. These fixtures prove
pointer publication, failure preservation and OS ownership, not image
acceptance. The independent selection-contract suite covers image contents.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import image_host_lock as locks
import image_selection as selection


# Only this small Python helper is spawned. It never imports a VM launcher.
LOCK_WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from image_host_lock import image_key, named_lock
root, mode, value = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
key = image_key(value) if mode == 'image' else value
with named_lock(key, root=root):
    print('LOCKED', flush=True)
    sys.stdin.buffer.read(1)
"""


class SelectionTransactionsTests(unittest.TestCase):
    profile = 'local-model-cli'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = selection.SelectionStore(self.profile, root=self.root)
        self.entries = {}
        self.user_patch = patch.object(selection, 'inspect_user', side_effect=self.inspect_user)
        self.source_patch = patch.object(selection, 'inspect_source', return_value={'fixture': True})
        self.validate_patch = patch.object(selection, 'validate_entry', side_effect=self.validate_entry)
        self.inspect = self.user_patch.start()
        self.source = self.source_patch.start()
        self.validate = self.validate_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.validate_patch.stop)

    def inspect_user(self, directory, profile):
        self.assertEqual(profile, self.profile)
        try:
            return deepcopy(self.entries[Path(directory).resolve()])
        except KeyError as exc:
            raise ValueError('fixture: incomplete working copy') from exc

    def validate_entry(self, entry, profile):
        self.assertEqual(profile, self.profile)
        self.assertEqual(entry, self.entries[Path(entry['image_directory']).resolve()])
        return deepcopy(entry)

    def user(self, name, *, version='0.7', source=None, directory=None):
        directory = directory or self.root / 'operating-images' / name
        source = source or self.root / 'sources' / name
        source.mkdir(parents=True, exist_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'system.raw').write_bytes(('disk:' + name).encode('ascii'))
        (directory / 'boots').mkdir(exist_ok=True)
        (directory / 'boots' / 'retained.txt').write_bytes(('history:' + name).encode('ascii'))
        entry = {'image_directory': str(directory.resolve()), 'source_directory': str(source.resolve()),
                 'image_id': str(uuid.uuid4()), 'cli_version': version,
                 'manifest_sha256': 'a' * 64, 'source_verdict_sha256': 'b' * 64,
                 'working_copy_sha256': 'c' * 64}
        self.entries[directory.resolve()] = entry
        return deepcopy(entry)

    def image_files(self):
        result = {}
        for directory in self.entries:
            for path in directory.rglob('*'):
                if path.is_file():
                    info = path.stat()
                    result[str(path)] = (path.read_bytes(), info.st_size, info.st_mtime_ns)
        return result

    def state_file(self):
        info = self.store.path.stat()
        return self.store.path.read_bytes(), info.st_size, info.st_mtime_ns

    def install(self, current, previous=None, revision=1):
        return self.store.publish(current, previous, revision)

    def test_first_select_captures_verified_legacy_in_one_atomic_record(self):
        legacy = self.user('legacy', version='0.6', directory=self.store.legacy)
        newer = self.user('newer')
        images_before = self.image_files()
        replace = selection.os.replace
        observed = []

        def observe_replace(temporary, target):
            self.assertEqual(target, self.store.path)
            self.assertFalse(target.exists())
            pending = json.loads(Path(temporary).read_bytes())
            self.assertEqual(pending['current'], newer)
            self.assertEqual(pending['previous'], legacy)
            self.assertEqual(pending['revision'], 1)
            observed.append(pending)
            replace(temporary, target)

        with patch.object(selection.os, 'replace', side_effect=observe_replace):
            result = self.store.change('select', newer['image_directory'])
        self.assertTrue(result['changed'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.store.read(), observed[0])
        self.assertEqual(self.image_files(), images_before)
        self.inspect.assert_any_call(self.store.legacy, self.profile)

    def test_later_selection_retains_immediate_previous_and_all_image_files(self):
        older, current, newer = self.user('older'), self.user('current'), self.user('newer')
        self.install(current, older, revision=8)
        old_bytes = self.store.path.read_bytes()
        images_before = self.image_files()
        replace = selection.os.replace

        def observe_replace(temporary, target):
            self.assertEqual(target.read_bytes(), old_bytes)
            value = json.loads(Path(temporary).read_bytes())
            self.assertEqual((value['current'], value['previous'], value['revision']), (newer, current, 9))
            replace(temporary, target)

        with patch.object(selection.os, 'replace', side_effect=observe_replace) as replacement:
            result = self.store.change('select', newer['image_directory'])
        replacement.assert_called_once()
        self.assertEqual(result['state'], self.store.read())
        self.assertEqual(self.image_files(), images_before)

    def test_rollback_swaps_only_pointers_and_can_swap_back(self):
        older, newer = self.user('older', version='0.6'), self.user('newer')
        self.install(newer, older, revision=3)
        images_before = self.image_files()
        first = self.store.change('rollback')
        self.assertEqual((first['current'], first['previous'], first['state']['revision']), (older, newer, 4))
        second = self.store.change('rollback')
        self.assertEqual((second['current'], second['previous'], second['state']['revision']), (newer, older, 5))
        self.assertEqual(self.image_files(), images_before)
        self.inspect.assert_not_called()
        self.source.assert_not_called()
        self.assertEqual([call.args[0] for call in self.validate.call_args_list], [older, newer])

    def test_failed_current_can_roll_back_to_healthy_previous(self):
        older, current = self.user('healthy-older'), self.user('failed-current')
        self.install(current, older)
        failure = Path(current['image_directory']) / 'boots' / 'latest-failure.json'
        failure.write_text('{"outcome":"FAIL"}', encoding='ascii')
        before = self.image_files()

        def validate_only_healthy(entry, profile):
            if entry == current:
                raise ValueError('fixture: latest boot failed')
            return self.validate_entry(entry, profile)

        self.validate.side_effect = validate_only_healthy
        result = self.store.change('rollback')
        self.assertEqual(result['current'], older)
        self.validate.assert_called_once_with(older, self.profile)
        self.assertEqual(self.image_files(), before)

    def test_failed_previous_rejects_rollback_without_publishing(self):
        older, current = self.user('failed-older'), self.user('current')
        self.install(current, older)
        before, images_before = self.state_file(), self.image_files()
        self.validate.side_effect = ValueError('fixture: previous boot failed')
        with self.assertRaisesRegex(ValueError, 'previous boot failed'):
            self.store.change('rollback')
        self.assertEqual(self.state_file(), before)
        self.assertEqual(self.image_files(), images_before)

    def test_malformed_state_never_uses_healthy_legacy_fallback(self):
        self.user('legacy', directory=self.store.legacy)
        current = self.user('newer')
        valid = self.install(current)
        cases = [b'{truncated', b'{"schema_version":1,"schema_version":1}',
                 json.dumps({**valid, 'schema_version': True}).encode(),
                 json.dumps({**valid, 'revision': True}).encode(),
                 json.dumps({**valid, 'profile': 'basic-cli'}).encode(),
                 json.dumps({**valid, 'extra': 'unexpected'}).encode()]
        for raw in cases:
            with self.subTest(raw=raw):
                self.store.path.write_bytes(raw)
                self.inspect.reset_mock()
                with self.assertRaises((ValueError, TypeError)):
                    self.store.change('select', current['image_directory'])
                self.inspect.assert_not_called()
                self.assertEqual(self.store.path.read_bytes(), raw)

    def test_replace_failure_preserves_previous_state_and_candidate_temp_record(self):
        current, newer = self.user('current'), self.user('newer')
        self.install(current)
        before, images_before = self.state_file(), self.image_files()
        with patch.object(selection.os, 'replace', side_effect=OSError('injected replace failure')):
            with self.assertRaisesRegex(OSError, 'replace failure'):
                self.store.change('select', newer['image_directory'])
        self.assertEqual(self.state_file(), before)
        self.assertEqual(self.image_files(), images_before)
        self.assertEqual(self.store.read()['current'], current)
        temporary = list(self.store.path.parent.glob('*.tmp'))
        self.assertEqual(len(temporary), 1)
        self.assertEqual(json.loads(temporary[0].read_bytes())['current'], newer)

    def test_existing_partial_clone_is_rejected_and_preserved(self):
        current = self.user('current')
        self.install(current)
        source = self.root / 'new-base'
        source.mkdir()
        (source / 'verdict.json').write_bytes(b'fixture: source contract seam')
        target = source.with_name(source.name + '-user')
        target.mkdir()
        partial = target / 'system.raw'
        partial.write_bytes(b'partial copy must remain for diagnosis')
        before = self.state_file()
        with patch('qemu_image.clone_verified') as clone:
            with self.assertRaisesRegex(ValueError, 'incomplete working copy'):
                self.store.change('select', source)
        clone.assert_not_called()
        self.assertEqual(self.state_file(), before)
        self.assertEqual(partial.read_bytes(), b'partial copy must remain for diagnosis')
        self.source.assert_called_once_with(source.resolve(), self.profile)

    def test_clone_failure_preserves_partial_output_and_does_not_publish(self):
        current = self.user('current')
        self.install(current)
        source = self.root / 'new-base'
        source.mkdir()
        (source / 'verdict.json').write_bytes(b'fixture: source contract seam')
        target = source.with_name(source.name + '-user')
        before = self.state_file()

        def failed_clone(supplied, destination):
            self.assertEqual((supplied, destination), (source.resolve(), target.resolve()))
            self.assertEqual(self.state_file(), before)
            destination.mkdir()
            (destination / 'system.raw').write_bytes(b'copy interrupted')
            raise OSError('injected copy failure')

        with patch('qemu_image.clone_verified', side_effect=failed_clone):
            with self.assertRaisesRegex(OSError, 'copy failure'):
                self.store.change('select', source)
        self.assertEqual(self.state_file(), before)
        self.assertEqual((target / 'system.raw').read_bytes(), b'copy interrupted')
        self.inspect.assert_not_called()

    def test_wrong_source_copy_is_rejected_without_publishing(self):
        current = self.user('current')
        self.install(current)
        source = self.root / 'new-base'
        source.mkdir()
        (source / 'verdict.json').write_bytes(b'fixture: source contract seam')
        self.user('wrong-source', directory=source.with_name(source.name + '-user'))
        before, images_before = self.state_file(), self.image_files()
        with self.assertRaisesRegex(ValueError, 'belongs to another source'):
            self.store.change('select', source)
        self.assertEqual(self.state_file(), before)
        self.assertEqual(self.image_files(), images_before)

    def test_selecting_same_current_does_not_rewrite_state(self):
        older, current = self.user('older'), self.user('current')
        self.install(current, older, revision=6)
        before, images_before = self.state_file(), self.image_files()
        with patch.object(selection.os, 'replace') as replace:
            result = self.store.change('select', current['image_directory'])
        self.assertFalse(result['changed'])
        self.assertEqual(result['state']['revision'], 6)
        replace.assert_not_called()
        self.assertEqual(self.state_file(), before)
        self.assertEqual(self.image_files(), images_before)

    def test_same_directory_with_changed_metadata_is_not_silently_reselected(self):
        current = self.user('current')
        self.install(current)
        before = self.state_file()
        self.entries[Path(current['image_directory'])]['working_copy_sha256'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'changed selection metadata'):
            self.store.change('select', current['image_directory'])
        self.assertEqual(self.state_file(), before)

    def test_fault_image_is_rejected_before_content_inspection(self):
        current, target = self.user('current'), self.user('fault-image')
        self.install(current)
        (Path(target['image_directory']) / 'fault-instrumentation.json').write_bytes(b'{}')
        before = self.state_file()
        with self.assertRaisesRegex(ValueError, 'test images cannot be selected'):
            self.store.change('select', target['image_directory'])
        self.inspect.assert_not_called()
        self.assertEqual(self.state_file(), before)

    def test_missing_current_directory_keeps_rollback_closed_and_state_intact(self):
        older, current = self.user('older'), self.user('current')
        self.install(current, older)
        before = self.state_file()
        directory = Path(current['image_directory'])
        relocated = directory.with_name('moved-current')
        directory.rename(relocated)
        with self.assertRaises(FileNotFoundError):
            self.store.change('rollback')
        self.validate.assert_not_called()
        self.assertEqual(self.state_file(), before)
        self.assertEqual((relocated / 'system.raw').read_bytes(), b'disk:current')

    def test_revision_exhaustion_preserves_state_without_replace(self):
        current, newer = self.user('current'), self.user('newer')
        self.install(current, revision=2**63 - 1)
        before = self.state_file()
        with patch.object(selection.os, 'replace') as replace:
            with self.assertRaisesRegex(ValueError, 'revision exhausted'):
                self.store.change('select', newer['image_directory'])
        replace.assert_not_called()
        self.assertEqual(self.state_file(), before)

    def test_cli_invalid_action_arguments_return_failure_without_mutation(self):
        current = self.user('current')
        self.install(current)
        before = self.state_file()
        cases = [(['select', '--agent'], 'select requires an image'),
                 (['rollback', '--agent', '--image-dir', current['image_directory']], 'rollback takes no image'),
                 (['show', '--agent', '--image-dir', current['image_directory']], 'Selection takes no image'),
                 (['show', '--agent', '--offline'], 'offline applies only to Run')]
        for arguments, reason in cases:
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(sys, 'argv', ['image_selection.py', *arguments]), \
                     patch.object(selection, 'storage_root', return_value=self.root), \
                     patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr):
                    self.assertEqual(selection.main(), 1)
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn(reason, stderr.getvalue())
                self.assertEqual(self.state_file(), before)
        self.inspect.assert_not_called()

    def test_cli_success_json_reports_published_state_and_nonzero_on_failed_rollback(self):
        current, newer = self.user('current'), self.user('newer')
        self.install(current)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['image_selection.py', 'select', '--agent', '--json',
                                       '--image-dir', newer['image_directory']]), \
             patch.object(selection, 'storage_root', return_value=self.root), \
             patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr):
            self.assertEqual(selection.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result, {'action': 'select', 'changed': True, 'current': newer,
                                 'previous': current, 'state': self.store.read()})
        self.assertEqual(stderr.getvalue(), '')
        before = self.state_file()
        self.validate.side_effect = ValueError('fixture: previous boot failed')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['image_selection.py', 'rollback', '--agent', '--json']), \
             patch.object(selection, 'storage_root', return_value=self.root), \
             patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr):
            self.assertEqual(selection.main(), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('previous boot failed', stderr.getvalue())
        self.assertEqual(self.state_file(), before)


class HostImageLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = self.root / 'image'
        self.image.mkdir()

    @contextmanager
    def owner(self, value, *, mode='image'):
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', LOCK_WORKER,
                                    str(TOOLS), str(self.root), mode, str(value)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results = queue.Queue()
        reader = threading.Thread(target=lambda: results.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            try:
                ready = results.get(timeout=10)
            except queue.Empty as exc:
                raise AssertionError('lock helper did not become ready within 10 seconds') from exc
            if ready not in (b'LOCKED\n', b'LOCKED\r\n'):
                process.wait(timeout=5)
                self.fail('lock helper failed: ' + process.stderr.read().decode('utf-8', errors='replace'))
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            reader.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def lock_path(self, key):
        return self.root / 'host-locks' / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.lock')

    def test_another_process_cannot_acquire_until_owner_releases(self):
        with self.owner(self.image) as process:
            key = locks.image_key(self.image)
            path = self.lock_path(key)
            identity = path.stat().st_ino
            with self.assertRaisesRegex(ValueError, 'operation is busy'):
                with locks.image_lock(self.image, root=self.root):
                    self.fail('a competing owner acquired the active lock')
            process.stdin.write(b'x')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 0)
            with locks.image_lock(self.image, root=self.root):
                self.assertEqual(path.stat().st_ino, identity)
            self.assertTrue(path.is_file())

    def test_owner_death_releases_os_lock_without_removing_lock_file(self):
        with self.owner(self.image) as process:
            path = self.lock_path(locks.image_key(self.image))
            identity = path.stat().st_ino
            process.kill()
            self.assertNotEqual(process.wait(timeout=5), 0)
            with locks.image_lock(self.image, root=self.root):
                self.assertEqual(path.stat().st_ino, identity)
            self.assertTrue(path.is_file())

    def test_different_path_aliases_compete_for_the_same_directory_identity(self):
        # A real '..' alias works without symlink privileges on Windows and Unix.
        transit = self.root / 'transit'
        transit.mkdir()
        alias = transit / '..' / self.image.name
        self.assertNotEqual(str(alias), str(self.image))
        self.assertTrue(alias.samefile(self.image))
        self.assertEqual(locks.image_key(alias), locks.image_key(self.image))
        with self.owner(alias):
            with self.assertRaisesRegex(ValueError, 'operation is busy'):
                with locks.image_lock(self.image, root=self.root):
                    self.fail('a path alias bypassed the lock')
            other = self.root / 'other-image'
            other.mkdir()
            with locks.image_lock(other, root=self.root):
                self.assertNotEqual(locks.image_key(other), locks.image_key(self.image))

    def test_profile_lock_blocks_selection_without_mutating_state_or_images(self):
        store = selection.SelectionStore('local-model-cli', root=self.root)
        before = list(self.image.iterdir())
        with self.owner('image-selection:local-model-cli', mode='named'):
            with patch.object(selection, 'inspect_user') as inspect:
                with self.assertRaisesRegex(ValueError, 'operation is busy'):
                    store.change('select', self.image)
            inspect.assert_not_called()
        self.assertFalse(store.path.exists())
        self.assertEqual(list(self.image.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
