"""Installer input failures must never become a successfully installed model."""
import hashlib
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_finalize import copy_model_input
from qemu_image import base_command, MODEL_ONLINE, MODEL_OFFLINE, MODEL_PROMPT_TIMEOUT


class ModelImageBuilderTests(unittest.TestCase):
    def test_exact_read_only_model_payload_copied_without_sector_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'bounded model fixture' * 37
            (root / 'input').write_bytes(payload + b'\0' * (-len(payload) % 512))
            expected = {'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            copy_model_input(root / 'input', root / 'installed', expected)
            self.assertEqual((root / 'installed').read_bytes(), payload)
            # TemporaryDirectory must remain removable on Windows as well.
            (root / 'installed').chmod(0o644)

    def test_truncated_changed_and_extra_model_bytes_are_rejected(self):
        payload = b'a' * 600
        expected = {'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        for wrong in (payload[:-1], b'b' * 600 + b'\0' * 424,
                      payload + b'\0' * 424 + b'x', payload + b'x' * 424):
            with self.subTest(length=len(wrong)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'input').write_bytes(wrong)
                with self.assertRaises(ValueError):
                    copy_model_input(root / 'input', root / 'installed', expected)

    def test_installer_will_not_replace_an_existing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'input').write_bytes(b'a' + b'\0' * 511)
            (root / 'installed').write_bytes(b'preserve existing')
            with self.assertRaises(FileExistsError):
                copy_model_input(root / 'input', root / 'installed',
                                 {'size_bytes': 1, 'sha256': hashlib.sha256(b'a').hexdigest()})
            self.assertEqual((root / 'installed').read_bytes(), b'preserve existing')

    def test_profile_resources_and_independent_verifier_commands_agree(self):
        import verify_image
        basic, model = base_command(Path('/qemu')), base_command(Path('/qemu'), model=True)
        self.assertEqual(basic[basic.index('-m') + 1], '768')
        self.assertEqual(model[model.index('-m') + 1], '3072')
        self.assertEqual(MODEL_ONLINE, verify_image.MODEL_ONLINE_COMMANDS)
        self.assertEqual(MODEL_OFFLINE, verify_image.MODEL_OFFLINE_COMMANDS)

    def test_outer_observer_does_not_kill_a_valid_inference_worker(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'hosted/linux'))
        from aios_agent.inference import TIMEOUT
        self.assertGreater(MODEL_PROMPT_TIMEOUT, TIMEOUT + 60)


if __name__ == '__main__':
    unittest.main()
