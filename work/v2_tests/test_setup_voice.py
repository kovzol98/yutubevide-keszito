"""The optional voice setup verifies downloads before writing runtime files."""
from pathlib import Path
import hashlib
import importlib.util
import io
import shutil
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('repo_setup_voice', ROOT / 'scripts/setup_voice.py')
voice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice)


def asset(content):
    return dict(size=len(content), sha256=hashlib.sha256(content).hexdigest())


class VoiceSetupTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/voice_setup_tests'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.addCleanup(self.remove)

    def remove(self):
        assert self.folder.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.folder)

    def archive(self, entries):
        filename = self.folder / 'runtime.zip'
        with zipfile.ZipFile(filename, 'w') as bundle:
            for name, content in entries.items():
                bundle.writestr(name, content)
        return filename

    def test_correct_runtime_is_installed_and_verified(self):
        data = {'piper/piper.exe': b'fake runtime', 'piper/lang/en': b'language data'}
        archive = self.archive(data)
        expected = {name: asset(content) for name, content in data.items()}
        target = self.folder / 'target'
        voice.install_runtime(archive, target, expected)
        self.assertTrue(all(voice.matches(target / name, value) for name, value in expected.items()))

    def test_all_hashes_are_verified_before_any_runtime_write(self):
        data = {'piper/piper.exe': b'correct', 'piper/lang/en': b'wrong'}
        archive = self.archive(data)
        expected = {'piper/piper.exe': asset(b'correct'), 'piper/lang/en': asset(b'right')}
        target = self.folder / 'target'
        with self.assertRaisesRegex(RuntimeError, 'ellenőrzőösszeg'):
            voice.install_runtime(archive, target, expected)
        self.assertFalse(target.exists())

    def test_unexpected_archive_paths_are_never_extracted(self):
        archive = self.archive({'piper/piper.exe': b'correct', '../escaped.txt': b'bad'})
        target = self.folder / 'target'
        voice.install_runtime(archive, target, {'piper/piper.exe': asset(b'correct')})
        self.assertTrue((target / 'piper/piper.exe').is_file())
        self.assertFalse((self.folder / 'escaped.txt').exists())

    def test_manifest_traversal_is_rejected(self):
        archive = self.archive({'../escaped.txt': b'bad'})
        with self.assertRaisesRegex(RuntimeError, 'fájlútvonal'):
            voice.install_runtime(archive, self.folder / 'target', {'../escaped.txt': asset(b'bad')})

    def test_oversized_download_keeps_existing_target_and_removes_partial(self):
        target = self.folder / 'download.zip'
        target.write_bytes(b'keep existing')
        with patch.object(voice.urllib.request, 'urlopen', return_value=io.BytesIO(b'larger than allowed')):
            with self.assertRaisesRegex(RuntimeError, 'méretet'):
                voice.download('https://example.invalid/fixture', target, 3)
        self.assertEqual(target.read_bytes(), b'keep existing')
        self.assertFalse(target.with_suffix('.zip.part').exists())


if __name__ == '__main__':
    unittest.main()
