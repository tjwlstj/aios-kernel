#!/usr/bin/env python3
"""Select a persistent user image without restoring or replacing its disk."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from image_host_lock import image_key, named_lock, storage_root
from image_selection_contract import inspect_source, inspect_user, validate_entry
from verify_image import decode, exact_json_equal

PROFILES = {'basic-cli': 'local-basic', 'local-model-cli': 'local-model'}
ENTRY_KEYS = {'image_directory', 'source_directory', 'image_id', 'cli_version',
              'manifest_sha256', 'source_verdict_sha256', 'working_copy_sha256'}


def require(condition, reason):
    if not condition:
        raise ValueError('image selection: ' + reason)


def read_json(path):
    with path.open('rb') as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, 'record is too large')
    return decode(raw)


def entry_shape(value):
    require(type(value) is dict and value.keys() == ENTRY_KEYS, 'invalid selection entry')
    require(all(type(item) is str and item and item.isprintable() for item in value.values()), 'invalid entry values')
    for key in ('image_directory', 'source_directory'):
        require(Path(value[key]).is_absolute(), 'selection paths must be absolute')
    for key in ('manifest_sha256', 'source_verdict_sha256', 'working_copy_sha256'):
        require(len(value[key]) == 64 and all(c in '0123456789abcdef' for c in value[key]), 'invalid hash')
    require(str(uuid.UUID(value['image_id'])) == value['image_id'], 'invalid image identity')


class SelectionStore:
    def __init__(self, profile, root=None):
        require(profile in PROFILES, 'unknown profile')
        self.profile = profile
        self.root = Path(root) if root is not None else storage_root()
        self.path = self.root / 'image-selections' / (PROFILES[profile] + '.json')
        self.legacy = self.root / 'operating-images' / PROFILES[profile]

    def lock(self):
        return named_lock('image-selection:' + self.profile, root=self.root)

    def read(self):
        if not self.path.exists():
            require(not self.path.is_symlink(), 'dangling selection link')
            return None
        require(not self.path.is_symlink(), 'selection must not be a symlink')
        value = read_json(self.path)
        require(type(value) is dict and value.keys() == {'schema_version', 'profile', 'revision', 'current', 'previous'}, 'invalid state fields')
        require(type(value['schema_version']) is int and value['schema_version'] == 1
                and value['profile'] == self.profile, 'invalid state schema or profile')
        require(type(value['revision']) is int and 1 <= value['revision'] < 2**63, 'invalid revision')
        entry_shape(value['current'])
        if value['previous'] is not None:
            entry_shape(value['previous'])
        return value

    def publish(self, current, previous, revision):
        entry_shape(current)
        if previous is not None:
            entry_shape(previous)
        value = {'schema_version': 1, 'profile': self.profile, 'revision': revision,
                 'current': current, 'previous': previous}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.parent / (self.path.stem + '-' + str(uuid.uuid4()) + '.tmp')
        # Keep current/previous in one file. A failed replace preserves old state.
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        return value

    def hold_image(self, stack, held, directory):
        key = image_key(directory)
        if key not in held:
            stack.enter_context(named_lock(key, root=self.root))
            held.add(key)

    def current(self, state, stack, held):
        if state is not None:
            directory = Path(state['current']['image_directory'])
            self.hold_image(stack, held, directory)
            # Old current may have a failed latest boot. Explicitly selecting a
            # healthy previous image must still be possible; never run it here.
            return state['current']
        if self.legacy.exists():
            self.hold_image(stack, held, self.legacy)
            return inspect_user(self.legacy, self.profile)
        return None

    def user_for(self, supplied, stack, held, destination=None):
        directory = Path(supplied).resolve(strict=True)
        self.hold_image(stack, held, directory)
        require(not (directory / 'fault-instrumentation.json').exists(), 'test images cannot be selected')
        if (directory / 'verdict.json').exists():
            inspect_source(directory, self.profile)
            target = Path(destination) if destination is not None else directory.with_name(directory.name + '-user')
            if not target.exists():
                from qemu_image import clone_verified
                print('[AIOS] Creating a separate persistent user disk.', file=sys.stderr, flush=True)
                clone_verified(directory, target)
            self.hold_image(stack, held, target)
            entry = inspect_user(target, self.profile)
            require(Path(entry['source_directory']).samefile(directory), 'user copy belongs to another source')
            return entry
        return inspect_user(directory, self.profile)

    def change(self, action, supplied=None):
        require(action in ('select', 'rollback'), 'invalid change action')
        require((action == 'select') == (supplied is not None), 'select requires an image; rollback takes no image')
        with self.lock(), ExitStack() as stack:
            held = set()
            state = self.read()
            old = self.current(state, stack, held)
            if action == 'rollback':
                require(state is not None and state['previous'] is not None, 'no previous selection')
                candidate = state['previous']
                self.hold_image(stack, held, Path(candidate['image_directory']))
                validate_entry(candidate, self.profile)
            else:
                candidate = self.user_for(supplied, stack, held)
            if old is not None and Path(old['image_directory']).samefile(candidate['image_directory']):
                require(exact_json_equal(old, candidate), 'same image has changed selection metadata')
                return {'action': action, 'changed': False, 'state': state,
                        'current': old, 'previous': state['previous'] if state else None}
            revision = state['revision'] + 1 if state else 1
            require(revision < 2**63, 'revision exhausted')
            changed = self.publish(candidate, old, revision)
            return {'action': action, 'changed': True, 'state': changed,
                    'current': candidate, 'previous': old}

    def show(self):
        with self.lock(), ExitStack() as stack:
            state = self.read()
            current = self.current(state, stack, set())
            return {'action': 'show', 'changed': False, 'state': state,
                    'current': current, 'previous': state['previous'] if state else None}

    def run(self, qemu, repo, *, supplied=None, offline=False):
        with self.lock():
            with ExitStack() as stack:
                held = set()
                if supplied is not None:
                    current = self.user_for(supplied, stack, held)
                else:
                    state = self.read()
                    current = self.current(state, stack, held)
                    if current is None:
                        pointer_name = 'verified-model-image.json' if self.profile == 'local-model-cli' else 'verified-image.json'
                        pointer = read_json(Path(repo) / 'build/hosted-image' / pointer_name)
                        require(type(pointer.get('schema_version')) is int and pointer['schema_version'] == 1
                                and type(pointer.get('image_directory')) is str
                                and Path(pointer['image_directory']).is_absolute(), 'invalid legacy image pointer')
                        current = self.user_for(pointer['image_directory'], stack, held, destination=self.legacy)
                    validate_entry(current, self.profile)
            print('[AIOS] Starting CLI %s from your persistent image: %s' %
                  (current['cli_version'], current['image_directory']), flush=True)
            # The child acquires its own image lock before replay/ordinal/Popen;
            # it retains that lock even if this selection parent is lost.
            command = [sys.executable, '-B', str(Path(__file__).with_name('qemu_image.py')),
                       'run', '--qemu', str(qemu), '--image-dir', current['image_directory']]
            if self.profile == 'local-model-cli':
                command.append('--agent')
            if offline:
                command.append('--offline')
            return subprocess.call(command)


def display(result):
    print('[AIOS] ' + ('Default image selected.' if result['changed'] else 'Current image selection.'))
    for name, entry in (('Current', result['current']), ('Previous', result['previous'])):
        print('  %s: %s' % (name, 'none' if entry is None else
              'CLI %s | %s' % (entry['cli_version'], entry['image_directory'])))
    if result['changed']:
        print('  Existing disks and session histories remain in their own images.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('select', 'show', 'rollback', 'run'))
    parser.add_argument('--agent', action='store_true')
    parser.add_argument('--image-dir', type=Path)
    parser.add_argument('--qemu', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        profile = 'local-model-cli' if args.agent else 'basic-cli'
        if args.action == 'run' and args.image_dir is not None and not args.agent:
            # Preserve the earlier explicit-model Run invocation without -Agent.
            profile = read_json(args.image_dir / 'image-manifest.json')['profile']
        store = SelectionStore(profile)
        if args.action == 'run':
            require(args.qemu is not None and args.qemu.is_file(), 'QEMU executable is missing')
            return store.run(args.qemu, Path(__file__).resolve().parents[2], supplied=args.image_dir, offline=args.offline)
        require(not args.offline, 'offline applies only to Run')
        if args.action == 'show':
            require(args.image_dir is None, 'Selection takes no image argument')
            result = store.show()
        else:
            result = store.change(args.action, args.image_dir)
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            display(result)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('[AIOS] ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
