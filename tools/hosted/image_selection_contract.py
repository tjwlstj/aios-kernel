"""Read-only pins for selecting a normal verified image and its user copy.

The parent selector holds its profile and image locks. This module does not
execute retained product sources or change an image. Source acceptance is full;
the latest user boot is replayed in full. Earlier user boots receive disk-chain,
archive/worker/serial and prior-history checks, not repeated actor replay.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

import verify_image as image

PROFILES = {'basic-cli', 'local-model-cli'}
ENTRY_KEYS = {'image_directory', 'source_directory', 'image_id', 'cli_version',
              'manifest_sha256', 'source_verdict_sha256', 'working_copy_sha256'}
VALIDATION_SCOPE = 'normal-source-full; user-latest-operating-boot-full; earlier-user-boot-chain-metadata'
# aios_boot.archive.previous_histories accepts at most MAX_HISTORIES - 1
# existing histories before starting the next boot. Retained sources are checked.
MAX_HISTORIES = 512


def require(condition, reason):
    if not condition:
        raise ValueError('image_selection:' + reason)


def _directory(value):
    path = Path(value).resolve(strict=True)
    require(path.is_dir(), 'directory')
    return path


def same_path(left, right):
    """Permit aliases only when the filesystem confirms the same object."""
    return Path(left).samefile(Path(right))


def normal_directory(path):
    require(not (path / 'fault-instrumentation.json').exists()
            and not (path / 'fault-instrumentation.json').is_symlink(), 'expected_fault_image_not_selectable')


def cli_version(raw):
    """Read a literal VERSION assignment without importing the product module."""
    tree = ast.parse(raw.decode('utf-8'))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'VERSION']
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
        and node.id == 'VERSION' and isinstance(node.ctx, ast.Store)]
    require(len(assignments) == len(stores) == 1 and isinstance(assignments[0].value, ast.Constant)
            and type(assignments[0].value.value) is str, 'literal_cli_version')
    value = assignments[0].value.value
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value) is not None and len(value) <= 32, 'cli_version')
    return value


def history_capacity(raw):
    tree = ast.parse(raw.decode('utf-8'))
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'MAX_HISTORIES'
        and isinstance(node.value, ast.Constant)]
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
        and node.id == 'MAX_HISTORIES' and isinstance(node.ctx, ast.Store)]
    require(len(values) == len(stores) == 1 and type(values[0]) is int and values[0] == MAX_HISTORIES, 'history_capacity_contract')
    return values[0]


def exact_files(path, expected):
    files = image.archive_files(path)
    require(files.keys() == set(expected), 'installed_file_set:' + path.name)
    return files


def disk_equal(left, right):
    image.disk_record(left)
    image.disk_record(right)
    return (same_path(left['path'], right['path']) and left['size_bytes'] == right['size_bytes']
            and left['sha256'] == right['sha256'])


def source_details(source, profile):
    require(type(profile) is str and profile in PROFILES, 'profile')
    source = _directory(source)
    normal_directory(source)
    manifest_raw, verdict_raw = image.read(source / 'image-manifest.json'), image.read(source / 'verdict.json')
    manifest, stored = image.decode(manifest_raw), image.decode(verdict_raw)
    require(manifest.get('profile') == profile, 'source_profile')
    require(stored.get('outcome') == 'PASS', 'source_stored_verdict')
    replay = image.verify_image(source)
    require(replay['outcome'] == 'PASS', 'source_verification:' + str(replay.get('reasons')))
    require(image.exact_json_equal(replay, stored), 'source_verdict_replay')
    runtime = exact_files(source / 'runtime-source', image.IMAGE_SOURCES)
    require(len(runtime) == 35, 'source_runtime35')
    history_capacity(runtime['aios_boot/__init__.py'])
    exact_files(source / 'installation-files', manifest['installation_files'])
    require(image.digest(image.read(source / 'boot.json')) == manifest['boot_config_sha256'], 'source_boot_config')
    require(image.record(source / 'build-result.json').get('outcome') == 'PASS', 'source_build')
    require(image.read(source / 'image-manifest.json') == manifest_raw and image.read(source / 'verdict.json') == verdict_raw,
            'source_changed_during_inspection')
    info = {'image_id': manifest['image_id'], 'cli_version': cli_version(runtime['aios_console/__init__.py']),
        'manifest_sha256': image.digest(manifest_raw), 'source_verdict_sha256': image.digest(verdict_raw)}
    return source, manifest, replay, info


def inspect_source(source: Path, profile: str) -> dict:
    """Fully replay a preserved normal source using its own retained runtime."""
    return source_details(source, profile)[3]


def history_entry(archive):
    return {'archive_manifest_sha256': image.digest(image.read(archive / 'archive-manifest.json')),
            'root_result_sha256': image.digest(image.read(archive / 'root-result.json'))}


def user_boots(path, manifest, source, source_replay, initial):
    boots_dir = path / 'boots'
    if not boots_dir.exists():
        require(not boots_dir.is_symlink(), 'missing_boot_directory')
        require(image.file_hash(path / 'system.raw') == (initial['size_bytes'], initial['sha256']), 'unused_clone_disk')
        return
    require(boots_dir.is_dir() and not boots_dir.is_symlink(), 'boot_directory')
    boots = list(boots_dir.iterdir())
    require(all(re.fullmatch(r'boot-[0-9]{2,3}', boot.name) for boot in boots), 'boot_sequence')
    boots.sort(key=lambda boot: int(boot.name[5:]))
    # clone_verified does not create this directory. An empty one means a
    # launch/partial-copy boundary was crossed without a completed boot.
    require(0 < len(boots) and [boot.name for boot in boots]
            == ['boot-%02d' % index for index in range(1, len(boots) + 1)], 'boot_sequence')
    require(len(source_replay['boots']) + len(boots) <= MAX_HISTORIES - 1, 'next_boot_history_capacity')
    previous = {row['boot_id']: history_entry(source / 'boots' / ('boot-%02d' % (index + 1)) / 'archive')
                for index, row in enumerate(source_replay['boots'])}
    prior_disk = initial
    for index, boot_path in enumerate(boots):
        require(boot_path.is_dir() and not boot_path.is_symlink(), 'boot_directory')
        require(not any(name.startswith('fault-') for name in (p.name for p in boot_path.iterdir())), 'fault_boot_not_selectable')
        before, after = image.record(boot_path / 'disk-before.json'), image.record(boot_path / 'disk-after.json')
        require(disk_equal(before, prior_disk), 'user_disk_chain')
        image.disk_record(after)
        require(same_path(after['path'], path / 'system.raw') and same_path(before['path'], path / 'system.raw')
                and before['size_bytes'] == after['size_bytes'], 'user_disk_identity')
        image.validate_launch(image.record(boot_path / 'launch.json'), before, smoke=False, model=image.is_model(manifest))
        vm = image.record(boot_path / 'vm-result.json')
        require(image.exact_json_equal(vm, {'schema_version': 1, 'process_exit_code': 0, 'host_killed': False,
            'shutdown_observed': True, 'runner_error': None}), 'user_vm_failed')
        archive = boot_path / 'archive'
        boot, worker, result, _stop = image.validate_archive(archive, manifest, previous)
        require(boot['boot_id'] not in previous, 'reused_user_boot')
        files = image.archive_files(archive)
        image.validate_archive_manifest(files, boot['boot_id'])
        raw = image.read(boot_path / 'serial.log', image.SERIAL_LIMIT)
        header, exported = image.export_from_serial(raw)
        require(exported == files and header['boot_id'] == boot['boot_id'], 'user_export_copy')
        image.validate_serial(raw, boot, result, image.record(archive / 'poweroff.json'),
                              files['session/console.log'], result['worker_exit_code'])
        stored = image.record(boot_path / 'verdict.json')
        require(stored.get('outcome') == 'PASS' and stored.get('boot_id') == boot['boot_id']
                and stored.get('session_id') == worker['session_id'], 'user_stored_verdict')
        if index == len(boots) - 1:
            replay = image.verify_operating_boot(path, boot_path)
            require(replay['outcome'] == 'PASS', 'latest_user_verification:' + str(replay.get('reasons')))
            require(image.exact_json_equal(stored, replay), 'latest_user_verdict_replay')
        previous[boot['boot_id']] = history_entry(archive)
        prior_disk = after


def inspect_user(directory: Path, profile: str) -> dict:
    """Pin a user copy; a used disk is checked against its latest complete boot.

