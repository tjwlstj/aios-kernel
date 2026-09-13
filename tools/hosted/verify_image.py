#!/usr/bin/env python3
"""Independent replay of installed, disk-booted AIOS operating images."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from verify_console import MANAGED_SOURCES, SPACE_SOURCES, source_version, verify_session
from verify_service import verify_service_runs
from newagent_output_contract import agent_result
from backend_output_contract import validate_backend_result

FILE_LIMIT = 2 * 1024 * 1024
EXPORT_LIMIT = 16 * 1024 * 1024
SERIAL_LIMIT = 48 * 1024 * 1024
ONLINE_COMMANDS = ['about', 'status', 'hardware', 'net status', 'service status', 'service start',
    'service status', 'room status', 'ask Say hello.', 'resolve example.com', 'fetch https://example.com/', 'exit']
OFFLINE_COMMANDS = [value for value in ONLINE_COMMANDS if not value.startswith(('resolve ', 'fetch '))]
MODEL_PREFIX = ['about', 'status', 'hardware', 'net status', 'service status', 'service start',
    'service status', 'backend status', 'agent status', 'backend start', 'agent start', 'room status',
    'ask Say hello.', 'room discover', 'room bind', 'resources link']
MODEL_ONLINE_COMMANDS = MODEL_PREFIX + ['ask What is the capital of France? Answer in one short sentence.',
    'resolve example.com', 'fetch https://example.com/', 'exit']
MODEL_OFFLINE_COMMANDS = MODEL_PREFIX + ['cell deactivate', 'cell activate', 'ask Say hello.',
    'resources status', 'room discover', 'room reconcile', 'resources link',
    'ask What is the Moon? Answer in one short sentence.', 'exit']
MODEL_CONFIG_PATH = '/etc/aios/model.json'
MODEL_DIRECTORY = '/opt/aios/inference/'
MODEL_FILES = {'Qwen3-0.6B-Q8_0.gguf': {'size_bytes': 639446688,
    'sha256': '9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031'},
    'llamafile-0.10.5-thin.exe': {'size_bytes': 42328074,
    'sha256': '55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611'}}
MODEL_ID = 'aios-qwen3-0.6b-q8_0'
MANIFEST_KEYS = {'schema_version', 'image_id', 'profile', 'substrate', 'iso', 'runtime_files',
    'boot_config_sha256', 'installation_files', 'source_only', 'repository_import', 'redistribution_approved'}
IMAGE_SOURCES = set(MANAGED_SOURCES) | {'aios-image-boot.py', 'aios_boot/__init__.py', 'aios_boot/archive.py', 'aios_boot/runtime.py'}
SPACE_IMAGE_SOURCES = IMAGE_SOURCES | (set(SPACE_SOURCES) - set(MANAGED_SOURCES))
SPACE_SESSION_VERSIONS = {'0.8.0': 8, '0.9.0': 9}
ISO = {'url': 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-virt-3.24.1-x86_64.iso',
       'version': '3.24.1', 'sha256': 'e73a6241bd5f3c5c2d4d38c02cc52c378c0415a7c888bd292066bf36e0f41a39'}
INSTALLATION_FILES = {'packages.txt', 'kernel.txt', 'installed-runtime.json', 'kernel-image.sha256',
    'initramfs.sha256', 'bootloader-config.sha256', 'inittab.sha256', 'boot-hook.sha256'}


def require(condition, reason):
    if not condition:
        raise ValueError('image_contract:' + reason)


def keys(value, expected, reason):
    require(type(value) is dict and value.keys() == expected, reason)


def exact_json_equal(value, expected):
    """Compare decoded evidence without Python's bool/int/float coercion."""
    if type(value) is not type(expected):
        return False
    if type(value) is dict:
        return value.keys() == expected.keys() and all(exact_json_equal(value[key], expected[key]) for key in value)
    if type(value) is list:
        return len(value) == len(expected) and all(exact_json_equal(left, right) for left, right in zip(value, expected))
    return value == expected


def integer(value, minimum=0, maximum=(1 << 64) - 1):
    require(type(value) is int and minimum <= value <= maximum, 'integer')


def schema(value):
    require(type(value) is int and value == 1, 'schema')


def identifier(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0, 'uuid')


def sha(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'sha256')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def relative(value):
    require(type(value) is str and 0 < len(value) <= 240 and '\\' not in value and ':' not in value
            and all(c.isprintable() for c in value), 'relative_path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and '.' not in path.parts
            and str(path) == value, 'relative_path')
    return path


def read(path, limit=FILE_LIMIT):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular_file:' + path.name)
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, 'file_size:' + path.name)
    return value


def decode(raw, limit=FILE_LIMIT):
    require(type(raw) is bytes and len(raw) <= limit, 'json_size')
    def pairs(items):
        value = {}
        for name, item in items:
            require(name not in value, 'duplicate_key')
            value[name] = item
        return value
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('image_contract:nonfinite')))
    require(type(value) is dict, 'object')
    pending, count = [(value, 1)], 0
    while pending:
        current, depth = pending.pop()
        count += 1
        require(count <= 16384 and depth <= 20, 'json_complexity')
        children = current.values() if type(current) is dict else current if type(current) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value


def record(path):
    return decode(read(path))


def file_hash(path):
    require(path.is_file() and not path.is_symlink(), 'disk_file')
    size, hashed = 0, hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            size += len(chunk)
            hashed.update(chunk)
    return size, hashed.hexdigest()


def disk_record(value):
    keys(value, {'schema_version', 'sha256', 'size_bytes', 'path'}, 'disk_keys')
    schema(value['schema_version'])
    sha(value['sha256'])
    integer(value['size_bytes'], 1)
    require(type(value['path']) is str and (PurePosixPath(value['path']).is_absolute() or PureWindowsPath(value['path']).is_absolute())
            and value['path'].replace('\\', '/').split('/')[-1] == 'system.raw', 'disk_path')


