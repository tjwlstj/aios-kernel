"""Installed-image fixture replay and semantic mutations, never live evidence."""
from __future__ import annotations

import ast
import base64
import copy
import io
import json
import platform
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(ROOT / 'tools/hosted'))
from aios_console import shell
from test_hosted_console import agent, backend, dns, fetch, service
import test_hosted_service_verifier as service_fixtures
import verify_image as verifier


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def get(path):
    return json.loads(path.read_bytes())


class ImageVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aios-image-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.disk = self.root / 'system.raw'
        self.disk.write_bytes(b'explicit fixture disk bytes')
        self.runtime = self.root / 'runtime-source'
        for name in verifier.IMAGE_SOURCES:
            source = ROOT / 'hosted/linux' / name
            target = self.runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes() if source.exists() else b'# explicit image source fixture\n')
        hashes = {name: verifier.digest((self.runtime / name).read_bytes()) for name in sorted(verifier.IMAGE_SOURCES)}
        self.config = {'schema_version': 1, 'profile': 'basic', 'model_config': None}
        install = self.root / 'installation-files'
        install.mkdir()
        (install / 'packages.txt').write_bytes(b'python3-3.14.2-r0\nlinux-virt-6.18.1-r0\nca-certificates-20260101-r0\nopenrc-0.61-r0\nsyslinux-6.04-r0\n')
        (install / 'kernel.txt').write_bytes((platform.release() + '\n').encode())
        for name in verifier.INSTALLATION_FILES:
            if name.endswith('.sha256'):
                (install / name).write_bytes(b'1' * 64 + b'\n')
        put(install / 'installed-runtime.json', hashes)
        self.manifest = {'schema_version': 1, 'image_id': str(uuid.uuid4()), 'profile': 'basic-cli',
            'substrate': 'linux-hosted', 'iso': dict(verifier.ISO), 'runtime_files': hashes,
            'boot_config_sha256': verifier.digest(encoded(self.config)),
            'installation_files': {path.name: verifier.digest(path.read_bytes()) for path in install.iterdir()},
            'source_only': True, 'repository_import': False, 'redistribution_approved': False}
        put(self.root / 'image-manifest.json', self.manifest)
        self.disk_value = {'schema_version': 1, 'sha256': verifier.digest(self.disk.read_bytes()),
            'size_bytes': self.disk.stat().st_size, 'path': str(self.disk.resolve())}
        put(self.root / 'image-before.json', self.disk_value)
        self.boots, self.previous = [], {}
        self.make_boot(1)
        self.make_boot(2)

    def make_boot(self, index, offline=False, commands=None, use_service=True):
        path = self.root / 'boots' / ('boot-%02d' % index)
        archive = path / 'archive'
        archive.mkdir(parents=True)
        helper = service_fixtures.ServiceVerifierTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        if use_service:
            shutil.copytree(helper.state, archive / 'service')
        terminal = get(helper.state / 'latest.json')
        boot_id = helper.boot_id
        running = {**terminal, 'state': 'RUNNING', 'observation_sequence': 1, 'heartbeat_monotonic_ns': 200}
        responses = iter([service('ABSENT'), running, running])
        commands = commands if commands is not None else verifier.OFFLINE_COMMANDS if offline else verifier.ONLINE_COMMANDS
        code = shell.run_console(archive / 'session', input_stream=io.StringIO('\n'.join(commands) + '\n'),
            output_stream=io.StringIO(), proc_root=helper.proc, sys_root=helper.sysfs, test_system='Linux',
            resolver=dns, fetcher=fetch, service_control=lambda _directory, _action: next(responses),
            agent_control=lambda _directory, action, **_kwargs: agent(action, state='ABSENT', error='process-not-running', protocol=4))
        self.assertEqual(code, 0)
        session_id = get(archive / 'session/session-result.json')['session_id']
        boot = {'schema_version': 1, 'boot_id': boot_id, 'image_id': self.manifest['image_id'], 'profile': 'basic',
            'substrate': 'linux-hosted', 'source_only': True, 'canonical_continuity': False,
            'config_sha256': self.manifest['boot_config_sha256'], 'image_manifest_sha256': verifier.digest(encoded(self.manifest)),
            'runtime_files': self.manifest['runtime_files'], 'installation_files': self.manifest['installation_files'],
            'previous_boots': copy.deepcopy(self.previous), 'live_root': '/run/aios/boots/' + boot_id,
            'history_root': '/var/lib/aios/history/' + boot_id, 'worker_uid': 1000, 'worker_gid': 1000,
            'root_uid': 0, 'root_euid': 0, 'started_monotonic_ns': 100}
        put(archive / 'boot.json', boot)
        put(archive / 'boot-config.json', self.config)
        put(archive / 'image.json', self.manifest)
        cleanup = [{'service': name, 'action': 'stop', 'outcome': 'OK', 'error': None, 'response': response}
            for name, response in [('MAIN', agent('stop', state='ABSENT', protocol=4)),
                ('MODEL_BACKEND', backend('stop', state='ABSENT')), ('CONSOLE_RUNTIME', terminal if use_service else service('ABSENT'))]]
        worker = {'schema_version': 1, 'boot_id': boot_id, 'uid': 1000, 'euid': 1000, 'gid': 1000, 'egid': 1000,
            'session_id': session_id, 'session_exit_code': 0, 'error': None, 'cleanup': cleanup,
            'cleanup_ok': True, 'completed_monotonic_ns': 1000}
        put(archive / 'worker-result.json', worker)
        result = {'schema_version': 1, 'boot_id': boot_id, 'image_id': self.manifest['image_id'], 'profile': 'basic',
            'source_only': True, 'config_sha256': self.manifest['boot_config_sha256'], 'worker_process_id': 321,
            'worker_exit_code': 0, 'worker_exit_verified': True, 'worker_result_sha256': verifier.digest(encoded(worker)),
            'session_id': session_id, 'session_exit_code': 0, 'cleanup_ok': True, 'outcome': 'PASS', 'error': None,
            'archive_complete': True, 'poweroff_intent': 'fixed-root-poweroff', 'completed_monotonic_ns': 1100}
        put(archive / 'root-result.json', result)
        put(archive / 'poweroff.json', {'schema_version': 1, 'boot_id': boot_id, 'command': ['/sbin/poweroff'],
            'root_uid': 0, 'root_euid': 0, 'source_only': True})
        self.seal(path)
        network = 'offline' if offline else 'online'
        put(path / 'launch.json', {'schema_version': 1, 'qemu_argv': ['/usr/bin/qemu-system-x86_64',
            '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max', '-m', '768', '-smp', '2', '-display', 'none',
            '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-boot', 'c', '-nic', 'none' if offline else 'user,model=e1000',
            '-device', 'qemu-xhci', '-device', 'usb-kbd', '-drive', 'file=' + self.disk.as_posix() + ',format=raw,if=virtio'],
            'stdin_commands': commands, 'network': network, 'firmware': 'bios', 'boot_method': 'disk'})
        for name in ('disk-before.json', 'disk-after.json'):
            put(path / name, self.disk_value)
        put(path / 'vm-result.json', {'schema_version': 1, 'process_exit_code': 0, 'host_killed': False,
            'shutdown_observed': True, 'runner_error': None})
        self.previous[boot_id] = {'archive_manifest_sha256': verifier.digest((archive / 'archive-manifest.json').read_bytes()),
            'root_result_sha256': verifier.digest((archive / 'root-result.json').read_bytes())}
        self.boots.append(path)
        return path

    def seal(self, path):
        archive = path / 'archive'
        files = {p.relative_to(archive).as_posix(): p.read_bytes() for p in archive.rglob('*') if p.is_file() and p.name != 'archive-manifest.json'}
        boot = get(archive / 'boot.json')
        metadata = {'schema_version': 1, 'boot_id': boot['boot_id'], 'file_count': len(files),
            'total_bytes': sum(map(len, files.values())), 'files': {name: {'bytes': len(raw), 'sha256': verifier.digest(raw)} for name, raw in files.items()}}
        put(archive / 'archive-manifest.json', metadata)
        files['archive-manifest.json'] = encoded(metadata)
        header = {'schema_version': 1, 'boot_id': boot['boot_id'], 'encoding': 'json-files-base64',
            'file_count': len(files), 'total_bytes': sum(map(len, files.values())), 'manifest_sha256': verifier.digest(encoded(metadata))}
        raw = b'Linux boot fixture\nAIOS_IMAGE_BOOT=' + encoded(boot)
        raw += b'AIOS_IMAGE_SESSION_BEGIN=' + encoded({'schema_version': 1, 'boot_id': boot['boot_id']})
        raw += files['session/console.log']
        raw += b'AIOS_IMAGE_SESSION_END=' + encoded({'schema_version': 1, 'boot_id': boot['boot_id'], 'worker_exit_code': 0})
        raw += b'AIOS_IMAGE_RESULT=' + files['root-result.json']
        raw += b'AIOS_IMAGE_EXPORT_BEGIN=' + encoded(header)
        for name, value in sorted(files.items()):
            raw += b'AIOS_IMAGE_EXPORT_FILE=' + encoded({'path': name, 'bytes': len(value), 'sha256': verifier.digest(value),
                'data_base64': base64.b64encode(value).decode()})
        raw += b'AIOS_IMAGE_EXPORT_END=' + encoded(header)
        raw += b'AIOS_IMAGE_POWEROFF=' + files['poweroff.json'] + b'[ 2.100000] reboot: Power down\n'
        (path / 'serial.log').write_bytes(raw)

    def verdict(self):
        return verifier.verify_image(self.root, require_live=False)

    def assert_failed(self, reason=None):
        value = self.verdict()
        self.assertEqual(value['outcome'], 'FAIL', value)
        if reason:
            self.assertIn(reason, str(value['reasons']))

    def test_two_cold_boots_replay_and_fixture_is_never_live(self):
        value = self.verdict()
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertEqual(value['cold_boots'], 2)
        self.assertFalse(value['model_bundled'])
        self.assertEqual(verifier.verify_image(self.root)['outcome'], 'FAIL')

    def test_optional_offline_third_boot_keeps_cli_available(self):
        self.make_boot(3, offline=True)
        value = self.verdict()
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertTrue(value['offline_boot_verified'])

    def test_generic_operating_boot_allows_help_exit_and_absent_service(self):
        path = self.make_boot(3, commands=['help', 'about', 'exit'], use_service=False)
        value = verifier.verify_operating_boot(self.root, path, require_live=False)
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertEqual(value['service_runs'], 0)
        self.assertFalse(value['prior_history_replayed'])
        self.assertEqual(verifier.verify_operating_boot(self.root, path)['outcome'], 'FAIL')

    def test_generic_operating_boot_replays_service_cleanup_without_fixed_smoke_requirement(self):
        value = verifier.verify_operating_boot(self.root, self.boots[1], require_live=False)
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertEqual(value['service_runs'], 1)

    def test_iso_pin_and_required_installed_packages_cannot_be_rehashed_away(self):
        path = self.root / 'image-manifest.json'
        changed = copy.deepcopy(self.manifest)
        changed['iso']['sha256'] = '1' * 64
        put(path, changed)
        self.assert_failed('iso_pin')
        changed = copy.deepcopy(self.manifest)
        packages = self.root / 'installation-files/packages.txt'
        packages.write_bytes(b'python3-3.14.2-r0\n')
        changed['installation_files']['packages.txt'] = verifier.digest(packages.read_bytes())
        put(path, changed)
        self.assert_failed('installed_packages')

    def test_rehashed_prior_boot_digest_drift_rejects(self):
        path = self.boots[1]
        boot = get(path / 'archive/boot.json')
        next(iter(boot['previous_boots'].values()))['root_result_sha256'] = 'f' * 64
        put(path / 'archive/boot.json', boot)
        self.seal(path)
        self.assert_failed('prior_boot_evidence')

    def test_rehashed_config_drift_rejects(self):
        path = self.boots[1]
        put(path / 'archive/boot-config.json', {**self.config, 'model_config': '/tmp/model.json'})
        self.seal(path)
        self.assert_failed('basic_no_model')

    def test_rehashed_privileged_worker_rejects(self):
        path = self.boots[1]
        worker = get(path / 'archive/worker-result.json')
        worker['euid'] = 0
        put(path / 'archive/worker-result.json', worker)
        result = get(path / 'archive/root-result.json')
        result['worker_result_sha256'] = verifier.digest(encoded(worker))
        put(path / 'archive/root-result.json', result)
        self.seal(path)
        self.assert_failed('worker_privilege')

    def test_host_injected_setup_and_external_kernel_are_rejected(self):
        path = self.boots[1] / 'launch.json'
        original = get(path)
        for edit in (lambda v: v['stdin_commands'].insert(0, 'root'),
                     lambda v: v['qemu_argv'].extend(['-kernel', '/tmp/kernel']),
                     lambda v: v['qemu_argv'].extend(['-virtfs', 'local,path=/host,mount_tag=src,security_model=none'])):
            value = copy.deepcopy(original)
            edit(value)
            put(path, value)
            self.assert_failed()
        put(path, original)

    def test_missing_duplicate_quoted_and_trailing_powerdown_records_reject(self):
        path = self.boots[1] / 'serial.log'
        original = path.read_bytes()
        for changed in (original.replace(b'AIOS_IMAGE_RESULT=', b'"AIOS_IMAGE_RESULT=', 1),
                        original.replace(b'AIOS_IMAGE_EXPORT_END=', b'AIOS_IMAGE_EXPORT_MISSING=', 1),
                        original + b'late payload\n', original.replace(b'AIOS_IMAGE_POWEROFF=', b'Traceback (most recent call last)\nAIOS_IMAGE_POWEROFF=', 1)):
            path.write_bytes(changed)
            self.assert_failed()
        path.write_bytes(original)

    def test_disk_hash_chain_and_actual_terminal_disk_are_verified(self):
        self.disk.write_bytes(b'changed disk after evidence')
        self.assert_failed('terminal_disk_hash')

    def test_rehashed_forced_poweroff_cannot_pass_normal_shutdown(self):
        path = self.boots[1]
        poweroff = get(path / 'archive/poweroff.json')
        poweroff['command'] = ['/sbin/poweroff', '-f']
        put(path / 'archive/poweroff.json', poweroff)
        self.seal(path)
        self.assert_failed('poweroff_command')

    def test_rehashed_service_from_another_boot_rejects(self):
        path = self.boots[1]
        boot = get(path / 'archive/boot.json')
        boot['boot_id'] = str(uuid.uuid4())
        put(path / 'archive/boot.json', boot)
        self.seal(path)
        self.assert_failed()

    def test_vm_timeout_or_boolean_exit_cannot_be_clean(self):
        path = self.boots[1] / 'vm-result.json'
        original = get(path)
        for changes in ({'host_killed': True}, {'process_exit_code': False}, {'shutdown_observed': False}, {'runner_error': 'timeout'}):
            put(path, {**original, **changes})
            self.assert_failed('vm_exit')
        put(path, original)

    def test_checker_has_no_product_runtime_import(self):
        tree = ast.parse((ROOT / 'tools/hosted/verify_image.py').read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith('aios_') for name in imports))


