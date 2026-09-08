#!/usr/bin/env python3
"""Record installation observations inside the isolated image builder guest."""
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_model_input(device, destination, expected):
    """Copy exactly one pinned model from the read-only installer input."""
    checksum, remaining = hashlib.sha256(), expected['size_bytes']
    with device.open('rb') as source, destination.open('xb') as target:
        while remaining:
            data = source.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError('model input is truncated')
            target.write(data)
            checksum.update(data)
            remaining -= len(data)
        padding = source.read(512)
        if padding != b'\0' * (-expected['size_bytes'] % 512) or source.read(1):
            raise ValueError('model input has unexpected trailing bytes')
        target.flush()
        os.fsync(target.fileno())
    if checksum.hexdigest() != expected['sha256']:
        raise ValueError('model input hash mismatch')
    destination.chmod(0o444)


def install_model(target, source, manifest, proof):
    bundle = manifest['model_bundle']
    model_name, backend_name = 'Qwen3-0.6B-Q8_0.gguf', 'llamafile-0.10.5-thin.exe'
    if set(bundle['files']) != {model_name, backend_name}:
        raise ValueError('unexpected model bundle files')
    # The installer is the only phase with this device. Its serial, exact size
    # and read-only bit distinguish it from the newly owned target disk.
    block = Path('/sys/class/block/vdc')
    if ((block / 'serial').read_text().strip() != 'AIOS_MODEL_INPUT'
            or (block / 'ro').read_text().strip() != '1'
            or int((block / 'size').read_text()) != (bundle['files'][model_name]['size_bytes'] + 511) // 512):
        raise ValueError('unexpected model input device')
    installed = target / 'opt/aios/inference'
    installed.mkdir(mode=0o755, exist_ok=False)
    copy_model_input(Path('/dev/vdc'), installed / model_name, bundle['files'][model_name])
    shutil.copyfile(source / 'model' / backend_name, installed / backend_name)
    (installed / backend_name).chmod(0o444)
    observed = {name: {'size_bytes': (installed / name).stat().st_size,
                       'sha256': digest(installed / name)} for name in (backend_name, model_name)}
    if observed != bundle['files']:
        raise ValueError('installed model bundle mismatch')
    shutil.copyfile(source / 'model/model-config.json', target / 'etc/aios/model.json')
    (target / 'etc/aios/model.json').chmod(0o444)
    shutil.copyfile(source / 'model/model-config.json', proof / 'model-config.json')
    shutil.copyfile(source / 'model/inference-provenance.json', proof / 'inference-provenance.json')
    shutil.copytree(source / 'model/provenance', proof / 'provenance')
    if (digest(proof / 'model-config.json') != bundle['config_sha256']
            or digest(proof / 'inference-provenance.json') != bundle['provenance_sha256']):
        raise ValueError('installed model metadata mismatch')
    receipt_files = {}
    for name, values in observed.items():
        info = (installed / name).stat()
        receipt_files[name] = {'path': '/opt/aios/inference/' + name, **values,
                              'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    (proof / 'model-integrity.json').write_text(json.dumps({'schema_version': 1,
        'verification': 'installed-read-complete', 'files': receipt_files}, indent=2) + '\n')


def main():
    target = Path('/mnt/target')
    source = Path('/mnt/source')
    runtime = target / 'opt/aios/linux'
    manifest = json.loads((source / 'image-manifest.json').read_text())
    observed = {p.relative_to(runtime).as_posix(): digest(p)
                for p in sorted(runtime.rglob('*')) if p.is_file()}
    if observed != manifest['runtime_files']:
        raise ValueError('installed runtime differs from build input')
    proof = target / 'usr/share/aios/installation-files'
    if manifest['profile'] == 'local-model-cli':
        install_model(target, source, manifest, proof)
    (proof / 'installed-runtime.json').write_text(json.dumps(observed, indent=2) + '\n')
    # The build includes exact local boot/init/package receipts; it does not
    # assert bit-reproducible future package resolution from a moving mirror.
    for label, path in {'kernel-image': target / 'boot/vmlinuz-virt',
                        'initramfs': target / 'boot/initramfs-virt',
                        'bootloader-config': target / 'boot/extlinux.conf',
                        'inittab': target / 'etc/inittab',
                        'boot-hook': target / 'usr/local/sbin/aios-start-system'}.items():
        (proof / (label + '.sha256')).write_text(digest(path) + '\n')
    manifest['installation_files'] = {p.relative_to(proof).as_posix(): digest(p)
                                      for p in sorted(proof.rglob('*')) if p.is_file()}
    if digest(target / 'etc/aios/boot.json') != manifest['boot_config_sha256']:
        raise ValueError('installed config differs from build input')
    (target / 'usr/share/aios/image.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('AIOS_IMAGE_MANIFEST=' + json.dumps(manifest, separators=(',', ':')))


if __name__ == '__main__':
    main()