def validate_launch(value, disk, *, smoke=True, model=False):
    keys(value, {'schema_version', 'qemu_argv', 'stdin_commands', 'network', 'firmware', 'boot_method'}, 'launch_keys')
    schema(value['schema_version'])
    require(value['firmware'] == 'bios' and value['boot_method'] == 'disk'
            and value['network'] in ('online', 'offline'), 'disk_boot')
    commands = (MODEL_ONLINE_COMMANDS if value['network'] == 'online' else MODEL_OFFLINE_COMMANDS) if model else (
        ONLINE_COMMANDS if value['network'] == 'online' else OFFLINE_COMMANDS)
    if smoke:
        require(value['stdin_commands'] == commands, 'host_setup_or_commands')
    else:
        from verify_console import parsed_command
        require(type(value['stdin_commands']) is list and 0 < len(value['stdin_commands']) <= 254, 'interactive_commands')
        allowed = {'help', 'about', 'status', 'hardware', 'net', 'service', 'agent', 'backend', 'room', 'cell',
                   'resources', 'ask', 'resolve', 'fetch', 'clear', 'exit'}
        require(all(parsed_command(line)[0] in allowed for line in value['stdin_commands']), 'host_setup_or_commands')
    argv = value['qemu_argv']
    require(type(argv) is list and argv and type(argv[0]) is str
            and (PurePosixPath(argv[0]).is_absolute() or PureWindowsPath(argv[0]).is_absolute())
            and argv[0].replace('\\', '/').split('/')[-1] in ('qemu-system-x86_64', 'qemu-system-x86_64.exe'), 'qemu_binary')
    expected = ['-machine', 'q35', '-accel', 'tcg', '-cpu', 'max', '-m', '3072' if model else '768', '-smp', '2',
        '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-boot', 'c', '-nic',
        'user,model=e1000' if value['network'] == 'online' else 'none', '-device', 'qemu-xhci', '-device', 'usb-kbd',
        '-drive', 'file=' + disk['path'].replace('\\', '/') + ',format=raw,if=virtio']
    require(argv[1:] == expected, 'qemu_disk_only')


def export_from_serial(raw):
    """Decode one bounded export without executing or writing archive paths."""
    require(len(raw) <= SERIAL_LIMIT, 'serial_size')
    lines = raw.replace(b'\r\n', b'\n').splitlines()
    markers = [(i, line) for i, line in enumerate(lines) if line.startswith(b'AIOS_IMAGE_')]
    begin = [(i, line) for i, line in markers if line.startswith(b'AIOS_IMAGE_EXPORT_BEGIN=')]
    end = [(i, line) for i, line in markers if line.startswith(b'AIOS_IMAGE_EXPORT_END=')]
    require(len(begin) == len(end) == 1 and begin[0][0] < end[0][0], 'export_frame')
    header = decode(begin[0][1].split(b'=', 1)[1])
    keys(header, {'schema_version', 'boot_id', 'encoding', 'file_count', 'total_bytes', 'manifest_sha256'}, 'export_header')
    schema(header['schema_version'])
    identifier(header['boot_id'])
    require(header['encoding'] == 'json-files-base64', 'export_encoding')
    integer(header['file_count'], 1, 512)
    integer(header['total_bytes'], 1, EXPORT_LIMIT)
    sha(header['manifest_sha256'])
    require(decode(end[0][1].split(b'=', 1)[1]) == header, 'export_terminal')
    files, total = {}, 0
    for line in lines[begin[0][0] + 1:end[0][0]]:
        require(line.startswith(b'AIOS_IMAGE_EXPORT_FILE='), 'export_payload')
        item = decode(line.split(b'=', 1)[1], limit=FILE_LIMIT * 2)
        keys(item, {'path', 'bytes', 'sha256', 'data_base64'}, 'export_file')
        relative(item['path'])
        require(item['path'] not in files, 'export_duplicate_file')
        integer(item['bytes'], 0, FILE_LIMIT)
        sha(item['sha256'])
        require(type(item['data_base64']) is str, 'export_base64')
        value = base64.b64decode(item['data_base64'], validate=True)
        require(len(value) == item['bytes'] and digest(value) == item['sha256'], 'export_file_hash')
        files[item['path']] = value
        total += len(value)
        require(total <= EXPORT_LIMIT and len(files) <= 512, 'export_limit')
    require(len(files) == header['file_count'] and total == header['total_bytes'], 'export_accounting')
    require('archive-manifest.json' in files and digest(files['archive-manifest.json']) == header['manifest_sha256'],
            'export_manifest_hash')
    return header, files


def is_model(manifest):
    return manifest['profile'] == 'local-model-cli'


def validate_model_integrity(value, *, boot_id=None):
    expected = {name: {'path': MODEL_DIRECTORY + name, **row, 'uid': 0, 'gid': 0, 'mode': 0o444}
                for name, row in MODEL_FILES.items()}
    keys(value, {'schema_version', 'verification', 'files'} | ({'boot_id'} if boot_id else set()),
         'model_integrity_keys')
    schema(value['schema_version'])
    require(value['verification'] == ('boot-read-complete' if boot_id else 'installed-read-complete')
            and value['files'] == expected, 'model_integrity')
    if boot_id:
        require(value['boot_id'] == boot_id, 'model_integrity_boot')
    for row in value['files'].values():
        for name in ('size_bytes', 'uid', 'gid', 'mode'):
            integer(row[name])


def validate_model_installation(directory, manifest):
    """Connect fixed installed assets/config to retained upstream evidence."""
    from resource_output_contract import config_hash
    from verify_agent import verify_model_provenance
    bundle = manifest['model_bundle']
    keys(bundle, {'schema_version', 'config_sha256', 'provenance_sha256', 'files'}, 'model_bundle_keys')
    schema(bundle['schema_version'])
    keys(bundle['files'], set(MODEL_FILES), 'model_bundle_files')
    for name, expected in MODEL_FILES.items():
        keys(bundle['files'][name], {'size_bytes', 'sha256'}, 'model_file_keys')
        integer(bundle['files'][name]['size_bytes'], 1)
        require(bundle['files'][name] == expected, 'model_file_pin')
    installation = directory / 'installation-files'
    config_raw = read(installation / 'model-config.json')
    config = decode(config_raw)
    config_hash(config)
    require(config['model_id'] == MODEL_ID and config['endpoint'] == 'http://127.0.0.1:18081'
            and config['model_path'] == MODEL_DIRECTORY + 'Qwen3-0.6B-Q8_0.gguf'
            and config['backend_path'] == MODEL_DIRECTORY + 'llamafile-0.10.5-thin.exe', 'installed_model_config')
    require(digest(config_raw) == bundle['config_sha256']
            and config['provenance_sha256'] == bundle['provenance_sha256'], 'installed_model_hash')
    provenance = verify_model_provenance(installation, config)
    integrity = record(installation / 'model-integrity.json')
    validate_model_integrity(integrity)
    expected_files = INSTALLATION_FILES | {'model-config.json', 'inference-provenance.json', 'model-integrity.json'}
    expected_files |= {row['path'] for row in provenance['source_receipts']}
    require(manifest['installation_files'].keys() == expected_files, 'model_installation_set')
    return config