class ModelImageVerifierTests(unittest.TestCase):
    """Complete copied model records remain explicit fixtures, including TCP proofs."""
    def setUp(self):
        import test_hosted_agent_verifier as agents
        self.base = ImageVerifierTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root, self.runtime = self.base.root, self.base.runtime
        model = agents.AgentModelVerifierTests()
        model.setUp()
        self.addCleanup(model.doCleanups)
        self.config = {**model.config, 'model_path': verifier.MODEL_DIRECTORY + 'Qwen3-0.6B-Q8_0.gguf',
                       'backend_path': verifier.MODEL_DIRECTORY + 'llamafile-0.10.5-thin.exe'}
        install = self.root / 'installation-files'
        put(install / 'model-config.json', self.config)
        shutil.copy2(model.root / 'inference-provenance.json', install / 'inference-provenance.json')
        shutil.copytree(model.root / 'provenance', install / 'provenance')
        self.integrity = {'schema_version': 1, 'verification': 'installed-read-complete', 'files': {
            name: {'path': verifier.MODEL_DIRECTORY + name, **value, 'uid': 0, 'gid': 0, 'mode': 292}
            for name, value in verifier.MODEL_FILES.items()}}
        put(install / 'model-integrity.json', self.integrity)
        self.boot_config = {'schema_version': 2, 'profile': 'local-model', 'model_config': verifier.MODEL_CONFIG_PATH}
        self.manifest = copy.deepcopy(self.base.manifest)
        self.manifest.update(schema_version=2, profile='local-model-cli',
            boot_config_sha256=verifier.digest(encoded(self.boot_config)), model_bundle={'schema_version': 1,
            'config_sha256': verifier.digest(encoded(self.config)), 'provenance_sha256': self.config['provenance_sha256'],
            'files': copy.deepcopy(verifier.MODEL_FILES)}, installation_files={
                p.relative_to(install).as_posix(): verifier.digest(p.read_bytes()) for p in install.rglob('*') if p.is_file()})
        put(self.root / 'image-manifest.json', self.manifest)
        self.previous = {}
        for index, path in enumerate(self.base.boots):
            self.convert_boot(path, offline=bool(index))

    def convert_boot(self, path, *, offline):
        from aios_management.binding import Authority
        from aios_management.resources import link, build_observation, is_current
        from aios_agent.inference import request_body
        from test_hosted_management import source
        from test_hosted_backend_execution import execution_fixture
        from test_hosted_resources_management import proof_fixture, sample_fixture, pressure_fixture
        from verify_agent import MANAGED_SOURCES
        from verify_backend import SOURCES as BACKEND_SOURCES
        a = path / 'archive'
        boot = get(a / 'boot.json')
        boot_id = boot['boot_id']
        boot.update(schema_version=2, profile='local-model', config_sha256=self.manifest['boot_config_sha256'],
            image_manifest_sha256=verifier.digest(encoded(self.manifest)), installation_files=self.manifest['installation_files'],
            previous_boots=copy.deepcopy(self.previous), model_bundle=self.manifest['model_bundle'],
            model_integrity={**self.integrity, 'boot_id': boot_id, 'verification': 'boot-read-complete'})
        for name in self.manifest['installation_files'].keys() - verifier.INSTALLATION_FILES:
            target = a / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / 'installation-files' / name, target)
        put(a / 'boot.json', boot)
        put(a / 'image.json', self.manifest)
        put(a / 'boot-config.json', self.boot_config)
        proof = proof_fixture(at=350)
        descriptor = proof['descriptor']
        descriptor.update(host_boot_id=boot_id, source_instance=str(uuid.uuid4()),
            **{k: self.config[k] for k in ('endpoint', 'model_id', 'model_sha256', 'backend_sha256')})
        proofs = [copy.deepcopy(proof)]
        authority, events, requests, resource_files = Authority(), [], {}, {}
        authority.initialize()
        main = {'host_boot_id': boot_id, 'process_id': 123, 'process_start_ticks': 111, 'uid': 1000}
        producer = source(source_id=str(uuid.uuid4()), source_instance=str(uuid.uuid4()), host_boot_id=boot_id,
                          model_sha256=self.config['model_sha256'])
        relation = None
        started = False

        def execution(offset=0):
            value = execution_fixture()
            value['descriptor'] = copy.deepcopy(descriptor)
            def walk(item):
                if isinstance(item, dict):
                    if 'host_boot_id' in item: item['host_boot_id'] = boot_id
                    for key in ('read_start_ns', 'read_end_ns'):
                        if key in item: item[key] += offset
                    for child in item.values(): walk(child)
                elif isinstance(item, list):
                    for child in item: walk(child)
            walk(value)
            client = value['send']['client']
            head, tail = client['raw_stat'].rsplit(') ', 1)
            fields = tail.split(); fields[1] = '123'
            client['raw_stat'] = head + ') ' + ' '.join(fields) + '\n'
            return value

        def receipt(prompt, warmup=False):
            body = request_body(prompt, warmup=warmup).decode()
            response = json.dumps({'model': self.config['model_id'], 'content': 'Explicit model fixture.', 'tokens_predicted': 4})
            return {'schema_version': 2, 'request_id': str(uuid.uuid4()), 'started_at': '2026-09-08T00:00:00+00:00',
                'purpose': 'warmup' if warmup else 'user', **{k: self.config[k] for k in
                    ('model_id', 'model_sha256', 'backend_sha256', 'provenance_sha256')}, 'request_body': body,
                'request_sha256': verifier.digest(body.encode()), 'response_body': response,
                'response_sha256': verifier.digest(response.encode()), 'content': 'Explicit model fixture.',
                'tokens_predicted': 4, 'elapsed_ns': 3000, 'outcome': 'OK', 'error': None,
                'backend_execution': execution(0 if warmup else 100000)}
        warmup = receipt('Say hello.', True)
        producer.update(warmup_request_sha256=warmup['request_sha256'], warmup_response_sha256=warmup['response_sha256'])

        def event(name, action=None, error=None, item=None, resource=None):
            item_path = 'warmup.json' if name == 'RUNNING' else 'requests/' + item['request_id'] + '.json' if item else None
            resource_path = ('resources/' + (item['request_id'] if item else str(uuid.uuid4())) + '.json') if resource else None
            if resource: resource_files[resource_path] = copy.deepcopy(resource)
            if item and name != 'RUNNING': requests[item_path] = copy.deepcopy(item)
            events.append({'schema_version': 4, 'source_instance': producer['source_instance'], 'sequence': len(events) + 1,
                'monotonic_ns': 10000 * (len(events) + 1), 'event': name, 'action': action,
                'outcome': 'ERROR' if error else 'OK', 'error': error,
                'source_record': None if name == 'STARTING' else copy.deepcopy(producer),
                'management_snapshot': authority.snapshot(), 'receipt_file': item_path, 'resource_file': resource_path})

        def resource_value(action, error=None, observation=None):
            return {'schema_version': 1, 'action': action, 'outcome': 'ERROR' if error else 'OK', 'error': error,
                'relation': copy.deepcopy(relation), 'relation_current': error is None, 'observation': observation,
                'observation_only': True, 'ownership_valid': False, 'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture'}

        def frame(at, after=False):
            fresh = copy.deepcopy(proof); fresh.update(nonce=str(uuid.uuid4()), observed_monotonic_ns=at)
            proofs.append(fresh)
            value = {'main': sample_fixture(123, 111, at=at, user=11 if after else 10),
                'backend': sample_fixture(234, 222, at=at, user=260 if after else 100),
                'pressure': pressure_fixture(at=at, total=110 if after else 100), 'backend_proof': fresh}
            value['main']['host_boot_id'] = value['backend']['host_boot_id'] = boot_id
            return value

        def control(_directory, action, **kwargs):
            nonlocal started, relation
            if action == 'status' and not started: return agent('status', state='ABSENT', protocol=4)
            error, item, resource, management = None, None, None, None
            if action == 'start':
                event('STARTING'); event('RUNNING', item=warmup); started = True
            elif action in ('room-status', 'ask'):
                managed = authority.observe(producer)
                management = managed['outcome']; error = None if management == 'accepted' else managed['reason']
                if action == 'ask' and error is None:
                    before_source, before_snapshot = copy.deepcopy(producer), authority.snapshot()
                    before, after = frame(100000), frame(1000100000, True)
                    item = receipt(kwargs['prompt'])
                    item.update(source_before=before_source, authority_instance=authority.authority_instance,
                                binding_generation=authority.snapshot()['binding']['generation'])
                    producer['completed_requests'] += 1
                    authority.observe(producer)
                    item['source_after'] = copy.deepcopy(producer)
                    observation = build_observation(kind='request', request_id=item['request_id'], relation_before=relation,
                        relation_after=relation, before=before, after=after, source_before=before_source, source_after=producer,
                        management_before=before_snapshot, management_after=authority.snapshot(), config=self.config)
                    resource = resource_value('request', observation=observation)
            elif action.startswith('cell-'):
                managed = authority.set_parent(action == 'cell-activate'); management = managed['outcome']
            elif action.startswith('room-'):
                managed = authority.discover([producer]) if action == 'room-discover' else getattr(authority, action[5:])(producer)
                management = managed['outcome']; error = None if management == 'accepted' else managed['reason']
            elif action == 'resources-link':
                relation = link(authority.snapshot(), producer, main, proof, self.config, previous=relation)
                resource = resource_value('link')
            elif action == 'resources-status':
                error = None if is_current(relation, authority.snapshot(), producer, main, proof, self.config) else 'resource-relation-stale'
                resource = resource_value('status', error)
            if action not in ('start', 'status', 'room-status'):
                event('COMMAND', action, error, item, resource)
            return {'schema_version': 4, 'action': action, 'outcome': 'ERROR' if error else 'OK', 'error': error,
                'state': 'RUNNING', 'service_kind': 'AI_SERVICE', 'source_record': copy.deepcopy(producer),
                'management_snapshot': authority.snapshot(), 'management_outcome': management, 'inference_receipt': item,
                'resource_result': resource, 'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture'}

        backend_id = {'schema_version': 1, 'service_id': str(uuid.uuid4()), 'instance_id': str(uuid.uuid4()), 'start_generation': 1}
        child = {'host_boot_id': boot_id, 'process_id': 234, 'process_start_ticks': 222, 'uid': 1000}
        supervisor = {**child, 'process_id': 233, 'process_start_ticks': 221}
        record = {**backend_id, 'supervisor_identity': supervisor, 'child_identity': None, 'lifecycle_state': 'starting',
            'backend_ready': False, 'config_sha256': verifier.digest(encoded(self.config)), 'profile': 'python-fixture',
            'source_only': True, 'backend_source_instance': None}
        backend_events = []
        for index, name in enumerate(('STARTING', 'CHILD_STARTED', 'RUNNING', 'STOPPING', 'STOPPED')):
            if index == 1: record['child_identity'] = child
            if index == 2: record.update(lifecycle_state='active', backend_ready=True, backend_source_instance=descriptor['source_instance'])
            if index == 3: record['backend_ready'] = False
            if index == 4: record['lifecycle_state'] = 'exited'
            backend_events.append({'schema_version': 1, 'instance_id': backend_id['instance_id'], 'sequence': index + 1,
                'monotonic_ns': (100, 200, 300, 2000000000, 2000000001)[index], 'event': name, 'outcome': 'OK', 'error': None,
                'service_record': copy.deepcopy(record), 'descriptor': copy.deepcopy(descriptor) if index == 2 else None})
        def backend_control(_directory, action, **_kwargs):
            value = backend(action, state='ABSENT' if action == 'status' else 'RUNNING')
            if action != 'status':
                value.update(service_record=copy.deepcopy(backend_events[2]['service_record']), descriptor=copy.deepcopy(descriptor))
            return value
        service_terminal = get(a / 'service/latest.json')
        service_running = {**service_terminal, 'state': 'RUNNING', 'observation_sequence': 1, 'heartbeat_monotonic_ns': 200}
        services = iter([service('ABSENT'), service_running, service_running])
        helper = service_fixtures.ServiceVerifierTests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        session = a / 'session'
        assert self.root.resolve() in session.resolve().parents
        shutil.rmtree(session)
        commands = verifier.MODEL_OFFLINE_COMMANDS if offline else verifier.MODEL_ONLINE_COMMANDS
        code = shell.run_console(session, input_stream=io.StringIO('\n'.join(commands) + '\n'), output_stream=io.StringIO(),
            proc_root=helper.proc, sys_root=helper.sysfs, test_system='Linux', resolver=dns, fetcher=fetch,
            service_control=lambda *_args: next(services), agent_control=control, backend_control=backend_control)
        self.assertEqual(code, 0)
        producer.update(lifecycle_state='exited', model_ready=False, source_generation=2)
        authority.observe(producer); event('STOPPING'); event('STOPPED')
        self.save_actors(a, producer, authority, events, requests, resource_files, relation, warmup, proof,
                         backend_id, backend_events, proofs, record, descriptor, MANAGED_SOURCES, BACKEND_SOURCES)
        worker = get(a / 'worker-result.json'); worker['session_id'] = get(session / 'session-result.json')['session_id']
        worker['cleanup'][0]['response'] = {**get(a / 'main/latest.json'), 'action': 'stop'}
        worker['cleanup'][1]['response'] = {**get(a / 'backend/latest.json'), 'action': 'stop'}
        put(a / 'worker-result.json', worker)
        result = get(a / 'root-result.json')
        result.update(schema_version=2, profile='local-model', config_sha256=boot['config_sha256'],
            model_bundle=boot['model_bundle'], model_integrity=boot['model_integrity'],
            session_id=worker['session_id'], worker_result_sha256=verifier.digest(encoded(worker)))
        put(a / 'root-result.json', result)
        launch = get(path / 'launch.json'); launch['stdin_commands'] = commands
        launch['network'] = 'offline' if offline else 'online'
        argv = launch['qemu_argv']; argv[argv.index('-m') + 1] = '3072'
        argv[argv.index('-nic') + 1] = 'none' if offline else 'user,model=e1000'
        put(path / 'launch.json', launch)
        self.base.seal(path)
        self.previous[boot_id] = {'archive_manifest_sha256': verifier.digest((a / 'archive-manifest.json').read_bytes()),
            'root_result_sha256': verifier.digest((a / 'root-result.json').read_bytes())}

    def save_actors(self, a, producer, authority, events, requests, resources, relation, warmup, proof,
                    backend_id, backend_events, proofs, backend_record, descriptor, main_sources, backend_sources):
        identity = {k: producer[k] for k in ('source_id', 'source_instance', 'service_start_generation')}
        state = a / 'main'; run = state / 'runs' / producer['source_instance']; run.mkdir(parents=True)
        (run / 'resources').mkdir()
        start = {'schema_version': 4, **identity, 'service_kind': 'AI_SERVICE', 'capture_kind': 'fixture',
            'started_at': '2026-09-08T00:00:00+00:00', 'config_sha256': verifier.digest(encoded(self.config)),
            'source_hashes': {name: self.manifest['runtime_files'][name] for name in main_sources}}
        for name, value in {'start.json': start, 'config.json': self.config, 'warmup.json': warmup,
                'source.json': producer, 'management.json': authority.snapshot(), 'backend-binding.json': {
                    'schema_version': 1, 'capture_kind': 'fixture', 'initial_proof': proof, 'descriptor': descriptor},
                **requests, **resources}.items(): put(run / name, value)
        (run / 'events.jsonl').write_bytes(b''.join(encoded(row) for row in events))
        result = {'schema_version': 4, **identity, 'state': 'STOPPED', 'exit_code': 0, 'error': None,
            'completed_at': '2026-09-08T00:00:01+00:00', 'capture_kind': 'fixture', 'source_record': producer,
            'management_snapshot': authority.snapshot(), 'files': {p.relative_to(run).as_posix(): verifier.digest(p.read_bytes())
            for p in run.rglob('*') if p.is_file()}}
        put(run / 'result.json', result)
        put(state / 'registry.json', {'schema_version': 1, **identity})
        put(state / 'management.json', authority.export_state())
        put(state / 'resource-state.json', {'schema_version': 1, 'relation': relation,
            'backend_dir': '/run/aios/boots/' + producer['host_boot_id'] + '/backend'})
        put(state / 'latest.json', {'schema_version': 4, 'action': 'status', 'outcome': 'OK', 'error': None,
            'state': 'STOPPED', 'service_kind': 'AI_SERVICE', 'source_record': producer,
            'management_snapshot': authority.snapshot(), 'management_outcome': None, 'inference_receipt': None,
            'resource_result': None, 'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture'})
        state = a / 'backend'; run = state / 'runs' / backend_id['instance_id']; run.mkdir(parents=True)
        start = {**backend_id, 'started_at': '2026-09-08T00:00:00+00:00', 'capture_kind': 'fixture',
            'profile': 'python-fixture', 'config_sha256': verifier.digest(encoded(self.config)),
            'source_hashes': {name: self.manifest['runtime_files'][name] for name in backend_sources},
            'command': ['/usr/bin/python3', self.config['backend_path'], '18081']}
        for name, value in {'start.json': start, 'config.json': self.config, 'service.json': backend_record,
                            'backend-source.json': descriptor, 'health.json': {'status': 'ok'}}.items(): put(run / name, value)
        (run / 'events.jsonl').write_bytes(b''.join(encoded(row) for row in backend_events))
        (run / 'backend-attestations.jsonl').write_bytes(b''.join(encoded(row) for row in proofs))
        (run / 'stdout.log').write_bytes(b'fixture\n'); (run / 'stderr.log').write_bytes(b'')
        result = {**backend_id, 'state': 'STOPPED', 'exit_code': 0, 'error': None,
            'completed_at': '2026-09-08T00:00:01+00:00', 'capture_kind': 'fixture', 'service_record': backend_record,
            'descriptor': descriptor, 'child_exit_code': 0, 'child_exit_verified': True, 'forced': False,
            'log_bytes': {'stdout': 8, 'stderr': 0}, 'files': {p.name: verifier.digest(p.read_bytes()) for p in run.iterdir()}}
        put(run / 'result.json', result)
        put(state / 'registry.json', backend_id); put(state / 'config.json', self.config)
        put(state / 'latest.json', {'schema_version': 1, 'action': 'status', 'outcome': 'OK', 'error': None,
            'state': 'STOPPED', 'service_kind': 'MODEL_BACKEND', 'capture_kind': 'fixture', 'service_record': backend_record,
            'descriptor': None, 'resource_actions': 'UNSUPPORTED'})

    def verdict(self):
        return verifier.verify_image(self.root, require_live=False)

    def test_complete_online_offline_model_fixture_has_fresh_actors_and_stale_rebind(self):
        value = self.verdict()
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertEqual((value['warmup_requests'], value['user_requests']), (2, 2))
        self.assertTrue(value['offline_boot_verified'] and value['stale_rebind_verified'])
        self.assertEqual(verifier.verify_image(self.root)['outcome'], 'FAIL')

    def reseal(self, path):
        a = path / 'archive'
        for actor in ('main', 'backend'):
            for run in (a / actor / 'runs').iterdir():
                result = get(run / 'result.json')
                result['files'] = {name: verifier.digest((run / name).read_bytes()) for name in result['files']}
                put(run / 'result.json', result)
        session = a / 'session'
        result = get(session / 'session-result.json')
        result['files'] = {name: verifier.digest((session / name).read_bytes()) for name in result['files']}
        put(session / 'session-result.json', result)
        result = get(a / 'root-result.json')
        result['worker_result_sha256'] = verifier.digest((a / 'worker-result.json').read_bytes())
        put(a / 'root-result.json', result)
        self.base.seal(path)

    def rejected(self, reason):
        value = self.verdict()
        self.assertEqual(value['outcome'], 'FAIL', value)
        self.assertIn(reason, str(value['reasons']))

    def test_generic_model_boot_replays_real_contract_without_fixed_workflow_claim(self):
        value = verifier.verify_operating_boot(self.root, self.base.boots[1], require_live=False)
        self.assertEqual(value['outcome'], 'PASS', value)
        self.assertEqual((value['main_runs'], value['backend_runs'], value['user_requests']), (1, 1, 1))
        self.assertFalse(value['prior_history_replayed'])

    def recovery_only_boot(self):
        """Build a full copied disk archive; only its fixture console verdict is supplied.

        Fixture console START deliberately has no real process identity. The
        image wrapper still needs a deterministic raw owner for all independent
        receipt / root-worker joins, so the separate console sub-verdict is the
        sole test seam. No backend or image verifier is replaced.
        """
        from test_hosted_backend_recovery_verifier import recovery_fixture, sample
        from verify_backend import SOURCES, FILES
        path = self.base.boots[1]
        archive = path / 'archive'
        boot = get(archive / 'boot.json')
        staging = self.root / 'recovery-input'
        staging.mkdir()
        state, _source, runs, receipt_path, owner = recovery_fixture(staging)
        config_hash = verifier.digest(encoded(self.config))
        source_hashes = {name: self.manifest['runtime_files'][name] for name in SOURCES}
        def update(value):
            if isinstance(value, dict):
                if 'host_boot_id' in value:
                    value['host_boot_id'] = boot['boot_id']
                if 'config_sha256' in value:
                    value['config_sha256'] = config_hash
                if 'source_hashes' in value:
                    value['source_hashes'] = dict(source_hashes)
                for key in ('endpoint', 'model_id', 'model_sha256', 'backend_sha256'):
                    if key in value:
                        value[key] = self.config[key]
                for child in value.values():
                    update(child)
            elif isinstance(value, list):
                for child in value:
                    update(child)
        for target in state.rglob('*.json'):
            value = get(target)
            update(value)
            if target.name == 'config.json':
                value = self.config
            if target.name == 'start.json':
                value['command'] = ['/usr/bin/python3', self.config['backend_path'], '18081']
            put(target, value)
        for target in state.rglob('*.jsonl'):
            rows = [json.loads(line) for line in target.read_bytes().splitlines()]
            update(rows)
            target.write_bytes(b''.join(encoded(row) for row in rows))
        receipt = get(receipt_path)
        owner = {**owner, 'host_boot_id': boot['boot_id']}
        receipt['old_run_hashes'] = {name: verifier.digest((runs[0] / name).read_bytes()) for name in FILES}
        put(receipt_path, receipt)
        for actor in ('main', 'backend', 'session'):
            target = archive / actor
            assert self.root.resolve() in target.resolve().parents
            shutil.rmtree(target)
        shutil.copytree(state, archive / 'backend')
        latest = get(state / 'latest.json')
        recovered = {**latest, 'state': 'RECOVERED', 'descriptor': None,
                     'service_record': {**latest['service_record'], 'lifecycle_state': 'exited', 'backend_ready': False}}
        commands = ['backend start', 'backend recover', 'backend status', 'exit']
        helper = service_fixtures.ServiceVerifierTests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        def control(_directory, action, **_kwargs):
            return {**(latest if action == 'start' else recovered), 'action': action}
        self.assertEqual(shell.run_console(archive / 'session',
            input_stream=io.StringIO('\n'.join(commands) + '\n'), output_stream=io.StringIO(),
            proc_root=helper.proc, sys_root=helper.sysfs, test_system='Linux', backend_control=control), 0)
        session = archive / 'session'
        session_events = [json.loads(line) for line in (session / 'session.events.jsonl').read_bytes().splitlines()]
        session_events[0]['data']['source_process'] = sample(owner, 1, 900)
        (session / 'session.events.jsonl').write_bytes(b''.join(encoded(row) for row in session_events))
        session_result = get(session / 'session-result.json')
        session_result['files'] = {name: verifier.digest((session / name).read_bytes()) for name in session_result['files']}
        put(session / 'session-result.json', session_result)
        worker = get(archive / 'worker-result.json')
        worker.update(session_id=session_result['session_id'], completed_monotonic_ns=3000)
        worker['cleanup'][0]['response'] = agent('stop', state='ABSENT', protocol=4)
        worker['cleanup'][1]['response'] = {**recovered, 'action': 'stop'}
        put(archive / 'worker-result.json', worker)
        result = get(archive / 'root-result.json')
        result.update(session_id=worker['session_id'], worker_process_id=owner['process_id'], completed_monotonic_ns=3100,
                      worker_result_sha256=verifier.digest(encoded(worker)))
        put(archive / 'root-result.json', result)
        launch = get(path / 'launch.json'); launch['stdin_commands'] = commands
        put(path / 'launch.json', launch)
        self.base.seal(path)
        return path, {'outcome': 'PASS', 'command_count': len(commands), 'dns_observed': False, 'https_observed': False}

    def test_generic_wrapper_recovered_only_cleanup_and_normal_fixed_smoke_separation(self):
        path, console = self.recovery_only_boot()
        with patch.object(verifier, 'verify_session', return_value=console):
            value = verifier.verify_operating_boot(self.root, path, require_live=False)
            self.assertEqual(value['outcome'], 'PASS', value)
            self.assertEqual((value['backend_recovered_runs'], value['backend_normal_runs'], value['main_runs']), (1, 0, 0))
            self.assertEqual(value['user_requests'], 0)
            strict = verifier.verify_image(self.root, require_live=False)
            self.assertEqual(strict['outcome'], 'FAIL', strict)
            self.assertIn('host_setup_or_commands', str(strict['reasons']))
            put(self.root / 'fault-instrumentation.json', {'test_only': True})
            normal = verifier.verify_operating_boot(self.root, path, require_live=False)
            strict = verifier.verify_image(self.root, require_live=False)
            for result in (normal, strict):
                self.assertEqual(result['outcome'], 'FAIL', result)
                self.assertIn('expected_fault_image_not_normal', str(result['reasons']))

    def test_generic_wrapper_cannot_rehash_away_recovery_owner_or_child_death(self):
        path, console = self.recovery_only_boot()
        archive = path / 'archive'
        root_path = archive / 'root-result.json'
        original = get(root_path)
        put(root_path, {**original, 'worker_process_id': original['worker_process_id'] + 1})
        self.base.seal(path)
        with patch.object(verifier, 'verify_session', return_value=console):
            value = verifier.verify_operating_boot(self.root, path, require_live=False)
            self.assertEqual(value['outcome'], 'FAIL', value)
            self.assertIn('model_recovery_owner', str(value['reasons']))
            put(root_path, original)
            receipt_path = next((archive / 'backend/recoveries').iterdir())
            receipt = get(receipt_path)
            receipt['recovery']['child_exit_observed'] = False
            put(receipt_path, receipt)
            self.base.seal(path)
            value = verifier.verify_operating_boot(self.root, path, require_live=False)
            self.assertEqual(value['outcome'], 'FAIL', value)
            self.assertIn('recovery_child_exit', str(value['reasons']))

    def test_rehashed_model_pin_and_installation_partial_read_are_rejected(self):
        manifest_path = self.root / 'image-manifest.json'
        bad = copy.deepcopy(self.manifest)
        bad['model_bundle']['files']['Qwen3-0.6B-Q8_0.gguf']['sha256'] = 'f' * 64
        put(manifest_path, bad)
        self.rejected('model_file_pin')
        bad = copy.deepcopy(self.manifest)
        integrity = copy.deepcopy(self.integrity)
        integrity['files']['Qwen3-0.6B-Q8_0.gguf']['size_bytes'] -= 1
        p = self.root / 'installation-files/model-integrity.json'; put(p, integrity)
        bad['installation_files']['model-integrity.json'] = verifier.digest(p.read_bytes())
        put(manifest_path, bad)
        self.rejected('model_integrity')

    def test_rehashed_installed_config_cannot_redirect_model_or_endpoint(self):
        p = self.root / 'installation-files/model-config.json'
        for change in ({'endpoint': 'http://10.0.2.2:18081'}, {'model_path': '/tmp/model.gguf'}):
            put(p, {**self.config, **change})
            manifest = copy.deepcopy(self.manifest)
            manifest['installation_files']['model-config.json'] = verifier.digest(p.read_bytes())
            manifest['model_bundle']['config_sha256'] = verifier.digest(p.read_bytes())
            put(self.root / 'image-manifest.json', manifest)
            self.rejected('endpoint' if 'endpoint' in change else 'installed_model_config')

    def test_boot_full_read_identity_privilege_and_mode_are_not_inherited_from_install(self):
        path = self.base.boots[1]
        original_boot, original_root = get(path / 'archive/boot.json'), get(path / 'archive/root-result.json')
        for key, value in (('boot_id', str(uuid.uuid4())), ('uid', False), ('mode', 0o666), ('sha256', 'e' * 64)):
            boot, root = copy.deepcopy(original_boot), copy.deepcopy(original_root)
            integrity = boot['model_integrity']
            if key == 'boot_id': integrity[key] = value
            else: integrity['files']['Qwen3-0.6B-Q8_0.gguf'][key] = value
            root['model_integrity'] = copy.deepcopy(integrity)
            put(path / 'archive/boot.json', boot); put(path / 'archive/root-result.json', root)
            self.reseal(path)
            self.rejected('model_integrity' if key != 'uid' else 'integer')

    def test_rehashed_result_model_integrity_boolean_uid_is_rejected(self):
        path = self.base.boots[1]
        result_path = path / 'archive/root-result.json'
        result = get(result_path)
        result['model_integrity']['files']['Qwen3-0.6B-Q8_0.gguf']['uid'] = False
        put(result_path, result)
        self.reseal(path)
        self.rejected('integer')

    def test_rehashed_result_model_bundle_boolean_schema_is_rejected(self):
        path = self.base.boots[1]
        result_path = path / 'archive/root-result.json'
        result = get(result_path)
        result['model_bundle']['schema_version'] = True
        put(result_path, result)
        self.reseal(path)
        self.rejected('boot_model_bundle')

    def test_prior_authority_cannot_be_reused_even_after_all_evidence_is_rehashed(self):
        one, two = self.base.boots
        old = get(one / 'archive/main/management.json')['authority_instance']
        current = get(two / 'archive/main/management.json')['authority_instance']
        for p in (two / 'archive').rglob('*'):
            if p.is_file(): p.write_bytes(p.read_bytes().replace(current.encode(), old.encode()))
        console = two / 'archive/session/console.log'
        console.write_bytes(console.read_bytes().replace(current[:8].encode(), old[:8].encode()))
        self.reseal(two)
        self.rejected('model_cross_boot_identity')

    def test_backend_from_other_boot_cannot_supply_this_boot_execution(self):
        path = self.base.boots[1]
        boot = get(path / 'archive/boot.json')['boot_id']
        foreign = str(uuid.uuid4())
        for p in (path / 'archive/backend').rglob('*'):
            if p.is_file(): p.write_bytes(p.read_bytes().replace(boot.encode(), foreign.encode()))
        worker_path = path / 'archive/worker-result.json'
        worker = get(worker_path)
        worker['cleanup'][1]['response'] = json.loads(json.dumps(worker['cleanup'][1]['response']).replace(boot, foreign))
        put(worker_path, worker)
        self.reseal(path)
        self.rejected('model_backend_boot_or_uid')

    def test_wrong_request_worker_parent_is_rejected_after_rehash(self):
        path = self.base.boots[1]
        request = next((path / 'archive/main/runs').glob('*/requests/*.json'))
        value = get(request)
        value['backend_execution']['send']['client']['raw_stat'] = value['backend_execution']['send']['client']['raw_stat'].replace(') S 123 ', ') S 999 ')
        put(request, value)
        self.reseal(path)
        self.rejected('receipt_execution_owner')

    def test_stale_rejection_reason_cannot_be_changed_to_another_allowed_error(self):
        path = self.base.boots[1]
        for p in (path / 'archive').rglob('*'):
            if p.is_file():
                raw = p.read_bytes()
                raw = raw.replace(b'"error":"stale"', b'"error":"not-discovered"')
                raw = raw.replace(b'Error: stale', b'Error: not-discovered')
                p.write_bytes(raw)
        self.reseal(path)
        self.assertEqual(self.verdict()['outcome'], 'FAIL')

    def test_forced_backend_cleanup_cannot_be_promoted_by_guest_poweroff(self):
        path = self.base.boots[1]
        p = next((path / 'archive/backend/runs').glob('*/result.json'))
        value = get(p); value['forced'] = True; put(p, value)
        self.reseal(path)
        self.rejected('child_exit')

    def test_model_schema_cannot_be_downgraded_to_basic(self):
        value = copy.deepcopy(self.manifest); value['schema_version'] = 1
        put(self.root / 'image-manifest.json', value)
        self.rejected('manifest_schema')


if __name__ == '__main__':
    unittest.main()
