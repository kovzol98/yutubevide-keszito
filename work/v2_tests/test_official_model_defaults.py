"""Default Wan model selection and setup never opt in to community Turbo.

No installed models, download endpoint, process, Comfy server or GPU is used.
Only disposable workspace fixtures are written; existing assets are preserved.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
import comfy_video as cv
import setup_comfy as setup

FIXTURES = ROOT / 'work/v2_tests/official_model_fixtures'


class OfficialModelDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.root = FIXTURES / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(self.remove_fixture)
        self.turbo = self.root / 'models/diffusion_models' / cv.TURBO_MODEL_NAME

    def remove_fixture(self):
        path = self.root.resolve()
        if not path.is_relative_to(FIXTURES.resolve()):
            raise AssertionError('Fixture cleanup escaped its workspace.')
        shutil.rmtree(path)

    def portable_files(self):
        for relative in ('ComfyUI_windows_portable/ComfyUI/main.py',
                         'ComfyUI_windows_portable/python_embeded/python.exe',
                         'ComfyUI_windows_portable/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py'):
            file = self.root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'Existing runtime fixture; never executed.')

    def manifest(self, version=None):
        file = self.root / 'comfy_installation.json'
        file.write_text(json.dumps({'version': version or setup.VERSION, 'complete': True}), encoding='utf-8')
        return file

    def test_existing_exact_size_turbo_does_not_replace_the_official_base(self):
        self.turbo.parent.mkdir(parents=True)
        original = b'Existing optional asset; preserve it.'
        self.turbo.write_bytes(original)
        real_stat = Path.stat
        def pretend_original_size(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if path == self.turbo:
                fields = list(result)
                fields[6] = 3815414496
                return os.stat_result(fields)
            return result
        # Simulate the previously preferred 3.8-GB asset without creating it.
        with patch.object(Path, 'stat', autospec=True, side_effect=pretend_original_size):
            selected = cv.selected_models(self.root)
        self.assertEqual(selected, cv.MODEL_NAMES)
        self.assertEqual(selected['unet_name'], 'Wan2.2-TI2V-5B-Q5_K_M.gguf')
        self.assertEqual(self.turbo.read_bytes(), original)

    def test_defaults_are_a_copy_and_need_no_optional_file(self):
        selected = cv.selected_models(self.root)
        selected['unet_name'] = 'caller override'
        self.assertNotEqual(cv.MODEL_NAMES['unet_name'], 'caller override')
        self.assertEqual(cv.selected_models(self.root), cv.MODEL_NAMES)
        self.assertFalse(self.turbo.exists())

    def test_an_explicit_internal_workflow_can_still_name_turbo(self):
        information = json.loads((ROOT / 'work/realism/quality_object_info.json').read_text(encoding='utf-8'))
        choices = information['UnetLoaderGGUF']['input']['required']['unet_name'][0]
        if cv.TURBO_MODEL_NAME not in choices:
            choices.append(cv.TURBO_MODEL_NAME)
        explicit = dict(cv.MODEL_NAMES, unet_name=cv.TURBO_MODEL_NAME)
        graph = cv._workflow(information, 'A tree moves in the wind.', 480, 640, 33,
            20, 42, 'internal_test', model_names=explicit)
        self.assertEqual(graph['1']['inputs']['unet_name'], cv.TURBO_MODEL_NAME)
        self.assertEqual(graph['7']['inputs']['steps'], 4)
        self.assertEqual(graph['7']['inputs']['cfg'], 1.0)
        self.assertEqual(cv.selected_models(self.root), cv.MODEL_NAMES)

    def test_setup_check_is_ready_without_turbo(self):
        self.portable_files()
        self.manifest()
        with patch.object(setup.native_setup, 'check', return_value={'missing': []}):
            status = setup.check(self.root)
        self.assertTrue(status['ready'])
        self.assertEqual(status['missing'], [])
        self.assertFalse(self.turbo.exists())

    def test_old_setup_manifest_requests_refresh_without_missing_turbo(self):
        self.portable_files()
        manifest = self.manifest('comfy-amd-v0.40.0-wan22-gguf-6ea2651-turbo-v2')
        before = manifest.read_bytes()
        with patch.object(setup.native_setup, 'check', return_value={'missing': []}):
            status = setup.check(self.root)
        self.assertFalse(status['ready'])
        self.assertEqual(status['missing'], [])
        self.assertEqual(manifest.read_bytes(), before)
        self.assertNotIn('turbo', status['version'].lower())

    def test_setup_obtains_only_runtime_dependencies_and_preserves_optional_turbo(self):
        self.portable_files()
        self.turbo.parent.mkdir(parents=True)
        self.turbo.write_bytes(b'Installed optional file remains unchanged.')
        before = self.turbo.read_bytes()
        record = {'version': setup.native_setup.VERSION, 'complete': True, 'files': {
            str(self.turbo.relative_to(self.root)).replace('\\', '/'): {'size': len(before), 'sha256': 'retained'}}}
        installed = {details['package']: details['version'] for details in setup.WHEELS}
        with ExitStack() as stack:
            native = stack.enter_context(patch.object(setup.native_setup, 'setup', return_value=self.root))
            stack.enter_context(patch.object(setup.native_setup, '_read_manifest', return_value=record))
            obtain = stack.enter_context(patch.object(setup, '_obtain',
                side_effect=lambda root, asset, *args: root / asset['path']))
            stack.enter_context(patch.object(setup, '_extract_addon'))
            stack.enter_context(patch.object(setup, '_check_dependencies', return_value=installed))
            run = stack.enter_context(patch.object(setup, '_run'))
            stack.enter_context(patch.object(setup.subprocess, 'Popen',
                side_effect=AssertionError('No process may start in setup tests.')))
            stack.enter_context(patch.object(setup.urllib.request, 'urlopen',
                side_effect=AssertionError('No download may start in setup tests.')))
            self.assertEqual(setup.setup(self.root), self.root)
        native.assert_called_once_with(self.root, None, None)
        self.assertEqual([call.args[1] for call in obtain.call_args_list], [setup.ADDON_ASSET])
        self.assertEqual(run.call_count, 1)  # The device probe command was captured, never executed.
        self.assertEqual(self.turbo.read_bytes(), before)
        manifest = json.loads((self.root / 'comfy_installation.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], setup.VERSION)
        self.assertIs(manifest['complete'], True)
        self.assertEqual(manifest['default_model'], setup.native_setup.ASSETS[1]['path'])
        self.assertEqual(manifest['automatic_model_variant'], 'official_wan22_5b')
        self.assertNotIn('turbo_sha256', manifest)
        self.assertTrue((self.root / 'youtube_muhely_models.yaml').is_file())

    def test_setup_catalog_contains_no_automatic_turbo_asset(self):
        self.assertFalse(hasattr(setup, 'TURBO_ASSET'))
        for asset in setup.native_setup.ASSETS:
            self.assertNotIn('turbo', (asset['path'] + asset['url']).lower())


if __name__ == '__main__':
    unittest.main()
