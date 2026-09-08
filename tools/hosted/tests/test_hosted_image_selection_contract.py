"""Selection pins against complete synthetic image archives.

The only seam changes require_live to False in existing verifier entry points.
Every manifest, archive, rendered session, hash, disk and path join remains real;
these fixtures make no claim of live guest execution.
"""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import image_selection_contract as contract
import verify_image as image
import test_hosted_image_verifier as fixtures
from test_hosted_image_verifier import get, put


class ImageSelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ImageVerifierTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.source = self.fixture.root
        temporary = tempfile.TemporaryDirectory(prefix='aios-user-selection-'); self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.user = self.parent / 'user'; self.user.mkdir()
        put(self.source / 'boot.json', self.fixture.config)
        put(self.source / 'build-result.json', {'schema_version': 1, 'outcome': 'PASS'})
        self.real_source, self.real_user, self.real_archive = image.verify_image, image.verify_operating_boot, image.validate_archive
        for name, original in (('verify_image', self.real_source), ('verify_operating_boot', self.real_user),
                               ('validate_archive', self.real_archive)):
            def with_fixture(*args, _original=original, **kwargs):
                return _original(*args, **{**kwargs, 'require_live': False})
            active = patch.object(image, name, side_effect=with_fixture)
            active.start(); self.addCleanup(active.stop)
        self.source_verdict = image.verify_image(self.source)
        self.assertEqual(self.source_verdict['outcome'], 'PASS', self.source_verdict)
        put(self.source / 'verdict.json', self.source_verdict)
        for name in ('image-manifest.json', 'boot.json', 'build-result.json', 'system.raw'):
            shutil.copyfile(self.source / name, self.user / name)
        for name in ('runtime-source', 'installation-files'):
            shutil.copytree(self.source / name, self.user / name)
        original = get(self.source / 'boots/boot-02/disk-after.json')
        self.initial = {**original, 'path': str(self.user / 'system.raw')}
        put(self.user / 'image-before.json', self.initial)
        self.working = {'schema_version': 1, 'source_directory': str(self.source), 'source_image': original,
            'initial_image': self.initial, 'source_verdict_sha256': image.digest((self.source / 'verdict.json').read_bytes())}
        put(self.user / 'working-copy.json', self.working)
        self.fixture.root = self.user
        self.fixture.disk = self.user / 'system.raw'
        self.fixture.disk_value = self.initial
        self.fixture.boots = []

    def inspect(self):
        return contract.inspect_user(self.user, 'basic-cli')

    def add_boot(self):
        index = len(self.fixture.boots) + 1
        path = self.fixture.make_boot(index, offline=True, commands=['about', 'exit'], use_service=False)
        (self.user / 'system.raw').write_bytes(bytes([64 + index]) * self.initial['size_bytes'])
        after = {**self.initial, 'sha256': image.digest((self.user / 'system.raw').read_bytes())}
        put(path / 'disk-after.json', after)
        self.fixture.disk_value = after
        verdict = image.verify_operating_boot(self.user, path)
        self.assertEqual(verdict['outcome'], 'PASS', verdict)
        put(path / 'verdict.json', verdict)
        return path

    def test_unused_copy_has_exact_pins_and_real_path_aliases_validate(self):
        source = contract.inspect_source(self.source, 'basic-cli')
        self.assertEqual(set(source), {'image_id', 'cli_version', 'manifest_sha256', 'source_verdict_sha256'})
        self.assertEqual(source['cli_version'], '0.7.0')
        entry = self.inspect()
        self.assertEqual(set(entry), contract.ENTRY_KEYS)
        alias = {**entry, 'image_directory': str(self.user / '.'), 'source_directory': str(self.source / '.')}
        self.assertTrue(contract.validate_entry(alias, 'basic-cli').samefile(self.user))
        # Calling the real live verifier directly still rejects this fixture.
        self.assertEqual(self.real_source(self.source)['outcome'], 'FAIL')

    def test_used_disk_and_multiple_complete_boots_use_terminal_hash(self):
        entry = self.inspect()
        self.add_boot(); self.add_boot()
        self.assertNotEqual(image.digest((self.user / 'system.raw').read_bytes()), self.initial['sha256'])
        self.assertEqual(self.inspect(), entry)
        self.assertTrue(contract.validate_entry(entry, 'basic-cli').samefile(self.user))

    def test_missing_changed_or_extra_retained_installation_files_reject(self):
        target = self.user / 'runtime-source/aios_console/__init__.py'
        original = target.read_bytes()
        for raw in (b'VERSION="9.9.9"\n', b''):
            target.write_bytes(raw)
            with self.assertRaises(ValueError): self.inspect()
        target.write_bytes(original)
        extra = self.user / 'runtime-source/extra.py'; extra.write_bytes(b'# extra\n')
        with self.assertRaisesRegex(ValueError, 'installed_file_set'): self.inspect()
        extra.unlink()
        (self.user / 'installation-files/kernel.txt').unlink()
        with self.assertRaises(ValueError): self.inspect()

    def test_resealed_manifest_and_runtime_change_cannot_redefine_source_copy(self):
        target = self.user / 'runtime-source/aios_console/__init__.py'
        target.write_bytes(b'VERSION="9.9.9"\n')
        manifest = get(self.user / 'image-manifest.json')
        manifest['runtime_files']['aios_console/__init__.py'] = image.digest(target.read_bytes())
        put(self.user / 'image-manifest.json', manifest)
        put(self.user / 'installation-files/installed-runtime.json', manifest['runtime_files'])
        manifest['installation_files']['installed-runtime.json'] = image.digest((self.user / 'installation-files/installed-runtime.json').read_bytes())
        put(self.user / 'image-manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'working_installed_bytes:image-manifest.json'): self.inspect()

    def test_unused_corrupt_disk_and_empty_or_partial_boot_reject(self):
        disk = self.user / 'system.raw'; original = disk.read_bytes()
        disk.write_bytes(b'X' * len(original))
        with self.assertRaisesRegex(ValueError, 'unused_clone_disk'): self.inspect()
        disk.write_bytes(original)
        boots = self.user / 'boots'; boots.mkdir()
        with self.assertRaisesRegex(ValueError, 'boot_sequence'): self.inspect()
        (boots / 'boot-01').mkdir()
        with self.assertRaises(ValueError): self.inspect()

    def test_failed_latest_boot_cannot_fall_back_to_older_success(self):
        self.add_boot(); latest = self.add_boot()
        put(latest / 'vm-result.json', {'schema_version': 1, 'process_exit_code': 1, 'host_killed': False,
                                      'shutdown_observed': True, 'runner_error': None})
        with self.assertRaisesRegex(ValueError, 'user_vm_failed'): self.inspect()

    def test_next_boot_capacity_counts_normal_source_history_too(self):
        boots = self.user / 'boots'; boots.mkdir()
        for index in range(1, contract.MAX_HISTORIES - len(self.source_verdict['boots']) + 1):
            (boots / ('boot-%02d' % index)).mkdir()
        with self.assertRaisesRegex(ValueError, 'next_boot_history_capacity'): self.inspect()

    def test_missing_intermediate_boot_or_rehashed_disk_chain_rejects(self):
        first, latest = self.add_boot(), self.add_boot()
        parked = self.user / 'parked-boot'
        first.rename(parked)
        with self.assertRaisesRegex(ValueError, 'boot_sequence'): self.inspect()
        parked.rename(first)
        before = get(latest / 'disk-before.json'); before['sha256'] = 'f' * 64
        put(latest / 'disk-before.json', before)
        with self.assertRaisesRegex(ValueError, 'user_disk_chain'): self.inspect()

    def test_fully_resealed_history_and_worker_failure_reject(self):
        latest = self.add_boot()
        boot = get(latest / 'archive/boot.json')
        next(iter(boot['previous_boots'].values()))['root_result_sha256'] = 'f' * 64
        put(latest / 'archive/boot.json', boot); self.fixture.seal(latest)
        with self.assertRaisesRegex(ValueError, 'prior_boot_evidence'): self.inspect()
        boot['previous_boots'] = {row['boot_id']: contract.history_entry(self.source / 'boots'
            / ('boot-%02d' % (index + 1)) / 'archive') for index, row in enumerate(self.source_verdict['boots'])}
        put(latest / 'archive/boot.json', boot)
        root = get(latest / 'archive/root-result.json'); root['worker_exit_verified'] = False
        put(latest / 'archive/root-result.json', root); self.fixture.seal(latest)
        with self.assertRaisesRegex(ValueError, 'root_result_failed'): self.inspect()

    def test_source_profile_test_clone_and_saved_pin_changes_reject(self):
        with self.assertRaisesRegex(ValueError, 'source_profile'):
            contract.inspect_source(self.source, 'local-model-cli')
        entry = self.inspect()
        for key in ('image_id', 'cli_version', 'manifest_sha256', 'source_verdict_sha256', 'working_copy_sha256'):
            with self.subTest(key=key):
                changed = {**entry, key: 'changed'}
                with self.assertRaisesRegex(ValueError, 'entry_pin_changed'):
                    contract.validate_entry(changed, 'basic-cli')
        put(self.user / 'fault-instrumentation.json', {'test_only': True})
        with self.assertRaisesRegex(ValueError, 'expected_fault_image_not_selectable'): self.inspect()
        put(self.source / 'fault-instrumentation.json', {'test_only': True})
        with self.assertRaisesRegex(ValueError, 'expected_fault_image_not_selectable'):
            contract.inspect_source(self.source, 'basic-cli')

    def test_working_record_foreign_source_disk_and_source_verdict_drift_reject(self):
        foreign = self.parent / 'foreign'; foreign.mkdir()
        (foreign / 'system.raw').write_bytes((self.source / 'system.raw').read_bytes())
        value = copy.deepcopy(self.working)
        value['source_image']['path'] = str(foreign / 'system.raw')
        put(self.user / 'working-copy.json', value)
        with self.assertRaisesRegex(ValueError, 'working_source_disk'): self.inspect()
        put(self.user / 'working-copy.json', self.working)
        put(self.source / 'verdict.json', {**self.source_verdict, 'image_id': 'changed'})
        with self.assertRaisesRegex(ValueError, 'source_verdict_replay'): self.inspect()


class VersionContractTests(unittest.TestCase):
    def test_literal_versions_are_read_without_execution(self):
        self.assertEqual(contract.cli_version(b'VERSION = "0.6.0"\n'), '0.6.0')
        self.assertEqual(contract.cli_version(b'VERSION = "0.7.0"\n'), '0.7.0')
        for source in (b'VERSION = compute()\n', b'VERSION = "0.7.0"\nVERSION = "9.9.9"\n',
                       b'VERSION = 7\n', b'VERSION = "0.7.0"\nVERSION += "x"\n'):
            with self.assertRaises(ValueError): contract.cli_version(source)

    def test_retained_history_capacity_is_a_fixed_nonexecuted_contract(self):
        self.assertEqual(contract.history_capacity(b'MAX_HISTORIES = 512\n'), 512)
        for raw in (b'MAX_HISTORIES = 1024\n', b'MAX_HISTORIES = True\n', b'MAX_HISTORIES = compute()\n'):
            with self.assertRaisesRegex(ValueError, 'history_capacity_contract'):
                contract.history_capacity(raw)


if __name__ == '__main__':
    unittest.main()
