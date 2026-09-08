"""Opt-in observation beside MAIN; failures never change producer readiness.

The MAIN daemon serializes this object under its existing private lock. Only
the explicit link command changes the management-owned relation. Measurements
are copied reads and never ownership or policy enforcement.
"""
from __future__ import annotations

import copy
import os
import re
import time
from contextlib import ExitStack
from pathlib import Path

from aios_agent.inference import encoded
from aios_management.resources import build_observation, is_current, link, validate_relation
from aios_service.lifecycle import atomic_json, prepare_directory, read_json
from .backend import attest
from .proc import ProcessReader, ResourceError, pressure_sample

MAX_OBSERVATION = 128 * 1024


def error_code(exc):
    code = getattr(exc, 'code', str(exc))
    return code if type(code) is str and re.fullmatch(r'[a-z]+(?:-[a-z]+)*', code) and len(code) <= 64 else 'observation-failed'


class ResourceManager:
    def __init__(self, directory: Path, config: dict, capture_kind: str):
        self.path = directory / 'resource-state.json'
        self.config = copy.deepcopy(config)
        self.capture_kind = capture_kind
        self.relation = self.backend_dir = self.last = None
        self.load_error = None
        try:
            value = read_json(self.path)
            if (set(value) != {'schema_version', 'relation', 'backend_dir'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1
                    or type(value['backend_dir']) is not str or not 0 < len(value['backend_dir']) < 96
                    or not Path(value['backend_dir']).is_absolute()):
                raise ValueError('resource-state-corrupt')
            validate_relation(value['relation'])
            self.relation, self.backend_dir = value['relation'], Path(value['backend_dir'])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.load_error = error_code(exc)

    def result(self, action, *, error=None, current=False, observation=None):
        return {'schema_version': 1, 'action': action, 'outcome': 'ERROR' if error else 'OK',
                'error': error, 'relation': copy.deepcopy(self.relation), 'relation_current': current,
                'observation': observation, 'observation_only': True, 'ownership_valid': False,
                'resource_actions': 'UNSUPPORTED', 'capture_kind': self.capture_kind}

    def _proof(self):
        proof = attest(self.backend_dir, self.config)
        if proof['capture_kind'] != self.capture_kind:
            raise ValueError('capture-mismatch')
        return proof

    def _check(self, source, snapshot, reader, proof):
        if not is_current(self.relation, snapshot, source, reader.identity, proof, self.config):
            raise ValueError('resource-relation-stale')

    def execute(self, action, source, snapshot, backend_dir=None):
        try:
            if self.load_error:
                raise ValueError(self.load_error)
            if action == 'link':
                if (type(backend_dir) is not str or not 0 < len(backend_dir) < 96
                        or not Path(backend_dir).is_absolute()):
                    raise ValueError('backend-directory-required')
                directory = prepare_directory(Path(backend_dir))
                proof = attest(directory, self.config)
                if proof['capture_kind'] != self.capture_kind:
                    raise ValueError('capture-mismatch')
                with ProcessReader(os.getpid(), expected_boot_id=source['host_boot_id']) as main:
                    if self.relation is not None and is_current(self.relation, snapshot, source, main.identity, proof, self.config):
                        return self.result(action, current=True)
                    relation = link(snapshot, source, main.identity, proof, self.config, previous=self.relation)
                atomic_json(self.path, {'schema_version': 1, 'relation': relation, 'backend_dir': str(directory)})
                self.relation, self.backend_dir, self.last = relation, directory, None
                return self.result(action, current=True)
            if self.relation is None:
                return self.result(action, error='resource-unlinked')
            if action == 'status':
                with ProcessReader(os.getpid(), expected_boot_id=source['host_boot_id']) as main:
                    self._check(source, snapshot, main, self._proof())
                # This is explicitly a last observation; no fresh metric claim.
                return self.result(action, current=True, observation=copy.deepcopy(self.last))
            if action != 'sample':
                raise ValueError('resource-action')
            window = self.begin(source, snapshot)
            if 'stack' not in window:
                return {**window, 'action': action}
            time.sleep(0.1)
            return self.finish(window, source, snapshot, kind='sample')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.last = None
            return self.result(action, error=error_code(exc))

    def begin(self, source, snapshot):
        """Hold both identities across the real request, or return an error."""
        if self.relation is None and self.load_error is None:
            return None
        stack = ExitStack()
        try:
            if self.load_error:
                raise ValueError(self.load_error)
            proof = self._proof()
            main = stack.enter_context(ProcessReader(os.getpid(), expected_boot_id=source['host_boot_id']))
            self._check(source, snapshot, main, proof)
            descriptor = proof['descriptor']
            backend = stack.enter_context(ProcessReader(descriptor['process_id'],
                expected_start_ticks=descriptor['process_start_ticks'], expected_boot_id=descriptor['host_boot_id']))
            frame = {'main': main.sample(), 'backend': backend.sample(), 'pressure': pressure_sample(), 'backend_proof': proof}
            return {'stack': stack, 'main': main, 'backend': backend, 'before': frame,
                    'source': copy.deepcopy(source), 'snapshot': copy.deepcopy(snapshot),
                    'relation': copy.deepcopy(self.relation)}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            stack.close()
            self.last = None
            return self.result('request', error=error_code(exc))

    def finish(self, window, source, snapshot, *, kind='request', request_id=None):
        if window is None:
            return None
        if 'stack' not in window:
            return {**window, 'action': kind}
        try:
            proof = self._proof()
            self._check(source, snapshot, window['main'], proof)
            frame = {'main': window['main'].sample(), 'backend': window['backend'].sample(),
                     'pressure': pressure_sample(), 'backend_proof': proof}
            observation = build_observation(kind=kind, request_id=request_id,
                relation_before=window['relation'], relation_after=self.relation,
                before=window['before'], after=frame, source_before=window['source'], source_after=source,
                management_before=window['snapshot'], management_after=snapshot, config=self.config)
            if len(encoded(observation)) > MAX_OBSERVATION:
                raise ValueError('observation-size')
            self.last = copy.deepcopy(observation)
            return self.result(kind, current=True, observation=observation)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.last = None
            return self.result(kind, error=error_code(exc))
        finally:
            window['stack'].close()
