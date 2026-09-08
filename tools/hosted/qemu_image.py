#!/usr/bin/env python3
"""Build and operate local disk-only AIOS basic and local-model images."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from qemu_console import SerialGuest, display, save_json, export_guest

ISO_URL = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-virt-3.24.1-x86_64.iso'
ISO_SHA = 'e73a6241bd5f3c5c2d4d38c02cc52c378c0415a7c888bd292066bf36e0f41a39'
DISK_BYTES = 4 * 1024**3
MODEL_PROMPT_TIMEOUT = 650
ONLINE = ['about', 'status', 'hardware', 'net status', 'service status', 'service start',
          'service status', 'room status', 'ask Say hello.', 'resolve example.com',
          'fetch https://example.com/', 'exit']
OFFLINE = [line for line in ONLINE if not line.startswith(('resolve ', 'fetch '))]
MODEL_PREFIX = ['about', 'status', 'hardware', 'net status', 'service status', 'service start',
    'service status', 'backend status', 'agent status', 'backend start', 'agent start', 'room status',
    'ask Say hello.', 'room discover', 'room bind', 'resources link']
MODEL_ONLINE = MODEL_PREFIX + ['ask What is the capital of France? Answer in one short sentence.',
    'resolve example.com', 'fetch https://example.com/', 'exit']
MODEL_OFFLINE = MODEL_PREFIX + ['cell deactivate', 'cell activate', 'ask Say hello.', 'resources status',
    'room discover', 'room reconcile', 'resources link',
    'ask What is the Moon? Answer in one short sentence.', 'exit']


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def disk_record(path: Path) -> dict:
    return {'schema_version': 1, 'path': str(path.resolve()), 'sha256': digest(path),
            'size_bytes': path.stat().st_size}


def base_command(qemu: Path, *, model=False) -> list[str]:
    return [str(qemu), '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max', '-m', '3072' if model else '768',
            '-smp', '2', '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot']


def display_image(data: bytes) -> None:
    # Machine records stay in the raw serial artifact; the user sees the boot
    # messages and AIOS command output in the interactive terminal.
    display(b''.join(line for line in data.splitlines(keepends=True)
                     if not line.startswith(b'AIOS_IMAGE_')))


def finish_guest(guest: SerialGuest, result: dict) -> None:
    if guest.process.poll() is None:
        result['host_killed'] = True
        guest.process.kill()
    guest.process.wait(timeout=15)
    guest.reader.join(timeout=15)
    result['process_exit_code'] = guest.process.returncode
    if guest.reader.is_alive() or guest.reader_error:
        result['runner_error'] = guest.reader_error or 'serial_reader_not_drained'


def build(args, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 1, 'outcome': 'FAIL', 'reason': 'not_completed'}
    save_json(output / 'build-result.json', result)
    repo = Path(__file__).resolve().parents[2]
    if digest(args.iso) != ISO_SHA:
        raise ValueError('pinned Linux ISO hash mismatch')
    installer_hashes = {}
    for member, supplied in (('boot/vmlinuz-virt', args.kernel), ('boot/initramfs-virt', args.initramfs)):
        embedded = subprocess.run(['tar', '-xOf', str(args.iso), member], capture_output=True, check=True).stdout
        expected = hashlib.sha256(embedded).hexdigest()
        if digest(supplied) != expected:
            raise ValueError('installer boot input differs from pinned ISO: ' + member)
        installer_hashes[member] = expected
    if not (repo / 'hosted/linux/aios-image-boot.py').is_file():
        raise ValueError('product image boot entry is missing')
    runtime = output / 'runtime-source'
    shutil.copytree(repo / 'hosted/linux', runtime,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    runtime_files = {p.relative_to(runtime).as_posix(): digest(p)
                     for p in sorted(runtime.rglob('*')) if p.is_file()}
    use_model = getattr(args, 'agent', False)
    config = {'schema_version': 2 if use_model else 1, 'profile': 'local-model' if use_model else 'basic',
              'model_config': '/etc/aios/model.json' if use_model else None}
    save_json(output / 'boot.json', config)
    manifest = {'schema_version': 1, 'image_id': str(uuid.uuid4()), 'profile': 'basic-cli',
                'substrate': 'linux-hosted', 'iso': {'url': ISO_URL, 'sha256': ISO_SHA, 'version': '3.24.1'},
                'runtime_files': runtime_files, 'boot_config_sha256': digest(output / 'boot.json'),
                'installation_files': {}, 'source_only': True, 'repository_import': False,
                'redistribution_approved': False}
    if use_model:
        from prepare_inference import prepare, ARTIFACTS
        from model_backend import MODEL_ID
        if args.inference_cache is None:
            raise ValueError('local-model build requires an external inference cache')
        prepare(args.inference_cache)
        provenance = (args.inference_cache / 'provenance-receipt.json').read_bytes()
        (output / 'inference-provenance.json').write_bytes(provenance)
        shutil.copytree(args.inference_cache / 'provenance', output / 'provenance')
        model_config = {'schema_version': 1, 'endpoint': 'http://127.0.0.1:18081', 'model_id': MODEL_ID,
            'model_path': '/opt/aios/inference/' + ARTIFACTS[1]['name'], 'model_sha256': ARTIFACTS[1]['sha256'],
            'backend_path': '/opt/aios/inference/' + ARTIFACTS[0]['name'], 'backend_sha256': ARTIFACTS[0]['sha256'],
            'provenance_sha256': hashlib.sha256(provenance).hexdigest()}
        save_json(output / 'model-config.json', model_config)
        manifest.update(schema_version=2, profile='local-model-cli', model_bundle={
            'schema_version': 1, 'config_sha256': digest(output / 'model-config.json'),
            'provenance_sha256': hashlib.sha256(provenance).hexdigest(),
            'files': {item['name']: {'size_bytes': item['size'], 'sha256': item['sha256']} for item in ARTIFACTS}})
    tools_snapshot = output / 'build-tools'
    tools_snapshot.mkdir()
    for name in ('qemu_image.py', 'image_install.sh', 'image_start.sh', 'image_finalize.py'):
        shutil.copyfile(Path(__file__).parent / name, tools_snapshot / name)
    disk = output / 'system.raw'
    if shutil.disk_usage(output).free < DISK_BYTES + 512 * 1024**2:
        raise ValueError('not enough space for a new owned image')
    with disk.open('xb') as stream:
        stream.truncate(DISK_BYTES)
    save_json(output / 'build-input.json', {'schema_version': 1, 'git_head': subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip(),
        'git_status': subprocess.run(['git', 'status', '--porcelain'], cwd=repo, capture_output=True,
                                    text=True, check=True).stdout.splitlines(),
        'qemu_version': subprocess.run([str(args.qemu), '--version'], capture_output=True,
                                      text=True, check=True).stdout.strip(),
        'iso_sha256': ISO_SHA, 'installer_boot_files': installer_hashes,
        'disk_path': str(disk), 'disk_size_bytes': DISK_BYTES,
        'source_files': runtime_files,
        'build_tools': {p.name: digest(p) for p in tools_snapshot.iterdir()}})
    with tempfile.TemporaryDirectory(prefix='aios-image-build-') as scratch:
        scratch_path = Path(scratch).resolve()
        if scratch_path.parent != Path(tempfile.gettempdir()).resolve() or not scratch_path.name.startswith('aios-image-build-'):
            raise ValueError('unexpected build scratch path')
        share = scratch_path / 'share'
        share.mkdir()
        shutil.copytree(runtime, share / 'runtime')
        if args.guest_tests:
            shutil.copytree(runtime, share / 'test-repo/hosted/linux')
            shutil.copytree(repo / 'hosted/contracts', share / 'test-repo/hosted/contracts')
            shutil.copytree(repo / 'tools/hosted', share / 'test-repo/tools/hosted',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            oracle = share / 'test-repo/kernel/include/kernel/kernel_room_management.h'
            oracle.parent.mkdir(parents=True)
            shutil.copyfile(repo / 'kernel/include/kernel/kernel_room_management.h', oracle)
            shutil.copytree(share / 'test-repo', output / 'test-source')
        shutil.copyfile(output / 'boot.json', share / 'boot.json')
        if use_model:
            model_share = share / 'model'
            model_share.mkdir()
            shutil.copyfile(args.inference_cache / ARTIFACTS[0]['name'], model_share / ARTIFACTS[0]['name'])
            shutil.copyfile(output / 'model-config.json', model_share / 'model-config.json')
            shutil.copyfile(output / 'inference-provenance.json', model_share / 'inference-provenance.json')
            shutil.copytree(output / 'provenance', model_share / 'provenance')
            # The large model is a separately labelled read-only installer input,
            # never a drive attached to an operating boot.
            model_disk = scratch_path / 'model.raw'
            with (args.inference_cache / ARTIFACTS[1]['name']).open('rb') as source, model_disk.open('xb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
                target.write(b'\0' * (-ARTIFACTS[1]['size'] % 512))
        save_json(share / 'image-manifest.json', manifest)
        for original, target in [('image_install.sh', 'image_install.sh'),
                                 ('image_start.sh', 'aios-start-system'), ('image_finalize.py', 'image_finalize.py')]:
            # Shell files must be LF even on a checkout with autocrlf enabled.
            (share / target).write_bytes((tools_snapshot / original).read_bytes().replace(b'\r\n', b'\n'))
        command = base_command(args.qemu, model=use_model) + ['-kernel', str(args.kernel), '-initrd', str(args.initramfs),
            '-append', 'console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage', '-cdrom', str(args.iso),
            '-nic', 'user,model=e1000', '-drive', 'file=' + disk.as_posix() + ',format=raw,if=none,id=aiosdisk',
            '-device', 'virtio-blk-pci,drive=aiosdisk,serial=AIOS_BUILD_DISK',
            # Name every virtio device explicitly. Mixing implicit and explicit
            # devices reorders the share behind the optional model input.
            '-drive', 'file=fat:ro:' + share.as_posix() + ',format=raw,if=none,id=buildinput,readonly=on',
            '-device', 'virtio-blk-pci,drive=buildinput,serial=AIOS_BUILD_INPUT']
        if use_model:
            command += ['-drive', 'file=' + model_disk.as_posix() + ',format=raw,if=none,id=modelinput,readonly=on',
                        '-device', 'virtio-blk-pci,drive=modelinput,serial=AIOS_MODEL_INPUT']
        save_json(output / 'installer-launch.json', {'schema_version': 1, 'qemu_argv': command})
        guest = SerialGuest(command, output / 'installer-serial.log')
        vm = {'schema_version': 1, 'process_exit_code': None, 'host_killed': False,
              'shutdown_observed': False, 'runner_error': None}
        try:
            print('[AIOS image] Preparing the isolated image builder.', flush=True)
            guest.wait(rb'login:\s*$', 120)
            guest.send('root')
            guest.wait(rb'localhost:~#')
            guest.process.stdin.write(b'\x1b[1;1R')
            guest.process.stdin.flush()
            guest.step('Builder console', 'export TERM=dumb; stty -echo')
            guest.step('Builder network', 'ip link set lo up && ip link set eth0 up && udhcpc -i eth0 -q -n -t 5 -T 3', 45)
            guest.step('Official package source', "printf '%s\\n' https://dl-cdn.alpinelinux.org/alpine/v3.24/main > /etc/apk/repositories")
            guest.step('Read-only build inputs',
                'test "$(cat /sys/class/block/vdb/serial)" = AIOS_BUILD_INPUT && '
                'test "$(cat /sys/class/block/vdb/ro)" = 1 && '
                'mkdir -p /mnt/source && mount -o ro /dev/vdb1 /mnt/source')
            guest.step('Installing onto the new owned virtual disk', 'sh /mnt/source/image_install.sh', 480)
            if args.guest_tests:
                guest.step('Image runtime and verifier tests on Linux',
                    "cd /mnt/source/test-repo && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover "
                    "-s tools/hosted/tests -p 'test_hosted_image*.py' -v >/tmp/image-tests.log 2>&1; "
                    "printf '%s\\n' \"$?\" >/tmp/image-tests.exit", 240)
                export_guest(guest, '/tmp', output / 'linux-tests', ['image-tests.log', 'image-tests.exit'])
                if (output / 'linux-tests/image-tests.exit').read_text().strip() != '0':
                    raise ValueError('Linux image tests failed; original log retained')
            export_guest(guest, '/mnt/target/usr/share/aios', output / 'installed-metadata')
            installed = output / 'installed-metadata'
            shutil.copyfile(installed / 'image.json', output / 'image-manifest.json')
            shutil.copytree(installed / 'installation-files', output / 'installation-files')
            guest.step('Flush installed filesystem', 'sync && umount /mnt/target/boot && umount /mnt/target', 60)
            guest.send('poweroff -f')
            guest.wait(rb'reboot: Power down', 45)
            vm['shutdown_observed'] = True
            guest.process.wait(timeout=30)
        except Exception as exc:
            vm['runner_error'] = str(exc)
            print('[AIOS image] Build failed: ' + str(exc), flush=True)
        finally:
            finish_guest(guest, vm)
            save_json(output / 'installer-result.json', vm)
    if vm['process_exit_code'] == 0 and vm['shutdown_observed'] and not vm['host_killed'] and vm['runner_error'] is None:
        save_json(output / 'image-before.json', disk_record(disk))
        result = {'schema_version': 1, 'outcome': 'PASS', 'reason': 'local_image_built',
                  'operating_boot_verified': False}
        print('[AIOS image] Disk built. Disk-only cold boot verification is still required.', flush=True)
    else:
        result['reason'] = vm['runner_error'] or 'installer_not_clean'
    save_json(output / 'build-result.json', result)
    return 0 if result['outcome'] == 'PASS' else 1


def clone_verified(source: Path, output: Path) -> None:
    """Create a persistent working copy without changing acceptance artifacts."""
    source, output = source.resolve(), output.resolve()
    if source == output or output.exists() or source in output.parents:
        raise ValueError('working copy requires a new separate directory')
    verdict = json.loads((source / 'verdict.json').read_text())
    if verdict.get('outcome') != 'PASS':
        raise ValueError('working copy requires a verified source image')
    from verify_image import verify_image
    checked = verify_image(source)
    if checked['outcome'] != 'PASS':
        raise ValueError('source image verification failed: ' + str(checked))
    if shutil.disk_usage(output.parent).free < DISK_BYTES + 128 * 1024**2:
        raise ValueError('not enough space for a working copy')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('image-manifest.json', 'boot.json', 'build-result.json'):
        shutil.copyfile(source / name, output / name)
    for name in ('runtime-source', 'installation-files'):
        shutil.copytree(source / name, output / name)
    shutil.copyfile(source / 'system.raw', output / 'system.raw')
    copied = disk_record(output / 'system.raw')
    original = disk_record(source / 'system.raw')
    if copied['sha256'] != original['sha256'] or copied['size_bytes'] != original['size_bytes']:
        raise ValueError('working disk copy integrity failed')
    save_json(output / 'image-before.json', copied)
    save_json(output / 'working-copy.json', {'schema_version': 1, 'source_directory': str(source),
              'source_image': original, 'initial_image': copied,
              'source_verdict_sha256': digest(source / 'verdict.json')})


def decode_export(serial: bytes, destination: Path) -> None:
    lines = serial.replace(b'\r\n', b'\n').splitlines()
    begin = [x for x in lines if x.startswith(b'AIOS_IMAGE_EXPORT_BEGIN=')]
    end = [x for x in lines if x.startswith(b'AIOS_IMAGE_EXPORT_END=')]
    if len(begin) != 1 or len(end) != 1:
        raise ValueError('missing or duplicate image export boundaries')
    meta = json.loads(begin[0].partition(b'=')[2])
    if json.loads(end[0].partition(b'=')[2]) != meta:
        raise ValueError('image export boundaries disagree')
    records = [json.loads(x.partition(b'=')[2]) for x in lines if x.startswith(b'AIOS_IMAGE_EXPORT_FILE=')]
    if len(records) > 512 or len(records) != meta['file_count']:
        raise ValueError('image export file count')
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    for record in records:
        name = record['path']
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or any(p in ('', '.', '..') for p in name.split('/')):
            raise ValueError('unsafe image export path')
        raw = base64.b64decode(record['data_base64'], validate=True)
        total += len(raw)
        if len(raw) > 2 * 1024**2 or total > 16 * 1024**2 or len(raw) != record['bytes'] or hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('image export integrity/size')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    if total != meta['total_bytes']:
        raise ValueError('image export total bytes')
    save_json(destination.parent / 'export.json', meta)


def boot(args, image_root: Path, ordinal: int, *, offline=False, interactive=False) -> int:
    disk = image_root / 'system.raw'
    output = image_root / 'boots' / ('boot-' + str(ordinal).zfill(2))
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'disk-before.json', disk_record(disk))
    use_model = json.loads((image_root / 'image-manifest.json').read_text())['profile'] == 'local-model-cli'
    command = base_command(args.qemu, model=use_model) + ['-boot', 'c', '-nic', 'none' if offline else 'user,model=e1000',
        '-device', 'qemu-xhci', '-device', 'usb-kbd', '-drive', 'file=' + disk.as_posix() + ',format=raw,if=virtio']
    launch = {'schema_version': 1, 'qemu_argv': command, 'stdin_commands': [],
              'network': 'offline' if offline else 'online', 'firmware': 'bios', 'boot_method': 'disk'}
    save_json(output / 'launch.json', launch)
    vm = {'schema_version': 1, 'process_exit_code': None, 'host_killed': False,
          'shutdown_observed': False, 'runner_error': None}
    save_json(output / 'vm-result.json', vm)
    guest = SerialGuest(command, output / 'serial.log')
    commands = iter((MODEL_OFFLINE if offline else MODEL_ONLINE) if use_model else (OFFLINE if offline else ONLINE))
    printed = 0
    try:
        print('[AIOS image] Cold boot ' + str(ordinal) + (' (offline)' if offline else ''), flush=True)
        while True:
            # MAIN's bounded inference worker allows 420 seconds; the outer VM
            # observer must leave time for that worker and its cleanup verdict.
            match, base = guest.wait(rb'(?:^|\r?\n)aios> |(?:^|\r?\n)AIOS_IMAGE_RESULT=', MODEL_PROMPT_TIMEOUT if use_model else 150)
            display_image(bytes(guest.transcript[printed:guest.cursor]))
            printed = guest.cursor
            if match.group(0).endswith(b'AIOS_IMAGE_RESULT='):
                break
            if interactive:
                try:
                    line = input()
                except (EOFError, KeyboardInterrupt):
                    line = 'exit'
            else:
                line = next(commands, None)
                if line is None:
                    raise ValueError('CLI failed to exit after fixed commands')
                print(line, flush=True)
            if len(line) > 2048 or not all(c.isprintable() for c in line):
                if not interactive:
                    raise ValueError('expected one printable CLI command')
                print('[AIOS] Enter one printable command of at most 2048 characters. Showing help.', flush=True)
                line = 'help'
            launch['stdin_commands'].append(line)
            save_json(output / 'launch.json', launch)
            guest.send(line)
        guest.wait(rb'reboot: Power down', 90)
        vm['shutdown_observed'] = True
        guest.process.wait(timeout=30)
    except Exception as exc:
        vm['runner_error'] = str(exc)
        print('[AIOS image] Run failed: ' + str(exc), flush=True)
    finally:
        finish_guest(guest, vm)
        save_json(output / 'vm-result.json', vm)
        save_json(output / 'disk-after.json', disk_record(disk))
    try:
        decode_export((output / 'serial.log').read_bytes(), output / 'archive')
        root_result = json.loads((output / 'archive/root-result.json').read_text())
        if root_result.get('outcome') != 'PASS' or root_result.get('cleanup_ok') is not True:
            raise ValueError('guest session or cleanup failed')
    except Exception as exc:
        vm['runner_error'] = vm['runner_error'] or str(exc)
        save_json(output / 'vm-result.json', vm)
    return 0 if vm['process_exit_code'] == 0 and vm['shutdown_observed'] and not vm['host_killed'] and not vm['runner_error'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'smoke', 'run', 'clone'))
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--image-dir', type=Path, required=True)
    for name in ('iso', 'kernel', 'initramfs'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--guest-tests', action='store_true')
    parser.add_argument('--source-image', type=Path)
    parser.add_argument('--agent', action='store_true', help='Build a local-model profile with pinned local dependencies')
    parser.add_argument('--inference-cache', type=Path)
    args = parser.parse_args()
    output = args.image_dir.resolve()
    if (output / 'fault-instrumentation.json').exists():
        parser.error('this is an expected-fault test clone; use its separate recovery test runner')
    if args.action == 'clone':
        if args.source_image is None:
            parser.error('clone requires --source-image')
        clone_verified(args.source_image, output)
        return 0
    if args.action == 'build':
        if any(getattr(args, name) is None for name in ('iso', 'kernel', 'initramfs')):
            parser.error('build requires --iso, --kernel and --initramfs')
        return build(args, output)
    from image_host_lock import image_lock
    with image_lock(output):
        return operate(args, output, parser)


def operate(args, output, parser):
    """Hold the actual image lock through validation, VM exit and final verdict."""
    if not (output / 'build-result.json').is_file() or json.loads((output / 'build-result.json').read_text()).get('outcome') != 'PASS':
        parser.error('a successfully built image directory is required')
    if args.agent and json.loads((output / 'image-manifest.json').read_text())['profile'] != 'local-model-cli':
        parser.error('--agent requires a local-model image; preserving the supplied basic image')
    if args.action == 'smoke':
        model_profile = json.loads((output / 'image-manifest.json').read_text())['profile'] == 'local-model-cli'
        for ordinal, offline in enumerate((False, True) if model_profile else (False, False, True), 1):
            if boot(args, output, ordinal, offline=offline):
                return 1
        from verify_image import verify_image
        verdict = verify_image(output)
        save_json(output / 'verdict.json', verdict)
        print(json.dumps(verdict, indent=2), flush=True)
        return 0 if verdict['outcome'] == 'PASS' else 1
    if (output / 'verdict.json').exists():
        parser.error('preserve the verified disk; clone it to a working image directory before interactive use')
    from image_selection_contract import inspect_user
    print('[AIOS] Checking your saved image before starting.', flush=True)
    inspect_user(output, json.loads((output / 'image-manifest.json').read_text())['profile'])
    existing = list((output / 'boots').glob('boot-*'))
    ordinal = 1 + max((int(p.name.split('-')[1]) for p in existing), default=0)
    if boot(args, output, ordinal, offline=args.offline, interactive=True):
        return 1
    from verify_image import verify_operating_boot
    verdict = verify_operating_boot(output, output / 'boots' / ('boot-' + str(ordinal).zfill(2)))
    save_json(output / 'boots' / ('boot-' + str(ordinal).zfill(2)) / 'verdict.json', verdict)
    if verdict['outcome'] != 'PASS':
        print('[AIOS image] Session verification failed: ' + str(verdict.get('reasons', [])), flush=True)
        return 1
    print('[AIOS image] Session saved and guest shutdown verified.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