def image_session_schema(directory, manifest):
    """Bind the space-family session to its hashed literal source version."""
    if set(manifest['runtime_files']) != SPACE_IMAGE_SOURCES:
        return None
    raw = read(directory / 'runtime-source/aios_console/__init__.py')
    require(digest(raw) == manifest['runtime_files']['aios_console/__init__.py'],
            'runtime_source_hash:aios_console/__init__.py')
    try:
        version = source_version(raw)
    except ValueError as exc:
        raise ValueError('image_contract:space_runtime_version') from exc
    require(version in SPACE_SESSION_VERSIONS, 'space_runtime_version')
    return SPACE_SESSION_VERSIONS[version]


def validate_manifest(directory):
    value = record(directory / 'image-manifest.json')
    model = value.get('profile') == 'local-model-cli'
    keys(value, MANIFEST_KEYS | ({'model_bundle'} if model else set()), 'manifest_keys')
    require(type(value['schema_version']) is int and value['schema_version'] == (2 if model else 1), 'manifest_schema')
    identifier(value['image_id'])
    require(value['profile'] in ('basic-cli', 'local-model-cli') and value['substrate'] == 'linux-hosted'
            and value['source_only'] is True and value['repository_import'] is False
            and value['redistribution_approved'] is False, 'manifest_boundary')
    keys(value['iso'], {'url', 'sha256', 'version'}, 'iso_keys')
    require(value['iso'] == ISO, 'iso_pin')
    sha(value['iso']['sha256'])
    require(type(value['iso']['version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value['iso']['version']), 'iso_version')
    url = urlsplit(value['iso']['url'])
    require(url.scheme == 'https' and url.hostname == 'dl-cdn.alpinelinux.org'
            and url.username is None and url.password is None and not url.query and not url.fragment
            and url.path.endswith('/alpine-virt-' + value['iso']['version'] + '-x86_64.iso'), 'iso_source')
    sha(value['boot_config_sha256'])
    hashes = value['runtime_files']
    require(type(hashes) is dict and set(hashes) in (IMAGE_SOURCES, SPACE_IMAGE_SOURCES), 'runtime_source_set')
    for name, expected in hashes.items():
        relative(name)
        require(name.endswith('.py'), 'runtime_source_type')
        sha(expected)
        require(digest(read(directory / 'runtime-source' / name)) == expected, 'runtime_source_hash:' + name)
    image_session_schema(directory, value)
    installed = value['installation_files']
    require(type(installed) is dict and (model or installed.keys() == INSTALLATION_FILES), 'installation_set')
    for name, expected in installed.items():
        relative(name)
        sha(expected)
        require(digest(read(directory / 'installation-files' / name)) == expected, 'installation_hash:' + name)
        if name.endswith('.sha256'):
            raw = read(directory / 'installation-files' / name)
            require(re.fullmatch(rb'[0-9a-f]{64}\n', raw) is not None, 'installed_boot_hash:' + name)
    require(record(directory / 'installation-files/installed-runtime.json') == hashes, 'installed_runtime')
    packages = read(directory / 'installation-files/packages.txt').decode('utf-8').splitlines()
    require(all(any(re.fullmatch(re.escape(name) + r'-[0-9][^\s]*(?: - [^\r\n]+)?', line) for line in packages)
                for name in ('python3', 'ca-certificates', 'linux-virt', 'openrc', 'syslinux')), 'installed_packages')
    require(read(directory / 'installation-files/kernel.txt').strip(), 'installation_empty')
    if model:
        validate_model_installation(directory, value)
    return value


def validate_archive(path, manifest, previous, *, require_live=True):
    """Check copied product records and replay the existing console/service contracts."""
    boot, result = record(path / 'boot.json'), record(path / 'root-result.json')
    model = is_model(manifest)
    model_keys = {'model_bundle', 'model_integrity'} if model else set()
    keys(boot, {'schema_version', 'boot_id', 'image_id', 'profile', 'substrate', 'source_only', 'canonical_continuity',
        'config_sha256', 'image_manifest_sha256', 'runtime_files', 'installation_files', 'previous_boots', 'live_root',
        'history_root', 'worker_uid', 'worker_gid', 'root_uid', 'root_euid', 'started_monotonic_ns'} | model_keys, 'boot_keys')
    version, profile = (2, 'local-model') if model else (1, 'basic')
    require(type(boot['schema_version']) is int and boot['schema_version'] == version, 'boot_schema')
    identifier(boot['boot_id'])
    require(boot['image_id'] == manifest['image_id'] and boot['profile'] == profile
            and boot['substrate'] == 'linux-hosted' and boot['source_only'] is True
            and boot['canonical_continuity'] is False, 'boot_boundary')
    for name, expected in (('worker_uid', 1000), ('worker_gid', 1000), ('root_uid', 0), ('root_euid', 0)):
        require(type(boot[name]) is int and boot[name] == expected, 'boot_privilege')
    integer(boot['started_monotonic_ns'], 1)
    require(boot['live_root'] == '/run/aios/boots/' + boot['boot_id']
            and boot['history_root'] == '/var/lib/aios/history/' + boot['boot_id'], 'boot_scoped_paths')
    prior = boot['previous_boots']
    require(type(prior) is dict and len(prior) <= 512 and boot['boot_id'] not in prior, 'prior_boot_list')
    for name, hashes in prior.items():
        identifier(name)
        keys(hashes, {'archive_manifest_sha256', 'root_result_sha256'}, 'prior_boot_hashes')
        sha(hashes['archive_manifest_sha256'])
        sha(hashes['root_result_sha256'])
    require(previous is None or prior == previous, 'prior_boot_evidence')
    require(boot['runtime_files'] == manifest['runtime_files']
            and boot['installation_files'] == manifest['installation_files'], 'embedded_installation')
    image_raw = read(path / 'image.json')
    require(decode(image_raw) == manifest and digest(image_raw) == boot['image_manifest_sha256'], 'embedded_manifest')
    config_raw = read(path / 'boot-config.json')
    config = decode(config_raw)
    keys(config, {'schema_version', 'profile', 'model_config'}, 'boot_config_keys')
    require(type(config['schema_version']) is int and config['schema_version'] == version, 'boot_config_schema')
    require(config['profile'] == profile and config['model_config'] == (MODEL_CONFIG_PATH if model else None),
            'model_boot_config' if model else 'basic_no_model')
    require(digest(config_raw) == boot['config_sha256'] == manifest['boot_config_sha256'], 'boot_config_drift')
    worker_raw = read(path / 'worker-result.json')
    worker = decode(worker_raw)
    keys(worker, {'schema_version', 'boot_id', 'uid', 'euid', 'gid', 'egid', 'session_id', 'session_exit_code',
        'error', 'cleanup', 'cleanup_ok', 'completed_monotonic_ns'}, 'worker_keys')
    schema(worker['schema_version'])
    require(worker['boot_id'] == boot['boot_id'] and worker['error'] is None and worker['cleanup_ok'] is True, 'worker_failed')
    for name in ('uid', 'euid', 'gid', 'egid'):
        require(type(worker[name]) is int and worker[name] == 1000, 'worker_privilege')
    require(type(worker['session_exit_code']) is int and worker['session_exit_code'] == 0, 'session_exit')
    identifier(worker['session_id'])
    integer(worker['completed_monotonic_ns'], boot['started_monotonic_ns'])
    keys(result, {'schema_version', 'boot_id', 'image_id', 'profile', 'source_only', 'config_sha256', 'worker_process_id',
        'worker_exit_code', 'worker_exit_verified', 'worker_result_sha256', 'session_id', 'session_exit_code', 'cleanup_ok',
        'outcome', 'error', 'archive_complete', 'poweroff_intent', 'completed_monotonic_ns'} | model_keys, 'root_result_keys')
    require(type(result['schema_version']) is int and result['schema_version'] == version, 'root_result_schema')
    require(result['boot_id'] == boot['boot_id'] and result['image_id'] == boot['image_id']
            and result['profile'] == profile and result['source_only'] is True
            and result['config_sha256'] == boot['config_sha256'] and result['worker_result_sha256'] == digest(worker_raw),
            'root_result_identity')
    integer(result['worker_process_id'], 1)
    require(type(result['worker_exit_code']) is int and result['worker_exit_code'] == 0
            and result['worker_exit_verified'] is True and result['session_id'] == worker['session_id']
            and type(result['session_exit_code']) is int and result['session_exit_code'] == 0 and result['cleanup_ok'] is True
            and result['outcome'] == 'PASS' and result['error'] is None and result['archive_complete'] is True
            and result['poweroff_intent'] == 'fixed-root-poweroff', 'root_result_failed')
    integer(result['completed_monotonic_ns'], worker['completed_monotonic_ns'])
    if model:
        require(all(exact_json_equal(value['model_bundle'], manifest['model_bundle']) for value in (boot, result)),
                'boot_model_bundle')
        validate_model_integrity(boot['model_integrity'], boot_id=boot['boot_id'])
        validate_model_integrity(result['model_integrity'], boot_id=boot['boot_id'])
        require(result['model_integrity'] == boot['model_integrity'], 'root_model_integrity')
        for name in manifest['installation_files'].keys() - INSTALLATION_FILES:
            require(digest(read(path / name)) == manifest['installation_files'][name], 'archived_model_metadata:' + name)
    poweroff = record(path / 'poweroff.json')
    require(poweroff == {'schema_version': 1, 'boot_id': boot['boot_id'], 'command': ['/sbin/poweroff'],
        'root_uid': 0, 'root_euid': 0, 'source_only': True}
        and all(type(poweroff[key]) is int for key in ('schema_version', 'root_uid', 'root_euid')), 'poweroff_command')
    cleanup = worker['cleanup']
    require(type(cleanup) is list and len(cleanup) == 3
            and [row['service'] for row in cleanup] == ['MAIN', 'MODEL_BACKEND', 'CONSOLE_RUNTIME'], 'cleanup_order')
    for row in cleanup:
        keys(row, {'service', 'action', 'outcome', 'error', 'response'}, 'cleanup_keys')
        require(row['action'] == 'stop' and row['outcome'] == 'OK' and row['error'] is None, 'cleanup_failed')
    main, backend, service = [row['response'] for row in cleanup]
    # CLI8 and CLI9 share MAIN5; cleanup has no console-rendering differences.
    agent_result({'name': 'agent', 'args': ['stop'], 'outcome': 'OK', 'result': main},
                 8 if set(manifest['runtime_files']) == SPACE_IMAGE_SOURCES else 6, require_live=require_live)
    validate_backend_result(backend, action='stop', require_live=require_live)
    if not model:
        require(main['state'] == backend['state'] == 'ABSENT' and main['source_record'] is None
                and main['inference_receipt'] is None, 'basic_main_absent')
        require(not any(name.startswith(('main/', 'backend/')) for name in archive_files(path)), 'basic_live_store_not_empty')
    return boot, worker, result, service


def archive_files(directory):
    require(directory.is_dir() and not directory.is_symlink(), 'archive_directory')
    files = {}
    for path in directory.rglob('*'):
        require(not path.is_symlink(), 'archive_symlink')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            relative(name)
            files[name] = read(path)
            require(len(files) <= 512 and sum(map(len, files.values())) <= EXPORT_LIMIT, 'archive_size')
        else:
            require(path.is_dir(), 'archive_special_file')
    return files


def validate_archive_manifest(files, boot_id):
    value = decode(files['archive-manifest.json'])
    keys(value, {'schema_version', 'boot_id', 'file_count', 'total_bytes', 'files'}, 'archive_manifest_keys')
    schema(value['schema_version'])
    require(value['boot_id'] == boot_id, 'archive_boot')
    expected = {name: {'bytes': len(raw), 'sha256': digest(raw)} for name, raw in files.items() if name != 'archive-manifest.json'}
    require(value['files'] == expected, 'archive_manifest_files')
    require(type(value['file_count']) is int and value['file_count'] == len(expected)
            and type(value['total_bytes']) is int and value['total_bytes'] == sum(row['bytes'] for row in expected.values()),
            'archive_manifest_accounting')
    for row in value['files'].values():
        require(type(row['bytes']) is int, 'archive_manifest_size_type')


def validate_serial(raw, boot, result, poweroff, console_bytes, worker_exit):
    raw = raw.replace(b'\r\n', b'\n')
    lines = raw.splitlines(keepends=True)
    names = [b'BOOT', b'SESSION_BEGIN', b'SESSION_END', b'RESULT', b'EXPORT_BEGIN', b'EXPORT_END', b'POWEROFF']
    positions = {}
    payloads = {}
    for name in names:
        prefix = b'AIOS_IMAGE_' + name + b'='
        found = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        require(len(found) == 1, 'marker_count:' + name.decode())
        positions[name] = found[0]
        payloads[name] = decode(lines[found[0]][len(prefix):].rstrip(b'\n'))
    require(list(positions.values()) == sorted(positions.values()), 'marker_order')
    require(payloads[b'BOOT'] == boot and payloads[b'RESULT'] == result and payloads[b'POWEROFF'] == poweroff, 'marker_record')
    require(payloads[b'SESSION_BEGIN'] == {'schema_version': 1, 'boot_id': boot['boot_id']}
            and type(payloads[b'SESSION_BEGIN']['schema_version']) is int, 'session_begin')
    require(payloads[b'SESSION_END'] == {'schema_version': 1, 'boot_id': boot['boot_id'], 'worker_exit_code': worker_exit}
            and all(type(payloads[b'SESSION_END'][key]) is int for key in ('schema_version', 'worker_exit_code')), 'session_end')
    captured = b''.join(lines[positions[b'SESSION_BEGIN'] + 1:positions[b'SESSION_END']])
    require(captured == console_bytes.replace(b'\r\n', b'\n'), 'serial_console_output')
    # Outside the exact console and export frames, fatal records cannot be
    # hidden by a later successful marker. Model/user text never enters here.
    outside = b''.join(lines[:positions[b'SESSION_BEGIN']] + lines[positions[b'SESSION_END']:positions[b'EXPORT_BEGIN']]
                       + lines[positions[b'EXPORT_END'] + 1:])
    require(re.search(rb'(?im)(?:kernel panic|Oops:|BUG:|Traceback \(most recent call last\)|AIOS_IMAGE_(?:FAILURE|FATAL)=)', outside) is None,
            'fatal_serial')
    shutdown = re.search(rb'(?m)^\[\s*[0-9]+\.[0-9]+\] reboot: Power down\n?\Z', raw)
    require(shutdown is not None and raw.count(b'reboot: Power down') == 1
            and raw.find(b'AIOS_IMAGE_POWEROFF=') < shutdown.start(), 'shutdown_record')


def validate_running_kernel(directory, files):
    kernels = read(directory / 'installation-files/kernel.txt').decode('utf-8').splitlines()
    require(0 < len(kernels) <= 8 and len(kernels) == len(set(kernels))
            and all(re.fullmatch(r'[A-Za-z0-9_.+-]+', name) for name in kernels), 'installed_kernel_list')
    events = [decode(line) for line in files['session/boot/events.jsonl'].splitlines()]
    observed = [event['data']['kernel_release'] for event in events if event['event'] == 'SUBSTRATE']
    require(len(observed) == 1 and observed[0] in kernels, 'running_kernel_version')


def validate_model_actors(path, directory, manifest, boot, worker, commands, source_root, *, require_live=True, allow_recovered=False):
    """Join displayed responses to independently verified boot-local actors."""
    from verify_agent import verify_agent_runs, verify_execution_backends, verify_backend_delivery, verify_cell_delivery, verify_space_delivery
    from verify_backend import verify_backend_runs
    from resource_output_contract import validate_resource_result
    config = record(directory / 'installation-files/model-config.json')
    main_stop, backend_stop = [row['response'] for row in worker['cleanup'][:2]]
    main_commands = [row for row in commands if row['name'] in ('agent', 'room', 'cell', 'resources', 'ask', 'space')]
    runs, backend_runs = [], []
    if backend_stop['state'] == 'ABSENT':
        require(not any(name.startswith('backend/') for name in archive_files(path)), 'absent_backend_store')
        require(all(row['result']['state'] == 'ABSENT' for row in commands if row['name'] == 'backend'), 'absent_backend_delivery')
    else:
        recoveries = [row for row in commands if row['name'] == 'backend' and row['args'] == ['recover'] and row['outcome'] == 'OK']
        owner = None
        if recoveries:
            require(allow_recovered, 'model_recovery_not_allowed')
            from resource_output_contract import validate_sample
            session_events = [decode(line) for line in read(path / 'session/session.events.jsonl').splitlines()]
            first = session_events[0]
            require(first['event'] == 'START' and type(first['schema_version']) is int and first['schema_version'] in (7, 8, 9),
                    'model_recovery_session_schema')
            sample = first['data']['source_process']
            validate_sample(sample)
            owner = {key: sample[key] for key in ('host_boot_id', 'process_id', 'process_start_ticks', 'uid')}
            root_result = record(path / 'root-result.json')
            require(owner['host_boot_id'] == boot['boot_id'] and owner['uid'] == worker['uid']
                    and type(root_result['worker_process_id']) is int
                    and owner['process_id'] == root_result['worker_process_id'], 'model_recovery_owner')
            require(boot['started_monotonic_ns'] <= sample['read_start_ns'], 'model_recovery_owner_time')
        backend = verify_backend_runs(path / 'backend', source_root=source_root, require_live=require_live,
                                      allow_recovered=allow_recovered and bool(recoveries), recovery_owner=owner)
        require(backend['outcome'] == 'PASS', 'model_backend:' + json.dumps(backend.get('reasons', [])))
        backend_runs = backend['runs']
        require(backend_stop['state'] == backend_runs[-1]['state']
                and backend_stop['state'] in ('STOPPED', 'RECOVERED')
                and backend_stop['service_record'] == backend_runs[-1]['service_record'],
                'model_backend_cleanup')
        for row in backend_runs:
            require(row['config'] == config, 'model_backend_config')
            if row['state'] == 'RECOVERED':
                require(sample['read_end_ns'] <= row['recovery']['lease']['owner']['read_start_ns']
                        and row['recovery']['recovery']['completed_monotonic_ns'] <= worker['completed_monotonic_ns'],
                        'model_recovery_owner_time')
            for identity in ('supervisor_identity', 'child_identity'):
                value = row['service_record'][identity]
                require(value['host_boot_id'] == boot['boot_id'] and type(value['uid']) is int and value['uid'] == 1000,
                        'model_backend_boot_or_uid')
        verify_backend_delivery(commands, {'managed_runs': backend_runs})
    if main_stop['state'] == 'ABSENT':
        require(not any(name.startswith('main/') for name in archive_files(path)), 'absent_main_store')
        require(all(row['result']['source_record'] is None and row['result']['inference_receipt'] is None
                    for row in main_commands), 'absent_main_delivery')
    else:
        store = verify_agent_runs(path / 'main', source_root=source_root, require_live=require_live)
        require(store['outcome'] == 'PASS', 'model_main:' + json.dumps(store.get('reasons', [])))
        runs = store['runs']
        require(main_stop['state'] == 'STOPPED' and main_stop['source_record'] == runs[-1]['source_record']
                and main_stop['management_snapshot'] == runs[-1]['management_snapshot'], 'model_main_cleanup')
        for row in runs:
            require(row['config'] == config and row['source_record']['host_boot_id'] == boot['boot_id'], 'model_main_config_or_boot')
            require(row['execution_binding'] is not None and row['execution_binding']['descriptor'] is not None,
                    'model_execution_binding')
        verify_execution_backends(runs, {'managed_runs': backend_runs})
        sources = [event['source_record'] for run in runs for event in run['events'] if event['source_record'] is not None]
        receipts = {item['request_id']: item for run in runs for item in run['requests']}
        shown_receipts = []
        for command in main_commands:
            value = command['result']
            if value['source_record'] is not None:
                require(value['source_record'] in sources, 'model_source_delivery')
            item = value['inference_receipt']
            if item is not None:
                require(receipts.get(item['request_id']) == item, 'model_receipt_delivery')
                shown_receipts.append(item['request_id'])
        require(len(shown_receipts) == len(set(shown_receipts)) and set(shown_receipts) == set(receipts),
                'model_request_delivery_count')
        recorded = [event for run in runs for event in run['events'] if event['event'] == 'COMMAND']
        shown = [row['result'] for row in main_commands
                 if row['result']['action'] not in ('status', 'start', 'restart', 'stop', 'room-status', 'cell-status')
                 and row['result']['source_record'] is not None]
        require(len(recorded) == len(shown), 'model_command_delivery_count')
        for event, value in zip(recorded, shown):
            require(all(event[key] == value[key] for key in ('action', 'outcome', 'error', 'source_record', 'management_snapshot')),
                    'model_command_delivery')
        verify_cell_delivery(main_commands, runs)
        verify_space_delivery(main_commands, runs)
        resources = [value for run in runs for value in run['resource_results']]
        require(resources == [row['result']['resource_result'] for row in main_commands
                              if row['result'].get('resource_result') is not None], 'model_resource_delivery')
        proofs = [proof for run in backend_runs for proof in run['proofs']]
        for value in resources:
            validate_resource_result(value, config=config, require_live=require_live)
            relation, observation = value['relation'], value['observation']
            if relation is not None:
                require(relation['backend_proof'] in proofs, 'model_relation_attestation')
            if observation is not None:
                require(all(observation[phase]['backend_proof'] in proofs for phase in ('before', 'after')),
                        'model_observation_attestation')
    return {'runs': runs, 'backend_runs': backend_runs}


def validate_model_workflow(commands, actors, *, offline):
    """One warmup/user request per boot; the offline boot proves explicit rebind."""
    runs, backend = actors['runs'], actors['backend_runs']
    require(len(runs) == len(backend) == 1, 'model_boot_run_count')
    run = runs[0]
    require(len(run['requests']) == 1 and run['warmup']['outcome'] == run['requests'][0]['outcome'] == 'OK',
            'model_actual_inference_count')
    require(run['running_source']['service_start_generation'] == backend[0]['start']['start_generation'] == 1,
            'model_boot_generation')
    initial = run['events'][0]['management_snapshot']
    require(initial['binding'] is None and initial['current_source'] is None and not initial['retired_instances'],
            'model_boot_fresh_authority')
    rejected = {11: 'not-discovered', 12: 'not-discovered', **({18: 'stale', 19: 'resource-relation-stale'} if offline else {})}
    for index, command in enumerate(commands):
        require(command['outcome'] == ('ERROR' if index in rejected else 'OK'), 'model_command_outcome')
        if index in rejected:
            require(command['result']['error'] == rejected[index]
                    and command['result']['inference_receipt'] is None, 'model_rejection_receipt')
    require(commands[7]['result']['state'] == commands[8]['result']['state'] == 'ABSENT', 'model_initial_absent')
    require(commands[10]['result']['source_record'] == run['running_source'], 'model_start_delivery')
    require(commands[11]['result']['management_snapshot'] == commands[12]['result']['management_snapshot'], 'model_initial_unbound')
    resources = run['resource_results']
    require([v['action'] for v in resources] == (['link', 'status', 'link', 'request'] if offline else ['link', 'request']),
            'model_resource_sequence')
    require(resources[0]['relation']['relation_generation'] == 1, 'model_initial_relation')
    observation = resources[-1]['observation']
    require(resources[-1]['outcome'] == 'OK' and observation is not None
            and observation['cpu']['backend']['cpu_time_ns'] > 0, 'model_workload_observation')
    if offline:
        before, inactive, active = [commands[index]['result'] for index in (15, 16, 17)]
        require(before['source_record'] == inactive['source_record'] == active['source_record']
                == commands[18]['result']['source_record'], 'model_cell_source_continuity')
        require([row['management_snapshot']['parent']['generation'] for row in (before, inactive, active)] == [1, 2, 3]
                and [row['management_snapshot']['parent']['active'] for row in (before, inactive, active)] == [True, False, True],
                'model_cell_transition')
        old, new = resources[0]['relation'], resources[2]['relation']
        require(resources[1]['outcome'] == 'ERROR' and resources[1]['error'] == 'resource-relation-stale'
                and old['relation_id'] == new['relation_id'] and new['relation_generation'] == new['binding_generation'] == 2
                and old['main_identity'] == new['main_identity']
                and old['backend_proof']['descriptor'] == new['backend_proof']['descriptor'], 'model_explicit_relink')
    return {'main_source_id': run['source_record']['source_id'], 'main_instance': run['source_record']['source_instance'],
        'authority_instance': run['management_snapshot']['authority_instance'], 'backend_service_id': backend[0]['start']['service_id'],
        'backend_instance': backend[0]['start']['instance_id'], 'backend_source_instance': backend[0]['descriptor']['source_instance'],
        'relation_id': resources[0]['relation']['relation_id'], 'request_ids': [run['warmup']['request_id'], run['requests'][0]['request_id']]}


def _verify_image(directory, source_root, require_live, verify_disk):
    require(not (directory / 'fault-instrumentation.json').exists(), 'expected_fault_image_not_normal')
    manifest = validate_manifest(directory)
    expected_session_schema = image_session_schema(directory, manifest)
    model = is_model(manifest)
    source_root = source_root or directory / 'runtime-source'
    for name, expected in manifest['runtime_files'].items():
        require(digest(read(source_root / name)) == expected, 'current_runtime_hash:' + name)
    initial = record(directory / 'image-before.json')
    disk_record(initial)
    paths = sorted((directory / 'boots').iterdir())
    require(len(paths) in ((2,) if model else (2, 3))
            and [p.name for p in paths] == ['boot-%02d' % i for i in range(1, len(paths) + 1)], 'boot_sequence')
    previous, prior_disk, sessions, services, boots = {}, initial, set(), set(), []
    prior_model = None
    for index, path in enumerate(paths):
        require(path.is_dir() and not path.is_symlink(), 'boot_directory')
        before, after, launch = record(path / 'disk-before.json'), record(path / 'disk-after.json'), record(path / 'launch.json')
        disk_record(before)
        disk_record(after)
        require(before == prior_disk and before['size_bytes'] == after['size_bytes'] and before['path'] == after['path'], 'disk_continuity')
        validate_launch(launch, before, model=model)
        require(launch['network'] == ('online' if index < (1 if model else 2) else 'offline'), 'network_sequence')
        vm = record(path / 'vm-result.json')
        keys(vm, {'schema_version', 'process_exit_code', 'host_killed', 'shutdown_observed', 'runner_error'}, 'vm_keys')
        schema(vm['schema_version'])
        require(type(vm['process_exit_code']) is int and vm['process_exit_code'] == 0 and vm['host_killed'] is False
                and vm['shutdown_observed'] is True and vm['runner_error'] is None, 'vm_exit')
        raw = read(path / 'serial.log', SERIAL_LIMIT)
        header, exported = export_from_serial(raw)
        files = archive_files(path / 'archive')
        require(files == exported, 'export_copy_mismatch')
        require(files.get('image.json') == read(directory / 'image-manifest.json'), 'installed_manifest_bytes')
        boot, worker, result, stop = validate_archive(path / 'archive', manifest, previous, require_live=require_live)
        require(header['boot_id'] == boot['boot_id'] and boot['boot_id'] not in previous, 'reused_boot')
        require(result['session_id'] not in sessions, 'reused_session')
        validate_archive_manifest(files, boot['boot_id'])
        validate_serial(raw, boot, result, record(path / 'archive/poweroff.json'), files['session/console.log'], result['worker_exit_code'])
        session_schema = decode(files['session/session.events.jsonl'].splitlines()[0])['schema_version']
        require(session_schema == expected_session_schema if expected_session_schema is not None
                else session_schema not in (8, 9), 'image_session_source_version')
        console = verify_session(path / 'archive/session', source_root=source_root, require_live=require_live,
            require_internet=launch['network'] == 'online', process_exit=0, expected_commands=launch['stdin_commands'],
            expected_session_id=result['session_id'])
        require(console['outcome'] == 'PASS', 'console:' + json.dumps(console.get('reasons', [])))
        validate_running_kernel(directory, files)
        events = [decode(line) for line in files['session/session.events.jsonl'].splitlines()]
        commands = [row['data'] for row in events if row['event'] == 'COMMAND']
        model_summary = None
        if model:
            actors = validate_model_actors(path / 'archive', directory, manifest, boot, worker, commands, source_root,
                                           require_live=require_live)
            model_summary = validate_model_workflow(commands, actors, offline=launch['network'] == 'offline')
            if prior_model is not None:
                require(all(model_summary[key] != prior_model[key] for key in model_summary if key != 'request_ids'),
                        'model_cross_boot_identity')
                require(not set(model_summary['request_ids']) & set(prior_model['request_ids']), 'model_cross_boot_receipt')
            prior_model = model_summary
        else:
            for row in commands:
                if row['name'] in ('room', 'ask'):
                    require(row['outcome'] == 'ERROR' and row['result']['error'] == 'process-not-running'
                            and row['result']['source_record'] is None and row['result']['inference_receipt'] is None,
                            'unbound_request')
                else:
                    require(row['outcome'] == 'OK', 'command_failed')
        svc = verify_service_runs(path / 'archive/service', source_root=source_root, require_live=require_live)
        require(svc['outcome'] == 'PASS' and len(svc['runs']) == 1 and svc['runs'][0] == stop, 'service_cleanup')
        terminal = svc['runs'][0]
        require(terminal['boot_id'] == boot['boot_id'] and terminal['generation'] == 1
                and terminal['service_id'] not in services, 'service_boot_scope')
        shown = [row['result'] for row in commands if row['name'] == 'service']
        require(len(shown) == 3 and shown[0]['state'] == 'ABSENT', 'initial_live_store')
        for running in shown[1:]:
            require(running['state'] == 'RUNNING' and all(running[key] == terminal[key] for key in
                    ('service_id', 'instance_id', 'generation', 'boot_id', 'pid'))
                    and running['observation_sequence'] <= terminal['observation_sequence'], 'service_delivery')
        if launch['network'] == 'offline':
            require(not console['dns_observed'] and not console['https_observed'], 'offline_network_claim')
        sessions.add(result['session_id'])
        services.add(terminal['service_id'])
        previous[boot['boot_id']] = {'archive_manifest_sha256': digest(files['archive-manifest.json']),
                                    'root_result_sha256': digest(files['root-result.json'])}
        prior_disk = after
        boots.append({'boot_id': boot['boot_id'], 'session_id': result['session_id'], 'network': launch['network'],
            'commands': len(commands), 'worker_uid': worker['uid'], 'service_id': terminal['service_id'], 'state': 'STOPPED'})
        if model_summary is not None:
            boots[-1]['model'] = model_summary
    if verify_disk:
        require(file_hash(directory / 'system.raw') == (prior_disk['size_bytes'], prior_disk['sha256']), 'terminal_disk_hash')
    result = {'schema_version': 2 if model else 1, 'outcome': 'PASS', 'reasons': [], 'profile': manifest['profile'],
        'substrate': 'linux-hosted', 'image_id': manifest['image_id'], 'boots': boots, 'cold_boots': len(boots),
        'internet_boots': 1 if model else 2, 'offline_boot_verified': model or len(boots) == 3,
        'disk_bytes_verified': verify_disk, 'main_started': model, 'model_bundled': model,
        'source_only': True, 'repository_import': False, 'vm_shutdown_verified': True}
    if model:
        result.update(warmup_requests=2, user_requests=2, model_bytes_verified=True, stale_rebind_verified=True)
    return result


def verify_image(directory, *, source_root=None, require_live=True, verify_disk=True):
    try:
        return _verify_image(Path(directory), Path(source_root) if source_root else None, require_live, verify_disk)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        return {'schema_version': 1, 'outcome': 'FAIL', 'reasons': [str(exc) or type(exc).__name__]}


def _verify_operating_boot(image_root, boot_dir, source_root=None, *, require_live=True, launch_validator):
    """Shared archive replay; callers supply their explicit launch contract."""
    try:
        directory, path = Path(image_root), Path(boot_dir)
        manifest = validate_manifest(directory)
        expected_session_schema = image_session_schema(directory, manifest)
        model = is_model(manifest)
        source_root = Path(source_root) if source_root else directory / 'runtime-source'
        for name, expected in manifest['runtime_files'].items():
            require(digest(read(source_root / name)) == expected, 'current_runtime_hash:' + name)
        before, after = record(path / 'disk-before.json'), record(path / 'disk-after.json')
        disk_record(before)
        disk_record(after)
        require(before['path'] == after['path'] and before['size_bytes'] == after['size_bytes'], 'disk_identity')
        launch = record(path / 'launch.json')
        launch_validator(launch, before, smoke=False, model=model)
        vm = record(path / 'vm-result.json')
        keys(vm, {'schema_version', 'process_exit_code', 'host_killed', 'shutdown_observed', 'runner_error'}, 'vm_keys')
        schema(vm['schema_version'])
        require(type(vm['process_exit_code']) is int and vm['process_exit_code'] == 0 and vm['host_killed'] is False
                and vm['shutdown_observed'] is True and vm['runner_error'] is None, 'vm_exit')
        raw = read(path / 'serial.log', SERIAL_LIMIT)
        header, exported = export_from_serial(raw)
        files = archive_files(path / 'archive')
        require(exported == files, 'export_copy_mismatch')
        require(files.get('image.json') == read(directory / 'image-manifest.json'), 'installed_manifest_bytes')
        boot, worker, result, stop = validate_archive(path / 'archive', manifest, None, require_live=require_live)
        require(header['boot_id'] == boot['boot_id'], 'export_boot')
        validate_archive_manifest(files, boot['boot_id'])
        validate_serial(raw, boot, result, record(path / 'archive/poweroff.json'), files['session/console.log'], result['worker_exit_code'])
        session_schema = decode(files['session/session.events.jsonl'].splitlines()[0])['schema_version']
        require(session_schema == expected_session_schema if expected_session_schema is not None
                else session_schema not in (8, 9), 'image_session_source_version')
        console = verify_session(path / 'archive/session', source_root=source_root, require_live=require_live,
            process_exit=0, expected_commands=launch['stdin_commands'], expected_session_id=result['session_id'])
        require(console['outcome'] == 'PASS', 'console:' + json.dumps(console.get('reasons', [])))
        validate_running_kernel(directory, files)
        svc = verify_service_runs(path / 'archive/service', source_root=source_root, allow_absent=True, require_live=require_live)
        require(svc['outcome'] == 'PASS', 'service_cleanup:' + json.dumps(svc.get('reasons', [])))
        if svc['state'] == 'ABSENT':
            from verify_service import public
            public(stop, 'stop')
            require(stop['state'] == 'ABSENT' and stop['outcome'] == 'OK', 'absent_service_cleanup')
        else:
            require(svc['runs'][-1] == stop and all(row['boot_id'] == boot['boot_id'] for row in svc['runs']), 'service_boot_scope')
        commands = [decode(line)['data'] for line in files['session/session.events.jsonl'].splitlines()
                    if decode(line)['event'] == 'COMMAND']
        actors = {'runs': [], 'backend_runs': []}
        if model:
            actors = validate_model_actors(path / 'archive', directory, manifest, boot, worker, commands, source_root,
                                           require_live=require_live, allow_recovered=True)
        else:
            for command in commands:
                if command['name'] in ('agent', 'room', 'cell', 'resources', 'ask', 'backend'):
                    public = command['result']
                    require(public.get('source_record') is None and public.get('inference_receipt') is None
                            and public.get('descriptor') is None, 'basic_no_model_activity')
        require(file_hash(directory / 'system.raw') == (after['size_bytes'], after['sha256']), 'terminal_disk_hash')
        result = {'schema_version': 2 if model else 1, 'outcome': 'PASS', 'reasons': [], 'profile': manifest['profile'], 'boot_id': boot['boot_id'],
            'session_id': result['session_id'], 'worker_uid': worker['uid'], 'commands': console['command_count'],
            'service_runs': len(svc['runs']), 'dns_observed': console['dns_observed'], 'https_observed': console['https_observed'],
            'prior_history_replayed': False, 'disk_bytes_verified': True, 'vm_shutdown_verified': True,
            'main_started': bool(actors['runs']), 'model_bundled': model}
        if model:
            result.update(main_runs=len(actors['runs']), backend_runs=len(actors['backend_runs']),
                          warmup_requests=len(actors['runs']), user_requests=sum(len(run['requests']) for run in actors['runs']),
                          model_bytes_verified=True)
            recovered = [run for run in actors['backend_runs'] if run['state'] == 'RECOVERED']
            if recovered:
                result.update(backend_recovered_runs=len(recovered),
                              backend_normal_runs=len(actors['backend_runs']) - len(recovered))
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        return {'schema_version': 1, 'outcome': 'FAIL', 'reasons': [str(exc) or type(exc).__name__]}


def verify_operating_boot(image_root, boot_dir, source_root=None, *, require_live=True):
    """Verify one ordinary disk boot with the unchanged strict normal launch."""
    if (Path(image_root) / 'fault-instrumentation.json').exists():
        return {'schema_version': 1, 'outcome': 'FAIL', 'reasons': ['image_contract:expected_fault_image_not_normal']}
    return _verify_operating_boot(image_root, boot_dir, source_root, require_live=require_live,
                                  launch_validator=validate_launch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact_dir', type=Path)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    result = verify_image(args.artifact_dir, source_root=args.source_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['outcome'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
