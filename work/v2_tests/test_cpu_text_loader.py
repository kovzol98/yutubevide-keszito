"""CPU-only proof that the UMT5 GGUF loader never selects a GPU device."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
import realistic_video

CLIP_NAME = 'umt5-xxl-encoder-Q5_K_M.gguf'


class CpuTextLoaderTests(unittest.TestCase):
    def setUp(self):
        self.cpu = 'cpu'
        self.path = '/fixture/models/text_encoders/' + CLIP_NAME
        self.weights = [{'gguf_state': object()}]
        self.patcher = SimpleNamespace(load_device='cpu', offload_device='cpu')
        self.clip = SimpleNamespace(patcher=self.patcher)
        self.gguf = ModuleType('fake_gguf.nodes')
        self.gguf.GGMLOps = object()
        self.gguf.GGUFModelPatcher = SimpleNamespace(clone=Mock(return_value=self.patcher))
        load_data, load_patcher = Mock(return_value=self.weights), Mock()
        class Original:
            __module__ = 'fake_gguf.nodes'
            get_filename_list = classmethod(lambda cls: [CLIP_NAME])
        Original.load_data, Original.load_patcher = load_data, load_patcher
        self.original = Original
        torch = ModuleType('torch')
        torch.device = lambda name: name
        torch.float32 = object()
        self.torch = torch
        self.comfy = ModuleType('comfy')
        self.sd = ModuleType('comfy.sd')
        self.sd.CLIPType = SimpleNamespace(WAN=object())
        self.sd.load_text_encoder_state_dicts = Mock(return_value=self.clip)
        self.mm = ModuleType('comfy.model_management')
        self.comfy.sd, self.comfy.model_management = self.sd, self.mm
        self.paths = ModuleType('folder_paths')
        self.paths.get_full_path_or_raise = Mock(return_value=self.path)
        self.paths.get_folder_paths = Mock(return_value=['/fixture/embeddings'])
        self.nodes = ModuleType('nodes')
        self.nodes.NODE_CLASS_MAPPINGS = {'CLIPLoaderGGUF': Original}
        advanced = ModuleType('comfy_extras.nodes_model_advanced')
        advanced.ModelSamplingSD3 = object()
        self.modules = {'torch': torch, 'comfy': self.comfy, 'comfy.sd': self.sd,
            'comfy.model_management': self.mm, 'folder_paths': self.paths, 'nodes': self.nodes,
            'comfy_extras': ModuleType('comfy_extras'),
            'comfy_extras.nodes_model_advanced': advanced, 'fake_gguf.nodes': self.gguf}
        context = patch.dict(sys.modules, self.modules)
        context.start()
        self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location('ytmuhely_cpu_loader_fixture',
            ROOT / 'work/v2_source/tools/quality_nodes/__init__.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def load(self, **changes):
        return self.module.YouTubeCpuCLIPLoader().load_clip(**dict(
            {'clip_name': CLIP_NAME, 'type': 'wan'}, **changes))

    def test_schema_registers_exact_gguf_names_and_wan_only(self):
        cls = self.module.YouTubeCpuCLIPLoader
        self.assertIs(self.module.NODE_CLASS_MAPPINGS['YouTubeCpuCLIPLoader'], cls)
        self.assertEqual(cls.RETURN_TYPES, ('CLIP',))
        self.assertEqual(cls.FUNCTION, 'load_clip')
        self.assertEqual(cls.INPUT_TYPES()['required']['clip_name'][0], [CLIP_NAME])
        self.assertEqual(cls.INPUT_TYPES()['required']['type'][0], ['wan'])

    def test_constructor_receives_cpu_load_offload_initial_and_float32(self):
        self.assertEqual(self.load(), (self.clip,))
        options = self.sd.load_text_encoder_state_dicts.call_args.kwargs
        self.assertIs(options['clip_type'], self.sd.CLIPType.WAN)
        self.assertIs(options['state_dicts'], self.weights)
        self.assertEqual(options['model_options'], {
            'custom_operations': self.gguf.GGMLOps,
            'load_device': 'cpu', 'offload_device': 'cpu', 'initial_device': 'cpu',
            'dtype': self.torch.float32})
        self.assertTrue(options['disable_dynamic'])
        self.assertEqual(options['embedding_directory'], ['/fixture/embeddings'])

    def test_reuses_original_cpu_gguf_file_reader_but_bypasses_gpu_loader(self):
        self.load()
        self.paths.get_full_path_or_raise.assert_called_once_with('clip', CLIP_NAME)
        self.original.load_data.assert_called_once_with([self.path])
        self.original.load_patcher.assert_not_called()
        self.gguf.GGUFModelPatcher.clone.assert_called_once_with(self.patcher)

    def test_missing_gguf_extension_fails_before_reading_any_weight(self):
        self.nodes.NODE_CLASS_MAPPINGS.clear()
        with self.assertRaisesRegex(RuntimeError, 'ComfyUI-GGUF'):
            self.load()
        self.original.load_data.assert_not_called()
        self.sd.load_text_encoder_state_dicts.assert_not_called()

    def test_non_wan_type_rejected_before_reading_model(self):
        for kind in ('stable_diffusion', 'WAN', '', None):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.load(type=kind)
        self.original.load_data.assert_not_called()

    def test_unknown_clip_name_rejected_before_path_lookup(self):
        with self.assertRaises(ValueError):
            self.load(clip_name='foreign.gguf')
        self.paths.get_full_path_or_raise.assert_not_called()

    def test_patcher_cpu_postcondition_is_checked(self):
        self.gguf.GGUFModelPatcher.clone.return_value = SimpleNamespace(load_device='cuda:0', offload_device='cpu')
        with self.assertRaisesRegex(RuntimeError, 'CPU-s betöltése nem igazolható'):
            self.load()

    def test_loader_does_not_mutate_global_device_selectors_or_node_registration(self):
        old_mm = dict(vars(self.mm))
        old_nodes = dict(self.nodes.NODE_CLASS_MAPPINGS)
        self.load()
        self.assertEqual(vars(self.mm), old_mm)
        self.assertEqual(self.nodes.NODE_CLASS_MAPPINGS, old_nodes)
        self.assertFalse(hasattr(self.torch, 'cuda'), 'The stub forbids all GPU API calls')

    def test_failed_load_does_not_leave_global_device_override(self):
        self.sd.load_text_encoder_state_dicts.side_effect = RuntimeError('fixture load failure')
        old_mm = dict(vars(self.mm))
        with self.assertRaisesRegex(RuntimeError, 'fixture load failure'):
            self.load()
        self.assertEqual(vars(self.mm), old_mm)


class CpuTextWorkflowTests(unittest.TestCase):
    def test_quality_graph_routes_both_prompts_through_cpu_loader(self):
        information = json.loads((ROOT / 'work/realism/quality_object_info.json').read_text(encoding='utf-8'))
        information['YouTubeCpuCLIPLoader'] = {'input': {'required': {
            'clip_name': [[CLIP_NAME]], 'type': [['wan'], {'default': 'wan'}]}}, 'output': ['CLIP']}
        previous = copy.deepcopy(information)
        reference = information['LoadImage']['input']['required']['image'][0][0]
        graph = realistic_video.workflow(information, 'A person waves.', 480, 640, 17, 4, 42,
                                         'YouTubeMuhely/jelenet_001_0123456789', reference)
        self.assertIn('YouTubeCpuCLIPLoader', realistic_video.EXTRA_NODES)
        self.assertEqual(graph['2'], {'class_type': 'YouTubeCpuCLIPLoader',
                                     'inputs': {'clip_name': CLIP_NAME, 'type': 'wan'}})
        self.assertEqual(graph['5']['inputs']['clip'], ['2', 0])
        self.assertEqual(graph['6']['inputs']['clip'], ['2', 0])
        self.assertEqual(graph['7']['class_type'], 'YouTubeWan14RamSampler')
        self.assertEqual(information, previous)


if __name__ == '__main__':
    unittest.main()
