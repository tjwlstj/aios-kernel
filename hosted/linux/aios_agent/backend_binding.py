"""Hold a backend execution identity separately from optional resource reads."""
from __future__ import annotations

import copy
import hashlib
import os
import select
import socket
import time
from pathlib import Path

from aios_resources import ResourceError
from aios_resources.backend import (MAX_FDS, TCP_LIMIT, attest, listener_proof, validate_descriptor)
from aios_resources.proc import ProcessReader, decimal, parse_stat, read_bounded

ACCEPT_SECONDS = 1.0
TERMINAL_FILE_LIMIT = 2 * 1024 * 1024


def _require(condition, code="backend-changed"):
    if not condition:
        raise ResourceError(code)


def _address(value):
    return {"address": value[0], "port": value[1]}


def _stamp(info):
    return {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_uid', 'st_mode')}


def _terminal_bytes(path):
    from aios_service.lifecycle import regular_file
    regular_file(path)
    raw = read_bounded(path, TERMINAL_FILE_LIMIT)
    regular_file(path)
    return raw


def _terminal_file(path):
    raw = _terminal_bytes(path)
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _tcp_address(value):
    return f"{int.from_bytes(socket.inet_aton(value['address']), 'little'):08X}:{value['port']:04X}"


def _connected_row(raw, local, remote, uid, inode=None):
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ResourceError("backend-recipient") from exc
    _require(lines and "local_address" in lines[0] and len(lines) <= 4096, "backend-recipient")
    candidates = []
    for line in lines[1:]:
        fields = line.split()
        _require(len(fields) >= 10, "backend-recipient")
        if fields[1:4] != [_tcp_address(local), _tcp_address(remote), "01"]:
            continue
        if decimal(fields[7]) != uid:
            raise ResourceError("backend-recipient")
        number = decimal(fields[9])
        if number == 0 or inode is not None and number != inode:
            continue
        candidates.append((number, line))
    _require(len(candidates) <= 1, "backend-recipient")
    return candidates[0] if candidates else None


def _owned_fd(reader, inode):
    target = f"socket:[{inode}]"
    directory = os.open("fd", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=reader._dirfd)
    try:
        names = os.listdir(directory)
        _require(len(names) <= MAX_FDS, "backend-recipient")
        matches = []
        for name in names:
            number = decimal(name)
            try:
                if os.readlink(name, dir_fd=directory) == target:
                    matches.append(number)
            except FileNotFoundError:
                pass
        if not matches:
            return None
        return min(matches), target
    finally:
        os.close(directory)


class ExecutionBinding:
    """Authenticated at startup; direct checks do not require the observer socket."""

    def __init__(self, backend_dir, config, capture_kind="live"):
        self._backend = self._launcher = None
        self._backend_dir = Path(backend_dir).absolute()
        self._terminal_dirfd = self._terminal_target = None
        self.config = copy.deepcopy(config)
        self.capture_kind = capture_kind
        _require(capture_kind in ("live", "fixture"), "backend-capture")
        self.initial_proof = attest(backend_dir, config)
        _require(self.initial_proof["capture_kind"] == capture_kind, "backend-capture")
        self._descriptor = copy.deepcopy(self.initial_proof["descriptor"])
        self._open()

    @classmethod
    def from_descriptor(cls, descriptor, config, capture_kind="live"):
        """Worker receives its supervisor's pinned descriptor over private stdin."""
        instance = cls.__new__(cls)
        instance._backend = instance._launcher = None
        instance._backend_dir = None
        instance._terminal_dirfd = instance._terminal_target = None
        instance.config = copy.deepcopy(config)
        instance.capture_kind = capture_kind
        instance.initial_proof = None
        instance._descriptor = copy.deepcopy(descriptor)
        _require(capture_kind in ("live", "fixture"), "backend-capture")
        instance._open()
        return instance

    @property
    def descriptor(self):
        return copy.deepcopy(self._descriptor)

    def _open(self):
        try:
            descriptor = validate_descriptor(self._descriptor, self.config)
            self._backend = ProcessReader(descriptor["process_id"], descriptor["process_start_ticks"], descriptor["host_boot_id"])
            self._launcher = ProcessReader(descriptor["launcher_process_id"], descriptor["launcher_start_ticks"], descriptor["host_boot_id"])
            self.check()
        except BaseException:
            self.close()
            raise

    def _participants(self):
        _require(self._backend is not None and self._launcher is not None)
        backend, launcher = self._backend.sample(), self._launcher.sample()
        _require(parse_stat(backend["raw_stat"])["parent_pid"] == launcher["process_id"])
        _require(backend["uid"] == launcher["uid"] == os.getuid())
        return backend, launcher

    def check(self):
        """Revalidate the exact process and listener; never discover replacements."""
        began = time.monotonic_ns()
        try:
            backend, launcher = self._participants()
            proof = listener_proof(self._backend, self.config)
            _require(proof["listener_inode"] == self._descriptor["listener_inode"])
            self._participants()
            return {"read_start_ns": began, "read_end_ns": time.monotonic_ns(), "backend": backend,
                    "launcher": launcher, "listener_proof": {key: proof[key] for key in
                        ("raw_tcp_line", "fd_target", "fd_number")}}
        except OSError as exc:
            raise ResourceError("backend-changed") from exc

    def connected(self, connection):
        """Verify this connected TCP socket's receiver before any HTTP bytes."""
        began = time.monotonic_ns()
        local, remote = _address(connection.getsockname()), _address(connection.getpeername())
        from urllib.parse import urlsplit
        endpoint = urlsplit(self.config["endpoint"])
        _require(local["address"] == "127.0.0.1" and remote == {"address": "127.0.0.1", "port": endpoint.port},
                 "backend-recipient")
        client_fd = connection.fileno()
        client_inode = os.fstat(client_fd).st_ino
        client_target = f"socket:[{client_inode}]"
        _require(os.readlink(f"/proc/self/fd/{client_fd}") == client_target, "backend-recipient")
        deadline = time.monotonic() + ACCEPT_SECONDS
        with ProcessReader(os.getpid()) as client_reader:
            while True:
                self._participants()
                raw = read_bounded("/proc/net/tcp", TCP_LIMIT)
                server_row = _connected_row(raw, remote, local, os.getuid())
                client_row = _connected_row(raw, local, remote, os.getuid(), client_inode)
                owned = _owned_fd(self._backend, server_row[0]) if server_row is not None else None
                if client_row is not None and server_row is not None and owned is not None:
                    break
                _require(time.monotonic() < deadline, "backend-recipient")
                time.sleep(0.01)
            backend, launcher = self._participants()
            client = client_reader.sample()
            _require(_owned_fd(self._backend, server_row[0]) == owned
                     and os.readlink(f"/proc/self/fd/{client_fd}") == client_target, "backend-recipient")
            return {"read_start_ns": began, "read_end_ns": time.monotonic_ns(), "client_address": local,
                "server_address": remote, "raw_client_tcp_line": client_row[1], "raw_server_tcp_line": server_row[1],
                "client_fd_number": client_fd, "client_fd_target": client_target,
                "server_fd_number": owned[0], "server_fd_target": owned[1],
                "client": client, "backend": backend, "launcher": launcher}

    def bind_terminal(self, expected):
        """Pin an authenticated admission target without acquiring stop authority.

Only a path-backed daemon binding can do this. The descriptor-only inference
worker never adopts a backend state directory or another process lifetime.
"""
        from aios_backend import IDENTITY_KEYS
        from aios_backend import protocol as backend_protocol
        from aios_backend.client import registry_at
        from aios_service.lifecycle import prepare_directory
        from .inference import encoded
        _require(self._backend_dir is not None, 'backend-terminal-unsupported')
        _require(self._terminal_target is None, 'backend-terminal-already-bound')
        held = None
        try:
            expected = copy.deepcopy(expected)
            backend_protocol.validate_reply(expected, 'status')
            _require(expected['outcome'] == 'OK' and expected['state'] == 'RUNNING'
                     and expected['capture_kind'] == self.capture_kind
                     and expected['descriptor'] == self._descriptor, 'backend-terminal-target')
            record = expected['service_record']
            self.check()
            _require(self._backend.identity == record['child_identity']
                     and self._launcher.identity == record['supervisor_identity'], 'backend-terminal-target')
            directory = prepare_directory(self._backend_dir, control_root=True)
            held = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stamp = _stamp(os.fstat(held))
            _require(_stamp(directory.lstat()) == stamp, 'backend-terminal-directory')
            registry = registry_at(directory)
            _require(registry is not None and all(registry[key] == record[key] for key in IDENTITY_KEYS),
                     'backend-terminal-registry')
            registry_file = _terminal_file(directory / 'registry.json')
            registry_stamp = _stamp((directory / 'registry.json').lstat())
            connection, authenticated, peer = backend_protocol.connect(directory, registry)
            try:
                _require(encoded(authenticated) == encoded(expected)
                         and peer == record['supervisor_identity']['process_id'], 'backend-terminal-target')
            finally:
                connection.close()
            run = prepare_directory(directory / 'runs' / registry['instance_id'])
            _require(not (run / 'result.json').exists(), 'backend-terminal-target')
            fixed_files = {name: _terminal_file(run / name) for name in ('start.json', 'config.json')}
            _require(fixed_files['config.json']['sha256'] == record['config_sha256']
                     and hashlib.sha256(encoded(self.config) + b'\n').hexdigest() == record['config_sha256'],
                     'backend-terminal-config')
            self.check()
            _require(_stamp(directory.lstat()) == stamp and registry_at(directory) == registry
                     and _terminal_file(directory / 'registry.json') == registry_file
                     and _stamp((directory / 'registry.json').lstat()) == registry_stamp,
                     'backend-terminal-registry')
            self._terminal_target = {'expected': expected, 'registry': registry,
                'registry_file': registry_file, 'registry_stamp': registry_stamp,
                'state_directory': stamp, 'run_directory': _stamp(run.lstat()), 'fixed_files': fixed_files,
                'backend_reader': self._backend, 'launcher_reader': self._launcher,
                'backend_pidfd': self._backend._pidfd, 'launcher_pidfd': self._launcher._pidfd}
            self._terminal_dirfd, held = held, None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ResourceError):
                raise
            raise ResourceError('backend-terminal-target') from exc
        finally:
            if held is not None:
                os.close(held)

    def _terminal_lifetimes(self):
        target = self._terminal_target
        _require(target is not None and self._terminal_dirfd is not None, 'backend-terminal-unbound')
        observations = {}
        for name, reader, identity in (
                ('backend', self._backend, target['expected']['service_record']['child_identity']),
                ('launcher', self._launcher, target['expected']['service_record']['supervisor_identity'])):
            _require(reader is not None and reader is target[name + '_reader']
                     and reader.identity == identity and reader._pidfd is not None
                     and reader._pidfd == target[name + '_pidfd'] and reader._dirfd is not None,
                     'backend-terminal-lifetime')
            _require(select.select([reader._pidfd], [], [], 0)[0] == [reader._pidfd],
                     'backend-terminal-live')
            observations[name] = {'identity': copy.deepcopy(identity), 'exited': True,
                                  'observed_monotonic_ns': time.monotonic_ns()}
        return observations

    def verify_stopped(self):
        """Observe both retained deaths and this target's normal terminal files.

No IPC stop, PID adoption, waiting, model-success inference or caller-supplied
STOPPED reply is involved. A later state-directory/registry replacement fails.
"""
        from aios_backend import protocol as backend_protocol
        from aios_backend.client import _terminal, registry_at
        from aios_service.lifecycle import prepare_directory, strict_json
        _require(self._backend_dir is not None, 'backend-terminal-unsupported')
        began = time.monotonic_ns()
        try:
            lifetimes = self._terminal_lifetimes()
            target = self._terminal_target
            directory = prepare_directory(self._backend_dir, control_root=True)
            _require(_stamp(os.fstat(self._terminal_dirfd)) == target['state_directory']
                     and _stamp(directory.lstat()) == target['state_directory'], 'backend-terminal-directory')
            registry = registry_at(directory)
            _require(registry == target['registry']
                     and _stamp((directory / 'registry.json').lstat()) == target['registry_stamp']
                     and _terminal_file(directory / 'registry.json') == target['registry_file'],
                     'backend-terminal-registry')
            # Hash exactly the bytes parsed here, before any later file read.
            latest_bytes = _terminal_bytes(directory / 'latest.json')
            latest = backend_protocol.validate_reply(strict_json(latest_bytes), 'status')
            expected_record = target['expected']['service_record']
            terminal_record = {**expected_record, 'lifecycle_state': 'exited', 'backend_ready': False}
            _require(latest['state'] == 'STOPPED' and latest['outcome'] == 'OK'
                     and latest['error'] is None and latest['descriptor'] is None
                     and latest['capture_kind'] == self.capture_kind
                     and latest['service_record'] == terminal_record, 'backend-terminal-result')
            run = prepare_directory(directory / 'runs' / registry['instance_id'])
            _require(_stamp(run.lstat()) == target['run_directory'], 'backend-terminal-directory')
            before = {'registry.json': _terminal_file(directory / 'registry.json'),
                      'latest.json': {'sha256': hashlib.sha256(latest_bytes).hexdigest(), 'bytes': len(latest_bytes)}}
            before['result.json'] = _terminal_file(run / 'result.json')
            result = _terminal(directory, registry, latest)
            _require(result['descriptor'] == target['expected']['descriptor'], 'backend-terminal-result')
            artifacts = {name: _terminal_file(run / name) for name in result['files']}
            _require(all(artifacts[name]['sha256'] == digest for name, digest in result['files'].items())
                     and all(artifacts[name] == value for name, value in target['fixed_files'].items()),
                     'backend-terminal-artifact')
            after = {name: _terminal_file(directory / name) for name in ('registry.json', 'latest.json')}
            after['result.json'] = _terminal_file(run / 'result.json')
            _require(before == after and registry_at(directory) == target['registry']
                     and _stamp((directory / 'registry.json').lstat()) == target['registry_stamp']
                     and _stamp(directory.lstat()) == target['state_directory']
                     and _stamp(run.lstat()) == target['run_directory'], 'backend-terminal-artifact')
            self._terminal_lifetimes()
            stopped = {**latest, 'action': 'stop'}
            backend_protocol.validate_reply(stopped, 'stop')
            return {'backend_stop': copy.deepcopy(stopped), 'evidence': {
                'schema_version': 1, 'expected': copy.deepcopy(target['expected']),
                'read_start_ns': began, 'read_end_ns': time.monotonic_ns(),
                'lifetimes': lifetimes, 'state_directory': copy.deepcopy(target['state_directory']),
                'registry': copy.deepcopy(registry), 'terminal_files': before, 'artifacts': artifacts}}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ResourceError):
                raise
            raise ResourceError('backend-terminal-result') from exc

    def close(self):
        for name in ("_backend", "_launcher"):
            reader = getattr(self, name, None)
            if reader is not None:
                reader.close()
                setattr(self, name, None)
        descriptor = getattr(self, '_terminal_dirfd', None)
        if descriptor is not None:
            os.close(descriptor)
            self._terminal_dirfd = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
