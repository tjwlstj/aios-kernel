#!/usr/bin/env python3
"""Independent acceptance of one instrumented image-worker recovery test.

The ordinary image verifier stays strict. This separate entry accepts exactly
one test-only serial channel and replays the original disk/worker/archive
evidence through the shared verifier, without editing or synthesizing records.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

import verify_image as image
from backend_output_contract import validate_backend_result
from backend_recovery_contract import identity, parent
from resource_output_contract import validate_sample

SCENARIO = 'image-worker-recover-exit'
COMMANDS = ['about', 'backend start', 'backend status', 'backend recover', 'backend status', 'exit']
HELPER = '/usr/local/libexec/aios-image-recovery-test.py'
INITTAB_LINE = ('ttyS1::once:/usr/bin/python3 -B ' + HELPER + '\n').encode('ascii')
CHANGED = ['/etc/inittab', '/usr/share/aios/installation-files/inittab.sha256', '/usr/share/aios/image.json', HELPER]
PRESERVED = {'/usr/local/sbin/aios-start-system', '/etc/aios/boot.json'}
DISK_BYTES = 4 * 1024 ** 3
# Extracted directly from boot/{vmlinuz-virt,initramfs-virt} of image.ISO's
# pinned 3.24.1 bytes; independent replay must not accept arbitrary input hashes.
INSTALLER_KERNEL_SHA256 = '1e6bf9027720c75c3ed0d79171f21b5791ee40ca9795d07c7c6e04dc5ea2ae90'
INSTALLER_INITRAMFS_SHA256 = '4caf09640385525632f0a54dfeabd08b5bf5ceeb2b7d63e888fb858b7b3fa006'
same = image.exact_json_equal


def require(condition, reason):
    if not condition:
        raise ValueError('image_recovery:' + reason)


def keys(value, expected, reason):
    require(type(value) is dict and value.keys() == set(expected), reason)


def boundary(value):
    image.schema(value['schema_version'])
    require(value['scenario'] == SCENARIO and value['test_only'] is True and value['source_only'] is True,
            'test_boundary')


def validate_channel(value):
    keys(value, {'schema_version', 'transport', 'host', 'port', 'chardev_id', 'guest_device'}, 'channel_keys')
    image.schema(value['schema_version'])
    image.integer(value['port'], 1, 65535)
    require(value['transport'] == 'qemu-serial-socket' and value['host'] == '127.0.0.1'
            and value['chardev_id'] == 'aiosfault' and value['guest_device'] == '/dev/ttyS1', 'channel_transport')


def validate_launch(value, disk, channel, *, smoke=False, model=False):
    """Accept the real fault argv itself; do not normalize it into a normal one."""
    validate_channel(channel)
    keys(value, {'schema_version', 'qemu_argv', 'stdin_commands', 'network', 'firmware', 'boot_method'}, 'launch_keys')
    image.schema(value['schema_version'])
    require(model is True and smoke is False and value['firmware'] == 'bios'
            and value['boot_method'] == 'disk' and value['network'] == 'offline', 'disk_boot')
    require(same(value['stdin_commands'], COMMANDS), 'command_plan')
    argv = value['qemu_argv']
    require(type(argv) is list and argv and type(argv[0]) is str
            and (PurePosixPath(argv[0]).is_absolute() or PureWindowsPath(argv[0]).is_absolute())
            and argv[0].replace('\\', '/').split('/')[-1] in ('qemu-system-x86_64', 'qemu-system-x86_64.exe'), 'qemu_binary')
    expected = ['-machine', 'q35', '-accel', 'tcg', '-cpu', 'max', '-m', '3072', '-smp', '2',
        '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-boot', 'c', '-nic', 'none',
        '-device', 'qemu-xhci', '-device', 'usb-kbd', '-drive',
        'file=' + disk['path'].replace('\\', '/') + ',format=raw,if=virtio',
        '-chardev', 'socket,id=aiosfault,host=127.0.0.1,port=' + str(channel['port']), '-serial', 'chardev:aiosfault']
    require(same(argv[1:], expected), 'qemu_fault_disk_only')


def snapshot(value, path):
    keys(value, {'path', 'uid', 'gid', 'mode', 'nlink', 'device', 'inode', 'size_bytes',
                 'mtime_ns', 'ctime_ns', 'sha256', 'data_base64'}, 'snapshot_keys')
    require(value['path'] == path, 'snapshot_path')
    for name in ('uid', 'gid', 'mode', 'nlink', 'device', 'inode', 'size_bytes', 'mtime_ns', 'ctime_ns'):
        image.integer(value[name])
    require(value['uid'] == value['gid'] == 0 and value['nlink'] == 1
            and 0 < value['mode'] <= 0o777 and not value['mode'] & 0o022
            and value['inode'] > 0 and value['size_bytes'] <= 256 * 1024, 'snapshot_metadata')
    image.sha(value['sha256'])
    require(type(value['data_base64']) is str, 'snapshot_base64')
    raw = base64.b64decode(value['data_base64'], validate=True)
    require(len(raw) == value['size_bytes'] and image.digest(raw) == value['sha256'], 'snapshot_bytes')
    return raw


def validate_instrumentation(value, base_raw, current_raw, helper_raw, *, boot_hook_hash, boot_config_hash):
    """Only these four small-file changes may connect the two image identities."""
    keys(value, {'schema_version', 'scenario', 'test_only', 'source_only', 'base_image_id', 'image_id',
        'base_manifest_sha256', 'manifest_sha256', 'runtime_files', 'changes', 'preserved'}, 'instrumentation_keys')
    boundary(value)
    base, current = image.decode(base_raw), image.decode(current_raw)
    image.identifier(value['base_image_id'])
    image.identifier(value['image_id'])
    require(value['base_image_id'] == base['image_id'] and value['image_id'] == current['image_id']
            and value['base_image_id'] != value['image_id'], 'separate_image_identity')
    require(value['base_manifest_sha256'] == image.digest(base_raw)
            and value['manifest_sha256'] == image.digest(current_raw), 'manifest_bytes')
    require(type(value['runtime_files']) is dict and value['runtime_files'].keys() == image.IMAGE_SOURCES
            and len(value['runtime_files']) == 35
            and same(value['runtime_files'], base['runtime_files'])
            and same(value['runtime_files'], current['runtime_files']), 'product_source35_unchanged')
    changes = value['changes']
    require(type(changes) is list and len(changes) == 4, 'changed_file_count')
    pairs = []
    for index, (row, name) in enumerate(zip(changes, CHANGED)):
        keys(row, {'path', 'before', 'after'}, 'change_keys')
        require(row['path'] == name, 'change_path_order')
        after = snapshot(row['after'], name)
        if index < 3:
            before = snapshot(row['before'], name)
            require(all(same(row['before'][key], row['after'][key]) for key in
                        ('uid', 'gid', 'mode', 'nlink', 'device', 'inode')), 'changed_file_identity')
            require(row['before']['mtime_ns'] <= row['after']['mtime_ns']
                    and row['before']['ctime_ns'] <= row['after']['ctime_ns'], 'changed_file_time')
        else:
            require(row['before'] is None and row['after']['mode'] == 0o444, 'new_helper_metadata')
            before = None
        pairs.append((before, after))
    old_init, new_init = pairs[0]
    require(old_init.endswith(b'\n') and b'ttyS1' not in old_init and b'aios-image-recovery' not in old_init
            and new_init == old_init + INITTAB_LINE, 'inittab_exact_addition')
    for index, manifest in enumerate((base, current)):
        require(pairs[1][index] == (image.digest(pairs[0][index]) + '\n').encode('ascii')
                and image.digest(pairs[1][index]) == manifest['installation_files']['inittab.sha256'], 'inittab_hash_join')
    require(pairs[2] == (base_raw, current_raw) and pairs[3][1] == helper_raw, 'installed_test_source')
    expected = copy.deepcopy(base)
    expected['image_id'] = current['image_id']
    expected['installation_files']['inittab.sha256'] = image.digest(pairs[1][1])
    require(same(current, expected), 'manifest_only_test_changes')
    keys(value['preserved'], PRESERVED, 'preserved_file_set')
    hook = snapshot(value['preserved']['/usr/local/sbin/aios-start-system'], '/usr/local/sbin/aios-start-system')
    config = snapshot(value['preserved']['/etc/aios/boot.json'], '/etc/aios/boot.json')
    require(image.digest(hook) == boot_hook_hash and image.digest(config) == boot_config_hash
            == base['boot_config_sha256'] == current['boot_config_sha256'], 'preserved_boot_entry')


def validate_preserved_tree(source, expected):
    """Read the normal source artifact, including its disk, without changing it."""
    require(type(expected) is dict and 2 <= len(expected) <= 8192, 'base_file_count')
    actual = {}
    for path in sorted(source.rglob('*')):
        before = path.lstat()
        require(not stat.S_ISLNK(before.st_mode), 'base_symlink')
        if stat.S_ISDIR(before.st_mode):
            continue
        require(stat.S_ISREG(before.st_mode), 'base_special_file')
        size, digest = image.file_hash(path)
        after = path.stat()
        require((before.st_size, before.st_mtime_ns, before.st_ino)
                == (after.st_size, after.st_mtime_ns, after.st_ino), 'base_read_race')
        actual[path.relative_to(source).as_posix()] = {'size_bytes': size, 'sha256': digest, 'mtime_ns': before.st_mtime_ns}
    for name, row in expected.items():
        image.relative(name)
        keys(row, {'size_bytes', 'sha256', 'mtime_ns'}, 'base_file_keys')
        image.integer(row['size_bytes'])
        image.integer(row['mtime_ns'])
        image.sha(row['sha256'])
    require(same(actual, expected) and 'system.raw' in actual and 'verdict.json' in actual, 'base_files_preserved')


def validate_preparation_serial(stage):
    """Join both copied preparation exports to their original serial frames."""
    raw = image.read(stage / 'serial.log', image.SERIAL_LIMIT).replace(b'\r\n', b'\n')
    require(re.search(rb'(?im)(?:kernel panic|Oops:|BUG:|Traceback \(most recent call last\)|AIOS_IMAGE_(?:FAILURE|FATAL)=)', raw)
            is None, 'preparation_fatal_serial')
    require(len(re.findall(rb'^AIOS_IMAGE_RECOVERY_PREPARED\n', raw, re.M)) == 1
            and raw.count(b'reboot: Power down') == 1
            and re.search(rb'(?m)^\[\s*[0-9]+\.[0-9]+\] reboot: Power down\n?\Z', raw) is not None,
            'preparation_serial_terminal')
    frames = re.findall(rb'^([0-9a-f]{32})([A-Za-z0-9+/=]+)\1\n', raw, re.M)
    require(len(frames) == 2 and frames[0][0] != frames[1][0], 'preparation_export_frames')
    for (_, payload), name in zip(frames, ('guest', 'installed-metadata')):
        decoded = image.decode(base64.b64decode(payload, validate=True), limit=image.EXPORT_LIMIT)
        require(len(decoded) <= 256, 'preparation_export_count')
        files = {}
        for relative, encoded in decoded.items():
            image.relative(relative)
            require(type(encoded) is str, 'preparation_export_base64')
            data = base64.b64decode(encoded, validate=True)
            require(len(data) <= image.FILE_LIMIT, 'preparation_export_file_size')
            files[relative] = data
        require(files == image.archive_files(stage / name), 'preparation_export_copy')
    require(image.read(stage / 'guest/instrumentation.json') == image.read(stage / 'instrumentation.json'),
            'preparation_receipt_copy')


def validate_preparation_launch(value, disk):
    keys(value, {'schema_version', 'qemu_argv', 'iso_sha256', 'kernel_sha256', 'initramfs_sha256'}, 'preparation_launch_keys')
    image.schema(value['schema_version'])
    require(value['iso_sha256'] == image.ISO['sha256'], 'preparation_iso_pin')
    image.sha(value['kernel_sha256'])
    image.sha(value['initramfs_sha256'])
    require(value['kernel_sha256'] == INSTALLER_KERNEL_SHA256
            and value['initramfs_sha256'] == INSTALLER_INITRAMFS_SHA256, 'preparation_boot_input_pins')
    argv = value['qemu_argv']
    require(type(argv) is list and all(type(item) is str for item in argv) and len(argv) == 36, 'preparation_argv_size')
    for index in (0, 19, 21, 25):
        require(PurePosixPath(argv[index]).is_absolute() or PureWindowsPath(argv[index]).is_absolute(), 'preparation_input_path')
    require(argv[0].replace('\\', '/').split('/')[-1] in ('qemu-system-x86_64', 'qemu-system-x86_64.exe'), 'preparation_qemu')
    share = argv[33]
    match = re.fullmatch(r'file=fat:ro:(.+),format=raw,if=none,id=testinput,readonly=on', share)
    require(match is not None and ',' not in match[1]
            and (PurePosixPath(match[1]).is_absolute() or PureWindowsPath(match[1]).is_absolute()), 'preparation_readonly_input')
    expected = ['-machine', 'q35', '-accel', 'tcg', '-cpu', 'max', '-m', '3072', '-smp', '2',
        '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot',
        '-kernel', argv[19], '-initrd', argv[21], '-append', 'console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage',
        '-cdrom', argv[25], '-nic', 'user,model=e1000', '-drive',
        'file=' + disk['path'].replace('\\', '/') + ',format=raw,if=none,id=aiosdisk',
        '-device', 'virtio-blk-pci,drive=aiosdisk,serial=AIOS_RECOVERY_CLONE', '-drive', share,
        '-device', 'virtio-blk-pci,drive=testinput,serial=AIOS_RECOVERY_INPUT']
    require(same(argv[1:], expected), 'preparation_owned_clone_only')


def validate_base_workers(source, base):
    """This new lane requires a CLI7 normal base, while normal replay stays historical."""
    require(type(base['boots']) is list and len(base['boots']) == 2, 'base_two_cli7_boots')
    for index, row in enumerate(base['boots']):
        archive = source / 'boots' / ('boot-%02d' % (index + 1)) / 'archive'
        events = [image.decode(line) for line in image.read(archive / 'session/session.events.jsonl').splitlines()]
        require(events and events[0]['event'] == 'START' and same(events[0]['schema_version'], 7), 'base_cli7')
        owner = events[0]['data']['source_process']
        validate_sample(owner)
        boot, root, worker = [image.record(archive / name) for name in ('boot.json', 'root-result.json', 'worker-result.json')]
        require(owner['host_boot_id'] == row['boot_id'] == boot['boot_id'] == root['boot_id'] == worker['boot_id']
                and same(owner['process_id'], root['worker_process_id'])
                and same(owner['uid'], 1000) and same(owner['uid'], worker['uid']), 'base_worker_owner')
        require(boot['started_monotonic_ns'] <= owner['read_start_ns'] <= owner['read_end_ns']
                <= worker['completed_monotonic_ns'] <= root['completed_monotonic_ns'], 'base_worker_time')


def validate_sidecar(directory, path, *, require_live):
    value = image.record(directory / 'fault-instrumentation.json')
    keys(value, {'schema_version', 'scenario', 'test_only', 'source_only', 'source_directory', 'source_verdict_sha256',
        'source_image', 'cloned_image', 'prepared_image', 'base_file_records', 'source_files_preserved',
        'guest_receipt_sha256', 'helper_source_sha256', 'preparation_source_sha256', 'runner_source_sha256'}, 'sidecar_keys')
    boundary(value)
    require(type(value['source_directory']) is str and Path(value['source_directory']).is_absolute(), 'base_directory')
    source = Path(value['source_directory']).resolve()
    require(source != directory.resolve() and source not in directory.resolve().parents
            and directory.resolve() not in source.parents and not source.is_symlink(), 'separate_clone')
    require(value['source_files_preserved'] is True, 'base_preservation_claim')
    validate_preserved_tree(source, value['base_file_records'])
    require(image.digest(image.read(source / 'verdict.json')) == value['source_verdict_sha256']
            == value['base_file_records']['verdict.json']['sha256'], 'base_verdict_hash')
    require(image.record(source / 'verdict.json')['outcome'] == 'PASS', 'base_stored_verdict')
    # The tree check already hashed the terminal base disk. Replay its normal
    # two-boot acceptance against its own retained sources without rereading 4GB.
    base = image.verify_image(source, require_live=require_live, verify_disk=False)
    require(base['outcome'] == 'PASS' and base['profile'] == 'local-model-cli', 'normal_base:' + str(base.get('reasons')))
    validate_base_workers(source, base)
    for name in ('source_image', 'cloned_image', 'prepared_image'):
        image.disk_record(value[name])
        require(value[name]['size_bytes'] == DISK_BYTES, 'disk_size')
    original, cloned, prepared = [value[name] for name in ('source_image', 'cloned_image', 'prepared_image')]
    base_terminal = image.record(source / 'boots/boot-02/disk-after.json')
    require(same(original, base_terminal) and Path(original['path']).resolve() == source / 'system.raw'
            and original['sha256'] == value['base_file_records']['system.raw']['sha256']
            and original['size_bytes'] == value['base_file_records']['system.raw']['size_bytes'], 'base_disk_join')
    require(cloned['sha256'] == original['sha256'] and cloned['path'] == prepared['path']
            and Path(cloned['path']).resolve() == directory.resolve() / 'system.raw'
            and cloned['sha256'] != prepared['sha256'], 'clone_preparation_chain')
    require(same(image.record(directory / 'image-before.json'), cloned)
            and same(image.record(path / 'disk-before.json'), prepared), 'prepared_boot_disk')
    copy_record = image.record(directory / 'working-copy.json')
    require(same(copy_record, {'schema_version': 1, 'source_directory': value['source_directory'],
        'source_image': original, 'initial_image': cloned, 'source_verdict_sha256': value['source_verdict_sha256']}), 'clone_receipt')
    stage = directory / 'preparation'
    require(same(image.record(stage / 'result.json'), {'schema_version': 1, 'scenario': SCENARIO,
        'test_only': True, 'outcome': 'PASS', 'reasons': []}), 'preparation_result')
    require(same(image.record(stage / 'vm-result.json'), {'schema_version': 1, 'process_exit_code': 0,
        'host_killed': False, 'shutdown_observed': True, 'runner_error': None}), 'preparation_vm_exit')
    validate_preparation_launch(image.record(stage / 'launch.json'), cloned)
    validate_preparation_serial(stage)
    for key, file in (('helper_source_sha256', 'image_recovery_guest.py'),
                      ('preparation_source_sha256', 'image_recovery_prepare.py'),
                      ('runner_source_sha256', 'qemu_image_recovery.py')):
        image.sha(value[key])
        require(image.digest(image.read(directory / 'verification-source' / file)) == value[key], 'test_source_hash')
    receipt_raw = image.read(stage / 'instrumentation.json')
    require(image.digest(receipt_raw) == value['guest_receipt_sha256'], 'instrumentation_hash')
    current_raw, base_raw = image.read(directory / 'image-manifest.json'), image.read(source / 'image-manifest.json')
    require(image.read(stage / 'installed-metadata/image.json') == current_raw
            and image.read(stage / 'installed-metadata/installation-files/inittab.sha256')
            == image.read(directory / 'installation-files/inittab.sha256'), 'prepared_metadata_copy')
    receipt = image.decode(receipt_raw)
    validate_instrumentation(receipt, base_raw, current_raw,
        image.read(directory / 'verification-source/image_recovery_guest.py'),
        boot_hook_hash=image.read(source / 'installation-files/boot-hook.sha256').decode('ascii').strip(),
        boot_config_hash=image.decode(base_raw)['boot_config_sha256'])
    require(same(image.record(stage / 'prepare-input.json'), {'schema_version': 1, 'scenario': SCENARIO,
        'base_manifest_sha256': image.digest(base_raw), 'helper_source_sha256': value['helper_source_sha256'],
        'image_id': receipt['image_id']}), 'prepare_input_join')
    return value, base


def validate_channel_close(path):
    """A Windows close reset is distinct from incomplete evidence or a failed VM."""
    value = image.record(path / 'fault-channel-close.json')
    keys(value, {'schema_version', 'outcome', 'transport_close', 'socket_error_code', 'pending_bytes',
                 'completed_frame_count', 'request_count'}, 'channel_close_keys')
    image.schema(value['schema_version'])
    require(value['outcome'] == 'closed' and same(value['pending_bytes'], 0)
            and same(value['completed_frame_count'], 3) and same(value['request_count'], 2), 'channel_close_incomplete')
    require((value['transport_close'] == 'eof' and value['socket_error_code'] is None)
            or (value['transport_close'] == 'connection-reset' and same(value['socket_error_code'], 10054)),
            'channel_close_transport')
    if value['transport_close'] == 'connection-reset':
        argv = image.record(path / 'launch.json')['qemu_argv']
        require(type(argv) is list and argv and type(argv[0]) is str
                and PureWindowsPath(argv[0]).is_absolute()
                and PureWindowsPath(argv[0]).name == 'qemu-system-x86_64.exe', 'channel_close_windows_launch')
    require(same(image.record(path / 'vm-result.json'), {'schema_version': 1, 'process_exit_code': 0,
        'host_killed': False, 'shutdown_observed': True, 'runner_error': None}), 'channel_close_vm_exit')


def validate_fault(path, helper_hash, receipt, commands, owner, boot_id, worker_pid):
    """Join the separate injector transcript to the unchanged image worker."""
    raw = image.read(path / 'fault-channel.log')
    require(raw.endswith(b'\n') and b'\r' not in raw and len(raw.splitlines()) == 3, 'auxiliary_frame_count')
    frames = [image.decode(line) for line in raw.splitlines()]
    ready, fault, complete = frames
    keys(ready, {'schema_version', 'event', 'scenario', 'test_only', 'source_only', 'boot_id',
        'uid', 'euid', 'gid', 'egid', 'groups', 'injector_source_sha256', 'injector'}, 'ready_keys')
    boundary(ready)
    require(ready['event'] == 'READY' and ready['boot_id'] == boot_id and ready['injector_source_sha256'] == helper_hash,
            'ready_identity')
    require(all(same(ready[name], 1000) for name in ('uid', 'euid', 'gid', 'egid'))
            and same(ready['groups'], []), 'injector_privilege')
    validate_sample(ready['injector'])
    injector = ready['injector']
    require(injector['host_boot_id'] == boot_id and injector['uid'] == 1000, 'injector_raw_identity')
    keys(fault, {'schema_version', 'event', 'proof'}, 'fault_frame_keys')
    image.schema(fault['schema_version'])
    require(fault['event'] == 'FAULT', 'fault_frame_event')
    proof = fault['proof']
    keys(complete, {'schema_version', 'event', 'boot_id', 'outcome', 'completed_monotonic_ns'}, 'complete_keys')
    image.schema(complete['schema_version'])
    image.integer(complete['completed_monotonic_ns'], 1)
    require(complete['event'] == 'COMPLETE' and complete['outcome'] == 'PASS' and complete['boot_id'] == boot_id,
            'fault_complete')
    require(same(ready, image.record(path / 'fault-ready.json')) and same(proof, image.record(path / 'fault.json'))
            and same(complete, image.record(path / 'fault-complete.json')), 'auxiliary_copy')
    require(same(image.record(path / 'fault-requests.json'), {'schema_version': 1, 'requests': [
        {'schema_version': 1, 'action': 'inject'}, {'schema_version': 1, 'action': 'acknowledge'}]}), 'auxiliary_requests')
    validate_channel_close(path)
    require([' '.join([row['name'], *row['args']]) for row in commands] == COMMANDS
            and all(row['outcome'] == 'OK' for row in commands), 'command_outcomes')
    keys(proof, {'schema_version', 'scenario', 'capture_kind', 'source_only', 'injector_source_sha256',
        'backend_status', 'cli', 'supervisor_before', 'child_before', 'child_after', 'child_after_fresh',
        'signal', 'fresh_recover'}, 'fault_keys')
    image.schema(proof['schema_version'])
    require(proof['scenario'] == 'running-supervisor-loss' and proof['capture_kind'] == 'live'
            and proof['source_only'] is True and proof['injector_source_sha256'] == helper_hash, 'fault_boundary')
    validate_backend_result(proof['backend_status'], require_live=True)
    running = proof['backend_status']
    require(running['state'] == 'RUNNING' and running['action'] == 'status'
            and same(running, commands[2]['result']) and same(running, {**commands[1]['result'], 'action': 'status'})
            and same(running['service_record'], receipt['lease']['service_record'])
            and same(running['descriptor'], receipt['lease']['descriptor']), 'authenticated_running_join')
    for role in ('cli', 'supervisor_before', 'child_before', 'child_after', 'child_after_fresh'):
        validate_sample(proof[role])
    validate_sample(owner)
    cli, supervisor, child = [proof[role] for role in ('cli', 'supervisor_before', 'child_before')]
    require(same(identity(cli), identity(owner)) and same(identity(cli), identity(receipt['lease']['owner']))
            and cli['process_id'] == worker_pid and cli['host_boot_id'] == boot_id and cli['uid'] == 1000
            and owner['read_end_ns'] <= cli['read_start_ns'], 'fault_worker_owner')
    require(same(identity(supervisor), running['service_record']['supervisor_identity'])
            and same(identity(child), running['service_record']['child_identity'])
            and parent(supervisor) == cli['process_id'] and parent(child) == supervisor['process_id'], 'fault_parent_chain')
    require(len({injector['process_id'], cli['process_id'], supervisor['process_id'], child['process_id']}) == 4
            and all(row['host_boot_id'] == boot_id and row['uid'] == 1000 for row in (supervisor, child)), 'separate_injector')
    require(all(same(identity(proof[role]), identity(child)) for role in ('child_after', 'child_after_fresh')), 'orphan_identity')
    signal = proof['signal']
    keys(signal, {'name', 'via', 'pidfd_acquired_monotonic_ns', 'sent_monotonic_ns',
        'supervisor_exit_observed', 'supervisor_exit_monotonic_ns'}, 'fault_signal_keys')
    require(signal['name'] == 'SIGKILL' and signal['via'] == 'authenticated-pidfd'
            and signal['supervisor_exit_observed'] is True, 'fault_signal')
    for field in ('pidfd_acquired_monotonic_ns', 'sent_monotonic_ns', 'supervisor_exit_monotonic_ns'):
        image.integer(signal[field], 1)
    require(injector['read_end_ns'] <= signal['pidfd_acquired_monotonic_ns']
        and receipt['lease']['acquired_monotonic_ns'] <= signal['pidfd_acquired_monotonic_ns']
        <= min(proof[role]['read_start_ns'] for role in ('cli', 'supervisor_before', 'child_before'))
        <= max(proof[role]['read_end_ns'] for role in ('cli', 'supervisor_before', 'child_before'))
        <= signal['sent_monotonic_ns'] <= signal['supervisor_exit_monotonic_ns']
        <= proof['child_after']['read_start_ns'] <= proof['child_after']['read_end_ns']
        <= proof['child_after_fresh']['read_start_ns'] <= proof['child_after_fresh']['read_end_ns']
        <= complete['completed_monotonic_ns'] <= receipt['recovery']['started_monotonic_ns'], 'fault_time_order')
    fresh = proof['fresh_recover']
    keys(fresh, {'process_exit_code', 'stdout', 'stderr', 'response'}, 'fresh_keys')
    require(same(fresh['process_exit_code'], 1) and fresh['stderr'] == '' and type(fresh['stdout']) is str
            and same(image.decode(fresh['stdout'].encode('utf-8')), fresh['response']), 'fresh_execution')
    validate_backend_result(fresh['response'], require_live=True)
    require(fresh['response']['action'] == 'recover' and fresh['response']['state'] == 'FAILED'
            and fresh['response']['outcome'] == 'ERROR' and fresh['response']['error'] == 'recovery-owner-required'
            and fresh['response']['service_record'] is None and fresh['response']['descriptor'] is None, 'fresh_owner_rejected')
    require(same(receipt['recovery']['supervisor_returncode'], -9)
            and receipt['recovery']['signal'] == 'SIGTERM'
            and receipt['recovery']['signal_via'] == 'retained-pidfd', 'recovery_signal_join')
    terminal = {**running, 'state': 'RECOVERED', 'descriptor': None,
        'service_record': {**running['service_record'], 'lifecycle_state': 'exited', 'backend_ready': False}}
    require(same(commands[3]['result'], {**terminal, 'action': 'recover'})
            and same(commands[4]['result'], terminal), 'explicit_recovered_delivery')


def verify_image_recovery(image_root, boot_dir, source_root=None, *, require_live=True):
    try:
        directory, path = Path(image_root), Path(boot_dir)
        require(path.resolve() == directory.resolve() / 'boots/boot-01'
                and sorted(p.name for p in (directory / 'boots').iterdir()) == ['boot-01'], 'one_test_boot')
        sidecar, base = validate_sidecar(directory, path, require_live=require_live)
        channel = image.record(path / 'fault-channel.json')
        def launch_check(value, disk, **kwargs):
            validate_launch(value, disk, channel, **kwargs)
        result = image._verify_operating_boot(directory, path, source_root,
            require_live=require_live, launch_validator=launch_check)
        require(result['outcome'] == 'PASS', 'operating_boot:' + str(result.get('reasons')))
        require(result['backend_recovered_runs'] == result['backend_runs'] == 1
                and result['backend_normal_runs'] == result['main_runs'] == result['warmup_requests']
                == result['user_requests'] == result['service_runs'] == 0
                and result['main_started'] is False and result['dns_observed'] is False
                and result['https_observed'] is False, 'recovered_only_cleanup')
        archive = path / 'archive'
        events = [image.decode(line) for line in image.read(archive / 'session/session.events.jsonl').splitlines()]
        commands = [row['data'] for row in events if row['event'] == 'COMMAND']
        require(events[0]['event'] == 'START' and same(events[0]['schema_version'], 7), 'console_schema7')
        receipts = list((archive / 'backend/recoveries').iterdir())
        require(len(receipts) == 1, 'one_recovery_receipt')
        validate_fault(path, sidecar['helper_source_sha256'], image.record(receipts[0]), commands,
            events[0]['data']['source_process'], result['boot_id'], image.record(archive / 'root-result.json')['worker_process_id'])
        expected_prior = {row['boot_id']: {
            'archive_manifest_sha256': image.digest(image.read(Path(sidecar['source_directory']) / 'boots'
                / ('boot-%02d' % (index + 1)) / 'archive/archive-manifest.json')),
            'root_result_sha256': image.digest(image.read(Path(sidecar['source_directory']) / 'boots'
                / ('boot-%02d' % (index + 1)) / 'archive/root-result.json'))}
            for index, row in enumerate(base['boots'])}
        require(same(image.record(archive / 'boot.json')['previous_boots'], expected_prior), 'normal_base_history')
        return {**result, 'scenario': SCENARIO, 'test_only': True, 'source_only': True,
            'normal_base_verified': True, 'base_image_id': base['image_id'],
            'image_id': image.record(directory / 'image-manifest.json')['image_id'],
            'product_runtime_unchanged': True, 'source_files_preserved': True,
            'expected_fault_verified': True, 'recovered_only_cleanup_verified': True,
            'prior_history_replayed': True}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        return {'schema_version': 1, 'outcome': 'FAIL', 'scenario': SCENARIO,
            'test_only': True, 'source_only': True, 'reasons': [str(exc) or type(exc).__name__]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image_root', type=Path)
    parser.add_argument('boot_dir', type=Path)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    result = verify_image_recovery(args.image_root, args.boot_dir, args.source_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['outcome'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