VALIDATION_SCOPE describes the earlier-boot limit. No lock or product import is
performed here; the selecting caller owns its profile and per-image locks.
"""
    path = _directory(directory)
    normal_directory(path)
    require(not (path / 'verdict.json').exists(), 'verified_source_is_not_user_copy')
    working_raw = image.read(path / 'working-copy.json')
    working = image.decode(working_raw)
    image.keys(working, {'schema_version', 'source_directory', 'source_image', 'initial_image', 'source_verdict_sha256'}, 'working_copy_keys')
    image.schema(working['schema_version'])
    require(type(working['source_directory']) is str and Path(working['source_directory']).is_absolute(), 'working_source_path')
    source, manifest, source_replay, info = source_details(working['source_directory'], profile)
    require(not same_path(path, source) and source not in path.parents and path not in source.parents, 'separate_user_copy')
    require(working['source_verdict_sha256'] == info['source_verdict_sha256'], 'working_source_verdict')
    terminal = image.record(source / 'boots' / ('boot-%02d' % len(source_replay['boots'])) / 'disk-after.json')
    require(disk_equal(working['source_image'], terminal)
            and same_path(working['source_image']['path'], source / 'system.raw'), 'working_source_disk')
    initial = image.record(path / 'image-before.json')
    require(disk_equal(working['initial_image'], initial) and same_path(initial['path'], path / 'system.raw')
            and initial['sha256'] == terminal['sha256'] and initial['size_bytes'] == terminal['size_bytes'], 'working_initial_disk')
    for name in ('image-manifest.json', 'boot.json', 'build-result.json'):
        require(image.read(path / name) == image.read(source / name), 'working_installed_bytes:' + name)
    user_manifest = image.validate_manifest(path)
    require(image.exact_json_equal(user_manifest, manifest), 'working_manifest')
    for name, expected in (('runtime-source', image.IMAGE_SOURCES), ('installation-files', manifest['installation_files'])):
        require(exact_files(path / name, expected) == exact_files(source / name, expected), 'working_installed_tree:' + name)
    user_boots(path, manifest, source, source_replay, initial)
    require(image.read(path / 'working-copy.json') == working_raw, 'working_copy_changed_during_inspection')
    return {'image_directory': str(path), 'source_directory': str(source), **info,
            'working_copy_sha256': image.digest(working_raw)}


def validate_entry(entry, profile) -> Path:
    """Reinspect current files and compare all saved pins, allowing path aliases."""
    image.keys(entry, ENTRY_KEYS, 'selection_entry_keys')
    for key in ('image_directory', 'source_directory'):
        require(type(entry[key]) is str and Path(entry[key]).is_absolute(), 'entry_path')
    fresh = inspect_user(Path(entry['image_directory']), profile)
    for key in ENTRY_KEYS:
        if key in ('image_directory', 'source_directory'):
            require(same_path(entry[key], fresh[key]), 'entry_path_changed:' + key)
        else:
            require(image.exact_json_equal(entry[key], fresh[key]), 'entry_pin_changed:' + key)
    return _directory(fresh['image_directory'])
