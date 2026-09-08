"""Negative contracts for the separate test image; no VM or model is launched."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_image as image
import verify_image_recovery as verifier
from test_hosted_backend_recovery_verifier import recovery_fixture, sample


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def snap(path, raw, inode, *, mode=0o644, at=1):
    return {'path': path, 'uid': 0, 'gid': 0, 'mode': mode, 'nlink': 1, 'device': 123, 'inode': inode,
        'size_bytes': len(raw), 'mtime_ns': at, 'ctime_ns': at, 'sha256': image.digest(raw),
        'data_base64': base64.b64encode(raw).decode('ascii')}


def instrumentation():
    initial = b'::sysinit:/sbin/openrc sysinit\nttyS0::once:/usr/local/sbin/aios-start-system\n'
    modified = initial + verifier.INITTAB_LINE
    hashes = [(image.digest(raw) + '\n').encode('ascii') for raw in (initial, modified)]
    helper, hook, config = b'# exact installed test helper\n', b'#!/bin/sh\nexec aios-image-boot\n', b'{"profile":"local-model"}\n'
    base = {'schema_version': 2, 'image_id': str(uuid.uuid4()), 'profile': 'local-model-cli',
        'runtime_files': {name: 'a' * 64 for name in image.IMAGE_SOURCES},
        'boot_config_sha256': image.digest(config), 'installation_files': {'inittab.sha256': image.digest(hashes[0])}}
    current = copy.deepcopy(base)
    current['image_id'] = str(uuid.uuid4())
    current['installation_files']['inittab.sha256'] = image.digest(hashes[1])
    base_raw, current_raw = encoded(base), encoded(current)
    pairs = [(initial, modified), tuple(hashes), (base_raw, current_raw), (None, helper)]
    value = {'schema_version': 1, 'scenario': verifier.SCENARIO, 'test_only': True, 'source_only': True,
        'base_image_id': base['image_id'], 'image_id': current['image_id'], 'base_manifest_sha256': image.digest(base_raw),
        'manifest_sha256': image.digest(current_raw), 'runtime_files': base['runtime_files'], 'changes': [],
        'preserved': {name: snap(name, raw, 100 + index) for index, (name, raw) in enumerate([
            ('/usr/local/sbin/aios-start-system', hook), ('/etc/aios/boot.json', config)])}}
    for index, (name, (before, after)) in enumerate(zip(verifier.CHANGED, pairs)):
        value['changes'].append({'path': name, 'before': snap(name, before, index + 1) if before is not None else None,
            'after': snap(name, after, index + 1, at=2, mode=0o444 if index == 3 else 0o644)})
    return value, base_raw, current_raw, helper, {'boot_hook_hash': image.digest(hook), 'boot_config_hash': image.digest(config)}


class InstrumentationContractTests(unittest.TestCase):
    def test_exact_four_changes_and_preserved_entry_pass(self):
        value, before, after, helper, kwargs = instrumentation()
        verifier.validate_instrumentation(value, before, after, helper, **kwargs)

    def test_fully_rehashed_extra_inittab_helper_and_product_changes_reject(self):
        for kind in ('inittab', 'helper', 'runtime', 'manifest'):
            with self.subTest(kind=kind):
                value, before, after, helper, kwargs = instrumentation()
                if kind in ('inittab', 'helper'):
                    index = 0 if kind == 'inittab' else 3
                    row = value['changes'][index]
                    raw = base64.b64decode(row['after']['data_base64']) + b'::once:/bin/sh\n'
                    row['after'] = snap(row['path'], raw, index + 1, at=2, mode=row['after']['mode'])
                else:
                    current = image.decode(after)
                    if kind == 'runtime':
                        current['runtime_files']['aios_boot/runtime.py'] = 'b' * 64
                    else:
                        current['profile'] = 'basic-cli'
                    after = encoded(current)
                    value['manifest_sha256'] = image.digest(after)
                    value['changes'][2]['after'] = snap(verifier.CHANGED[2], after, 3, at=2)
                with self.assertRaises(ValueError):
                    verifier.validate_instrumentation(value, before, after, helper, **kwargs)

    def test_metadata_owner_boolean_inode_mode_and_preserved_hook_reject(self):
        for kind in ('owner', 'bool', 'inode', 'mode', 'hook', 'same_id', 'extra'):
            with self.subTest(kind=kind):
                value, before, after, helper, kwargs = instrumentation()
                if kind == 'owner': value['changes'][0]['after']['uid'] = 1000
                elif kind == 'bool': value['changes'][0]['after']['uid'] = False
                elif kind == 'inode': value['changes'][0]['after']['inode'] += 1
                elif kind == 'mode': value['changes'][3]['after']['mode'] = 0o644
                elif kind == 'same_id': value['image_id'] = value['base_image_id']
                elif kind == 'extra': value['changes'].append(copy.deepcopy(value['changes'][0]))
                else:
                    name = '/usr/local/sbin/aios-start-system'
                    value['preserved'][name] = snap(name, b'#!/bin/sh\ntrue\n', 100)
                with self.assertRaises(ValueError):
                    verifier.validate_instrumentation(value, before, after, helper, **kwargs)

    def test_base_tree_preservation_checks_actual_bytes_and_full_file_set(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'system.raw').write_bytes(b'normal original disk')
            put(root / 'verdict.json', {'outcome': 'PASS'})
            expected = {p.name: {'size_bytes': p.stat().st_size, 'sha256': image.digest(p.read_bytes()),
                'mtime_ns': p.stat().st_mtime_ns} for p in root.iterdir()}
            verifier.validate_preserved_tree(root, expected)
            (root / 'system.raw').write_bytes(b'changed original disk')
            with self.assertRaisesRegex(ValueError, 'base_files_preserved'):
                verifier.validate_preserved_tree(root, expected)
            (root / 'extra').write_bytes(b'extra')
            expected['system.raw'] = {'size_bytes': (root / 'system.raw').stat().st_size,
                'sha256': image.digest((root / 'system.raw').read_bytes()), 'mtime_ns': (root / 'system.raw').stat().st_mtime_ns}
            with self.assertRaisesRegex(ValueError, 'base_files_preserved'):
                verifier.validate_preserved_tree(root, expected)

    def test_preparation_raw_exports_match_and_trailing_fatal_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            put(root / 'instrumentation.json', {'schema_version': 1})
            put(root / 'guest/instrumentation.json', {'schema_version': 1})
            put(root / 'installed-metadata/image.json', {'schema_version': 2})
            raw = b'AIOS_IMAGE_RECOVERY_PREPARED\n'
            for index, name in enumerate(('guest', 'installed-metadata')):
                token = ('a' if index == 0 else 'b').encode() * 32
                bundle = {p.name: base64.b64encode(p.read_bytes()).decode('ascii') for p in (root / name).iterdir()}
                raw += token + base64.b64encode(encoded(bundle)) + token + b'\n'
            raw += b'[ 12.500000] reboot: Power down\n'
            (root / 'serial.log').write_bytes(raw)
            verifier.validate_preparation_serial(root)
            for suffix in (b'Kernel panic\n', b'Oops: invalid\n', b'Traceback (most recent call last)\n'):
                (root / 'serial.log').write_bytes(raw + suffix)
                with self.assertRaisesRegex(ValueError, 'preparation_fatal_serial'):
                    verifier.validate_preparation_serial(root)
class LaunchContractTests(unittest.TestCase):
    def setUp(self):
        self.disk = {'schema_version': 1, 'path': '/test/system.raw', 'size_bytes': verifier.DISK_BYTES, 'sha256': 'a' * 64}
        self.channel = {'schema_version': 1, 'transport': 'qemu-serial-socket', 'host': '127.0.0.1',
                        'port': 43210, 'chardev_id': 'aiosfault', 'guest_device': '/dev/ttyS1'}
        from qemu_image import base_command
        self.base = base_command('/usr/bin/qemu-system-x86_64', model=True)
        self.launch = {'schema_version': 1, 'network': 'offline', 'firmware': 'bios', 'boot_method': 'disk',
            'stdin_commands': list(verifier.COMMANDS), 'qemu_argv': self.base + ['-boot', 'c', '-nic', 'none',
            '-device', 'qemu-xhci', '-device', 'usb-kbd', '-drive', 'file=/test/system.raw,format=raw,if=virtio',
            '-chardev', 'socket,id=aiosfault,host=127.0.0.1,port=43210', '-serial', 'chardev:aiosfault']}

    def test_exact_fault_launch_passes_and_normal_rejects_it(self):
        verifier.validate_launch(self.launch, self.disk, self.channel, model=True)
        with self.assertRaisesRegex(ValueError, 'qemu_disk_only'):
            image.validate_launch(self.launch, self.disk, model=True, smoke=False)

    def test_extra_device_network_command_and_ambiguous_channel_reject(self):
        for kind in ('drive', 'command', 'network', 'host', 'port', 'tty'):
            with self.subTest(kind=kind):
                value, channel = copy.deepcopy(self.launch), copy.deepcopy(self.channel)
                if kind == 'drive': value['qemu_argv'] += ['-drive', 'file=/tmp/extra.raw']
                elif kind == 'command': value['stdin_commands'].insert(3, 'backend restart')
                elif kind == 'network': value['network'] = 'online'
                elif kind == 'host': channel['host'] = '0.0.0.0'
                elif kind == 'port': channel['port'] = True
                else: channel['guest_device'] = '/dev/ttyS0'
                with self.assertRaises(ValueError):
                    verifier.validate_launch(value, self.disk, channel, model=True)

    def test_preparation_only_owned_clone_and_readonly_input(self):
        value = {'schema_version': 1, 'iso_sha256': image.ISO['sha256'], 'kernel_sha256': verifier.INSTALLER_KERNEL_SHA256,
            'initramfs_sha256': verifier.INSTALLER_INITRAMFS_SHA256, 'qemu_argv': self.base + ['-kernel', '/iso/kernel', '-initrd', '/iso/initrd',
            '-append', 'console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage', '-cdrom', '/iso/alpine.iso',
            '-nic', 'user,model=e1000', '-drive', 'file=/test/system.raw,format=raw,if=none,id=aiosdisk',
            '-device', 'virtio-blk-pci,drive=aiosdisk,serial=AIOS_RECOVERY_CLONE', '-drive',
            'file=fat:ro:/tmp/share,format=raw,if=none,id=testinput,readonly=on', '-device',
            'virtio-blk-pci,drive=testinput,serial=AIOS_RECOVERY_INPUT']}
        verifier.validate_preparation_launch(value, self.disk)
        for key in ('kernel_sha256', 'initramfs_sha256'):
            with self.assertRaisesRegex(ValueError, 'preparation_boot_input_pins'):
                verifier.validate_preparation_launch({**value, key: 'a' * 64}, self.disk)
        for index, bad in ((29, 'file=/normal/system.raw,format=raw,if=none,id=aiosdisk'),
                           (33, 'file=fat:rw:/tmp/share,format=raw,if=none,id=testinput,readonly=off')):
            mutated = copy.deepcopy(value); mutated['qemu_argv'][index] = bad
            with self.assertRaises(ValueError):
                verifier.validate_preparation_launch(mutated, self.disk)


class FaultContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        state, _source, _runs, receipt_path, owner = recovery_fixture(self.path)
        self.receipt = image.record(receipt_path)
        self.receipt['lease']['service_record']['profile'] = 'llamafile-pinned'
        self.receipt['recovery'].update(started_monotonic_ns=1700, completed_monotonic_ns=1800)
        self.owner = sample(owner, 1, 900)
        self.boot_id, self.worker_pid = owner['host_boot_id'], owner['process_id']
        self.helper_hash = 'e' * 64
        running = {**image.record(state / 'latest.json'), 'capture_kind': 'live',
                   'service_record': self.receipt['lease']['service_record'], 'action': 'status'}
        terminal = {**running, 'state': 'RECOVERED', 'descriptor': None,
            'service_record': {**running['service_record'], 'lifecycle_state': 'exited', 'backend_ready': False}}
        self.commands = [{'name': line.split()[0], 'args': line.split()[1:], 'outcome': 'OK', 'result': None}
                         for line in verifier.COMMANDS]
        for index, value in ((1, {**running, 'action': 'start'}), (2, running),
                             (3, {**terminal, 'action': 'recover'}), (4, terminal)):
            self.commands[index]['result'] = value
        child = self.receipt['lease']['service_record']['child_identity']
        supervisor = self.receipt['lease']['service_record']['supervisor_identity']
        fresh = {**running, 'action': 'recover', 'state': 'FAILED', 'outcome': 'ERROR',
            'error': 'recovery-owner-required', 'service_record': None, 'descriptor': None}
        self.proof = {'schema_version': 1, 'scenario': 'running-supervisor-loss', 'capture_kind': 'live',
            'source_only': True, 'injector_source_sha256': self.helper_hash, 'backend_status': running,
            'cli': sample(owner, 1, 1460), 'supervisor_before': sample(supervisor, owner['process_id'], 1470),
            'child_before': sample(child, supervisor['process_id'], 1480), 'child_after': sample(child, 1, 1510),
            'child_after_fresh': sample(child, 1, 1550), 'signal': {'name': 'SIGKILL', 'via': 'authenticated-pidfd',
                'pidfd_acquired_monotonic_ns': 1455, 'sent_monotonic_ns': 1495, 'supervisor_exit_observed': True,
                'supervisor_exit_monotonic_ns': 1500}, 'fresh_recover': {'process_exit_code': 1,
                'stdout': encoded(fresh).decode(), 'stderr': '', 'response': fresh}}
        self.ready = {'schema_version': 1, 'event': 'READY', 'scenario': verifier.SCENARIO, 'test_only': True,
            'source_only': True, 'boot_id': self.boot_id, 'uid': 1000, 'euid': 1000, 'gid': 1000, 'egid': 1000,
            'groups': [], 'injector_source_sha256': self.helper_hash,
            'injector': sample({**owner, 'process_id': 99, 'process_start_ticks': 20}, 1, 800)}
        self.complete = {'schema_version': 1, 'event': 'COMPLETE', 'boot_id': self.boot_id, 'outcome': 'PASS',
                         'completed_monotonic_ns': 1600}
        put(self.path / 'fault-requests.json', {'schema_version': 1, 'requests': [
            {'schema_version': 1, 'action': 'inject'}, {'schema_version': 1, 'action': 'acknowledge'}]})
        self.close = {'schema_version': 1, 'outcome': 'closed', 'transport_close': 'eof', 'socket_error_code': None,
            'pending_bytes': 0, 'completed_frame_count': 3, 'request_count': 2}
        put(self.path / 'fault-channel-close.json', self.close)
        self.vm = {'schema_version': 1, 'process_exit_code': 0, 'host_killed': False,
            'shutdown_observed': True, 'runner_error': None}
        put(self.path / 'vm-result.json', self.vm)
        put(self.path / 'launch.json', {'qemu_argv': ['C:/QEMU/qemu-system-x86_64.exe']})
        self.reseal()

    def reseal(self):
        put(self.path / 'fault-ready.json', self.ready)
        put(self.path / 'fault.json', self.proof)
        put(self.path / 'fault-complete.json', self.complete)
        (self.path / 'fault-channel.log').write_bytes(encoded(self.ready) + encoded({
            'schema_version': 1, 'event': 'FAULT', 'proof': self.proof}) + encoded(self.complete))

    def check(self):
        verifier.validate_fault(self.path, self.helper_hash, self.receipt, self.commands,
                                self.owner, self.boot_id, self.worker_pid)

    def test_authenticated_fault_same_worker_and_distinct_recovery_pass(self):
        self.check()

    def test_completed_windows_reset_passes_only_with_exact_close_record_and_clean_vm(self):
        reset = {**self.close, 'transport_close': 'connection-reset', 'socket_error_code': 10054}
        put(self.path / 'fault-channel-close.json', reset)
        self.check()
        for fields in ({'outcome': 'failed'}, {'pending_bytes': 1}, {'completed_frame_count': 2},
                       {'request_count': 1}, {'socket_error_code': 10053}, {'socket_error_code': True},
                       {'transport_close': 'eof'}, {'transport_close': 'error'}):
            with self.subTest(fields=fields):
                put(self.path / 'fault-channel-close.json', {**reset, **fields})
                with self.assertRaisesRegex(ValueError, 'channel_close_'): self.check()
        put(self.path / 'fault-channel-close.json', reset)
        put(self.path / 'launch.json', {'qemu_argv': ['/usr/bin/qemu-system-x86_64']})
        with self.assertRaisesRegex(ValueError, 'channel_close_windows_launch'): self.check()
        put(self.path / 'launch.json', {'qemu_argv': ['C:/QEMU/qemu-system-x86_64.exe']})
        for fields in ({'process_exit_code': False}, {'process_exit_code': 1}, {'host_killed': True},
                       {'shutdown_observed': False}, {'runner_error': 'original-failure'}):
            with self.subTest(fields=fields):
                put(self.path / 'vm-result.json', {**self.vm, **fields})
                with self.assertRaisesRegex(ValueError, 'channel_close_vm_exit'): self.check()

    def test_missing_close_record_and_reset_with_trailing_data_are_rejected(self):
        (self.path / 'fault-channel-close.json').unlink()
        with self.assertRaisesRegex(ValueError, 'regular_file:fault-channel-close.json'): self.check()
        put(self.path / 'fault-channel-close.json', {**self.close, 'transport_close': 'connection-reset',
                                                  'socket_error_code': 10054})
        with (self.path / 'fault-channel.log').open('ab') as stream:
            stream.write(b'trailing')
        with self.assertRaisesRegex(ValueError, 'auxiliary_frame_count'): self.check()

    def test_resealed_wrong_worker_parent_source_and_orphan_identity_reject(self):
        for kind in ('worker', 'parent', 'source', 'orphan', 'injector'):
            with self.subTest(kind=kind):
                old_proof, old_ready = copy.deepcopy(self.proof), copy.deepcopy(self.ready)
                if kind == 'worker': self.worker_pid += 1
                elif kind == 'parent':
                    raw = self.proof['supervisor_before']['raw_stat']
                    self.proof['supervisor_before']['raw_stat'] = raw.replace(') S 101 ', ') S 102 ')
                elif kind == 'source': self.ready['injector_source_sha256'] = 'f' * 64
                elif kind == 'orphan': self.proof['child_after'] = copy.deepcopy(self.proof['cli'])
                else: self.ready['injector'] = copy.deepcopy(self.owner)
                self.reseal()
                with self.assertRaises(ValueError): self.check()
                self.proof, self.ready = old_proof, old_ready
                if kind == 'worker': self.worker_pid -= 1

    def test_resealed_false_exit_nonowner_success_old_error_and_time_reject(self):
        for kind in ('death', 'fresh_zero', 'fresh_error', 'time', 'lease_time', 'returncode'):
            with self.subTest(kind=kind):
                old_proof, old_complete, old_receipt = copy.deepcopy(self.proof), copy.deepcopy(self.complete), copy.deepcopy(self.receipt)
                if kind == 'death': self.proof['signal']['supervisor_exit_observed'] = False
                elif kind == 'fresh_zero': self.proof['fresh_recover']['process_exit_code'] = 0
                elif kind == 'fresh_error':
                    self.proof['fresh_recover']['response']['error'] = 'process-not-running'
                    self.proof['fresh_recover']['stdout'] = encoded(self.proof['fresh_recover']['response']).decode()
                elif kind == 'time': self.complete['completed_monotonic_ns'] = 1701
                elif kind == 'lease_time': self.receipt['lease']['acquired_monotonic_ns'] = 1456
                else: self.receipt['recovery']['supervisor_returncode'] = 0
                self.reseal()
                with self.assertRaises(ValueError): self.check()
                self.proof, self.complete, self.receipt = old_proof, old_complete, old_receipt

    def test_missing_truncated_duplicate_error_and_wrong_request_reject(self):
        original = (self.path / 'fault-channel.log').read_bytes()
        for raw in (b'', original[:-1], original + encoded(self.complete),
                    original.replace(b'"event":"COMPLETE"', b'"event":"ERROR"')):
            (self.path / 'fault-channel.log').write_bytes(raw)
            with self.assertRaises(ValueError): self.check()
        self.reseal()
        put(self.path / 'fault-requests.json', {'schema_version': 1, 'requests': [{'schema_version': 1, 'action': 'inject'}]})
        with self.assertRaisesRegex(ValueError, 'auxiliary_requests'): self.check()


class TemporaryPathCanonicalizationTests(unittest.TestCase):
    def test_noncanonical_temp_root_keeps_exact_base_replay_path(self):
        from test_hosted_image_verifier import AliasedTemporaryDirectory
        with patch.object(tempfile, 'TemporaryDirectory', AliasedTemporaryDirectory):
            fixture = SidecarJoinTests()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            value, _base = fixture.check()
            self.assertEqual(value, fixture.sidecar)
            self.assertEqual(fixture.source, fixture.source.resolve())
            self.assertEqual(fixture.output, fixture.output.resolve())


class SidecarJoinTests(unittest.TestCase):
    """Exercise the complete sidecar IO path; base model replay has its own suite."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.source, self.output = root / 'normal', root / 'fault'
        self.source.mkdir(); self.output.mkdir()
        self.boot = self.output / 'boots/boot-01'; self.boot.mkdir(parents=True)
        value, before, after, helper, kwargs = instrumentation()
        self.receipt = value
        (self.source / 'system.raw').write_bytes(b'normal disk')
        (self.output / 'system.raw').write_bytes(b'cloned disk')
        put(self.source / 'verdict.json', {'schema_version': 2, 'outcome': 'PASS'})
        self.base_result = {'outcome': 'PASS', 'profile': 'local-model-cli', 'boots': []}
        for index in (1, 2):
            boot_id = str(uuid.uuid4())
            self.base_result['boots'].append({'boot_id': boot_id})
            archive = self.source / 'boots' / ('boot-%02d' % index) / 'archive'
            owner = sample({'host_boot_id': boot_id, 'uid': 1000, 'process_id': 101, 'process_start_ticks': 50}, 1, 900)
            target = archive / 'session/session.events.jsonl'; target.parent.mkdir(parents=True)
            target.write_bytes(encoded({'schema_version': 7, 'event': 'START', 'data': {'source_process': owner}}))
            put(archive / 'boot.json', {'boot_id': boot_id, 'started_monotonic_ns': 100})
            put(archive / 'worker-result.json', {'boot_id': boot_id, 'uid': 1000, 'completed_monotonic_ns': 1000})
            put(archive / 'root-result.json', {'boot_id': boot_id, 'worker_process_id': 101, 'completed_monotonic_ns': 1100})
        (self.source / 'image-manifest.json').write_bytes(before)
        (self.output / 'image-manifest.json').write_bytes(after)
        target = self.source / 'installation-files/boot-hook.sha256'; target.parent.mkdir()
        target.write_bytes((kwargs['boot_hook_hash'] + '\n').encode())
        current_inittab = base64.b64decode(value['changes'][1]['after']['data_base64'])
        target = self.output / 'installation-files/inittab.sha256'; target.parent.mkdir()
        target.write_bytes(current_inittab)
        original = {'schema_version': 1, 'path': str(self.source / 'system.raw'), 'size_bytes': 11,
                    'sha256': image.digest((self.source / 'system.raw').read_bytes())}
        cloned = {**original, 'path': str(self.output / 'system.raw')}
        prepared = {**cloned, 'sha256': image.digest((self.output / 'system.raw').read_bytes())}
        put(self.source / 'boots/boot-02/disk-after.json', original)
        put(self.output / 'image-before.json', cloned)
        put(self.boot / 'disk-before.json', prepared)
        stage = self.output / 'preparation'
        put(stage / 'instrumentation.json', value)
        put(stage / 'guest/instrumentation.json', value)
        target = stage / 'installed-metadata/image.json'; target.parent.mkdir(parents=True)
        target.write_bytes(after)
        target = stage / 'installed-metadata/installation-files/inittab.sha256'; target.parent.mkdir()
        target.write_bytes(current_inittab)
        raw = b'AIOS_IMAGE_RECOVERY_PREPARED\n'
        for index, name in enumerate(('guest', 'installed-metadata')):
            token = ('a' if index == 0 else 'b').encode() * 32
            bundle = {p.relative_to(stage / name).as_posix(): base64.b64encode(p.read_bytes()).decode('ascii')
                      for p in (stage / name).rglob('*') if p.is_file()}
            raw += token + base64.b64encode(encoded(bundle)) + token + b'\n'
        (stage / 'serial.log').write_bytes(raw + b'[ 20.125000] reboot: Power down\n')
        put(stage / 'result.json', {'schema_version': 1, 'scenario': verifier.SCENARIO,
                                   'test_only': True, 'outcome': 'PASS', 'reasons': []})
        put(stage / 'vm-result.json', {'schema_version': 1, 'process_exit_code': 0, 'host_killed': False,
                                      'shutdown_observed': True, 'runner_error': None})
        from qemu_image import base_command
        argv = base_command('/usr/bin/qemu-system-x86_64', model=True) + ['-kernel', '/iso/kernel', '-initrd', '/iso/initrd',
            '-append', 'console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage', '-cdrom', '/iso/alpine.iso',
            '-nic', 'user,model=e1000', '-drive', 'file=' + cloned['path'].replace('\\', '/') + ',format=raw,if=none,id=aiosdisk',
            '-device', 'virtio-blk-pci,drive=aiosdisk,serial=AIOS_RECOVERY_CLONE', '-drive',
            'file=fat:ro:/tmp/share,format=raw,if=none,id=testinput,readonly=on', '-device',
            'virtio-blk-pci,drive=testinput,serial=AIOS_RECOVERY_INPUT']
        put(stage / 'launch.json', {'schema_version': 1, 'qemu_argv': argv, 'iso_sha256': image.ISO['sha256'],
            'kernel_sha256': verifier.INSTALLER_KERNEL_SHA256, 'initramfs_sha256': verifier.INSTALLER_INITRAMFS_SHA256})
        snapshots = self.output / 'verification-source'; snapshots.mkdir()
        for name, data in (('image_recovery_guest.py', helper), ('image_recovery_prepare.py', b'# preparer\n'),
                           ('qemu_image_recovery.py', b'# runner\n')):
            (snapshots / name).write_bytes(data)
        files = {p.relative_to(self.source).as_posix(): {'size_bytes': p.stat().st_size,
            'sha256': image.digest(p.read_bytes()), 'mtime_ns': p.stat().st_mtime_ns}
            for p in self.source.rglob('*') if p.is_file()}
        self.sidecar = {'schema_version': 1, 'scenario': verifier.SCENARIO, 'test_only': True, 'source_only': True,
            'source_directory': str(self.source), 'source_verdict_sha256': files['verdict.json']['sha256'],
            'source_image': original, 'cloned_image': cloned, 'prepared_image': prepared,
            'base_file_records': files, 'source_files_preserved': True,
            'guest_receipt_sha256': image.digest((stage / 'instrumentation.json').read_bytes()),
            'helper_source_sha256': image.digest(helper), 'preparation_source_sha256': image.digest(b'# preparer\n'),
            'runner_source_sha256': image.digest(b'# runner\n')}
        put(self.output / 'fault-instrumentation.json', self.sidecar)
        put(self.output / 'working-copy.json', {'schema_version': 1, 'source_directory': str(self.source),
            'source_image': original, 'initial_image': cloned, 'source_verdict_sha256': files['verdict.json']['sha256']})
        put(stage / 'prepare-input.json', {'schema_version': 1, 'scenario': verifier.SCENARIO,
            'base_manifest_sha256': image.digest(before), 'helper_source_sha256': image.digest(helper), 'image_id': value['image_id']})

    def check(self):
        with patch.object(verifier, 'DISK_BYTES', 11), patch.object(image, 'verify_image', return_value=self.base_result) as replay:
            result = verifier.validate_sidecar(self.output, self.boot, require_live=False)
            replay.assert_called_once_with(self.source, require_live=False, verify_disk=False)
            return result

    def test_complete_sidecar_chain_reads_original_tree_and_preparation_exports(self):
        value, _base = self.check()
        self.assertEqual(value, self.sidecar)

    def test_rehashed_wrong_prepared_disk_and_missing_base_verdict_reject(self):
        self.sidecar['prepared_image']['sha256'] = 'f' * 64
        put(self.output / 'fault-instrumentation.json', self.sidecar)
        with self.assertRaisesRegex(ValueError, 'prepared_boot_disk'):
            self.check()
        (self.source / 'verdict.json').unlink()
        with self.assertRaisesRegex(ValueError, 'base_files_preserved'):
            self.check()

    def test_base_cli6_or_rehashed_foreign_worker_cannot_supply_cli7_lane(self):
        path = self.source / 'boots/boot-01/archive/session/session.events.jsonl'
        original = image.decode(path.read_bytes())
        foreign = sample({**verifier.identity(original['data']['source_process']), 'process_id': 102}, 1, 900)
        for change, reason in (({'schema_version': 6}, 'base_cli7'),
                               ({'data': {'source_process': foreign}}, 'base_worker_owner')):
            with self.subTest(reason=reason):
                path.write_bytes(encoded({**original, **change}))
                relative = path.relative_to(self.source).as_posix()
                self.sidecar['base_file_records'][relative] = {'size_bytes': path.stat().st_size,
                    'sha256': image.digest(path.read_bytes()), 'mtime_ns': path.stat().st_mtime_ns}
                put(self.output / 'fault-instrumentation.json', self.sidecar)
                with self.assertRaisesRegex(ValueError, reason): self.check()


if __name__ == '__main__':
    unittest.main()
