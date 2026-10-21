"""CPU-only contracts for one decode retry from this job's saved latent.

No server, model, GPU or network is used. The history/download integration uses
real PNG bytes and the real history loop; the final encoder is a local mock.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import sys
import unittest
import urllib.error
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import comfy_recovery
import comfy_video
import realistic_video
import ytmuhely as app
from PIL import Image

BASE = 'http://127.0.0.1:18190'
PREFIX = 'YouTubeMuhely/jelenet_001_a93e820b14'
FIXTURES = ROOT / 'work/comfy_recovery_tests'


def empty_queue():
    return {'queue_running': [], 'queue_pending': []}


def failed_history(prompt_id, graph, prefix):
    name = prefix.rsplit('/', 1)[-1] + '_latent_00001_.latent'
    return {
        'status': {'status_str': 'error', 'completed': False, 'messages': [
            ['execution_error', {'prompt_id': prompt_id, 'node_id': '9',
                'node_type': graph['9']['class_type'],
                'exception_type': 'torch.OutOfMemoryError',
                'exception_message': 'HIP out of memory. Tried to allocate 256 MiB',
                'executed': ['3', '7', '15']}] ]},
        'outputs': {'15': {'latents': [{'filename': name,
            'subfolder': 'YouTubeMuhely', 'type': 'output'}]}}}


class WorkspaceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.information = json.loads((ROOT / 'work/realism/quality_object_info.json').read_text(encoding='utf-8'))
        cls.reference = cls.information['LoadImage']['input']['required']['image'][0][0]

    def setUp(self):
        # TemporaryDirectory has an inherited-ACL issue in this Windows sandbox.
        self.base = FIXTURES / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.addCleanup(self.remove_workspace)

    def remove_workspace(self):
        self.assertTrue(self.base.resolve().is_relative_to(FIXTURES.resolve()))
        shutil.rmtree(self.base)

    def quality_graph(self, prefix=PREFIX, frames=81, width=480, height=640):
        return realistic_video.workflow(self.information, 'A person dances.',
            width, height, frames, 4, 42, prefix, self.reference,
            image_motion='Teljes alak • tánc')


class RecoveryProofTests(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.graph = self.quality_graph()
        self.result = failed_history('original', self.graph, PREFIX)
        self.output = self.base / 'output'
        self.checkpoint = self.output / 'YouTubeMuhely' / self.result['outputs']['15']['latents'][0]['filename']
        self.checkpoint.parent.mkdir(parents=True)
        self.content = b'exact checkpoint tensor fixture for this job'
        self.checkpoint.write_bytes(self.content)
        self.engine = app.Engine()
        self.process = Mock()
        self.process.poll.return_value = None
        self.engine._comfy_process = self.process
        self.engine._comfy_url = BASE
        self.engine._comfy_prompt_id = 'original'
        self.request = Mock(return_value=empty_queue())

    def recover(self, **changes):
        values = dict(engine=self.engine, base=BASE, prompt_id='original',
            result=self.result, graph=self.graph, prefix=PREFIX, frames=81,
            output_root=self.output, information=self.information, request=self.request)
        values.update(changes)
        return comfy_recovery.decode_recovery(**values)

    def test_only_decode_nodes_are_rebuilt_with_the_exact_hashed_checkpoint(self):
        original = copy.deepcopy(self.graph)
        plan = self.recover()
        self.assertIsNotNone(plan)
        graph = plan['graph']
        self.assertEqual(set(graph), {'3', '16', '9', '10'})
        self.assertEqual({node['class_type'] for node in graph.values()},
            {'VAELoader', 'LoadLatent', 'VAEDecodeTiled', 'SaveImage'})
        self.assertEqual(graph['3'], self.graph['3'])
        self.assertEqual(graph['16']['inputs']['latent'],
            'YouTubeMuhely/' + self.checkpoint.name + ' [output]')
        self.assertEqual(graph['9']['inputs'], {'samples': ['16', 0], 'vae': ['3', 0],
            'tile_size': 256, 'overlap': 64, 'temporal_size': 84, 'temporal_overlap': 4})
        self.assertEqual(graph['10']['inputs'],
            {'images': ['9', 0], 'filename_prefix': PREFIX + '_decode_retry'})
        self.assertEqual(plan['checkpoint']['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(Path(plan['checkpoint']['path']), self.checkpoint)
        self.assertEqual(plan['checkpoint']['original_prompt_id'], 'original')
        self.assertEqual(self.graph, original, 'Recovery must not mutate the sampling graph')
        self.request.assert_called_once_with(BASE, '/queue', timeout=5)

    def test_checkpoint_is_an_ordered_latent_dependency_of_the_first_decoder(self):
        self.assertEqual(self.graph['15']['class_type'], 'SaveLatent')
        self.assertEqual(self.graph['15']['inputs']['samples'], ['7', 0])
        self.assertEqual(self.graph['9']['inputs']['samples'], ['15', 0])
        self.assertEqual(self.information['SaveLatent']['output'], ['LATENT'])
        for change in ('missing_checkpoint', 'wrong_source', 'decoder_bypasses_checkpoint'):
            graph = copy.deepcopy(self.graph)
            if change == 'missing_checkpoint': graph.pop('15')
            elif change == 'wrong_source': graph['15']['inputs']['samples'] = ['8', 0]
            else: graph['9']['inputs']['samples'] = ['7', 0]
            with self.subTest(change=change): self.assertIsNone(self.recover(graph=graph))

    def test_retry_graph_matches_actual_installed_node_inputs_and_connection_types(self):
        graph = self.recover()['graph']
        for node in graph.values():
            schema = self.information[node['class_type']]
            fields = {**schema['input'].get('required', {}), **schema['input'].get('optional', {})}
            self.assertLessEqual(set(node['inputs']), set(fields))
            self.assertLessEqual(set(schema['input'].get('required', {})), set(node['inputs']))
            for name, value in node['inputs'].items():
                if isinstance(value, list) and len(value) == 2 and value[0] in graph:
                    actual_type = self.information[graph[value[0]]['class_type']]['output'][value[1]]
                    self.assertEqual(fields[name][0], actual_type)

    def test_spatial_512_oom_may_retry_but_256_never_retries_again(self):
        graph = self.quality_graph(width=832, height=1088)
        result = failed_history('original', graph, PREFIX)
        self.assertIsNotNone(self.recover(graph=graph, result=result))
        graph['9']['inputs']['tile_size'] = 256
        self.assertIsNone(self.recover(graph=graph, result=result))

    def test_old_continuous_decoder_failures_remain_recoverable_without_resampling(self):
        graph = copy.deepcopy(self.graph)
        graph['9'] = {'class_type': 'VAEDecode', 'inputs': {'samples': ['15', 0], 'vae': ['3', 0]}}
        result = failed_history('original', graph, PREFIX)
        plan = self.recover(graph=graph, result=result)
        self.assertIsNotNone(plan)
        self.assertEqual(plan['graph']['9']['class_type'], 'VAEDecodeTiled')
        self.assertEqual(plan['graph']['9']['inputs']['tile_size'], 256)

    def test_full_temporal_window_is_kept_and_overlong_scene_is_rejected(self):
        for frames in (5, 81, 161, 1921, 2881):
            with self.subTest(frames=frames):
                temporal = self.recover(frames=frames)['graph']['9']['inputs']['temporal_size']
                self.assertGreaterEqual(temporal, frames)
                self.assertLessEqual(temporal, 4096)
        self.assertIsNone(self.recover(frames=5001))

    def test_only_one_node_9_oom_after_a_completed_save_is_eligible(self):
        mutations = {
            'sampler_oom': {'node_id': '7', 'node_type': 'YouTubeWan14RamSampler'},
            'save_oom': {'node_id': '15', 'node_type': 'SaveLatent'},
            'wrong_node_type': {'node_type': 'OtherDecoder'},
            'foreign_prompt': {'prompt_id': 'someone-else'},
            'checkpoint_not_executed': {'executed': ['3', '7']},
            'non_oom': {'exception_type': 'RuntimeError', 'exception_message': 'tensor size mismatch'},
            'unsafe_gpu_state': {'exception_message': 'out of memory; illegal memory access'},
            'device_assert': {'exception_message': 'out of memory; device-side assert'},
        }
        for name, fields in mutations.items():
            result = copy.deepcopy(self.result)
            result['status']['messages'][0][1].update(fields)
            with self.subTest(failure=name): self.assertIsNone(self.recover(result=result))
        for event in ('execution_interrupted', 'execution_error'):
            result = copy.deepcopy(self.result)
            result['status']['messages'].append([event, copy.deepcopy(result['status']['messages'][0][1])])
            with self.subTest(extra_event=event): self.assertIsNone(self.recover(result=result))
        result = copy.deepcopy(self.result); result['status']['status_str'] = 'success'
        self.assertIsNone(self.recover(result=result))

    def test_runtime_oom_is_supported_but_a_legacy_sampler_is_not(self):
        result = copy.deepcopy(self.result)
        result['status']['messages'][0][1]['exception_type'] = 'RuntimeError'
        self.assertIsNotNone(self.recover(result=result))
        graph = copy.deepcopy(self.graph); graph['7']['class_type'] = 'KSampler'
        self.assertIsNone(self.recover(graph=graph))

    def test_foreign_or_traversal_checkpoint_locators_are_rejected(self):
        locators = [
            {'filename': 'jelenet_001_fffffffffe_latent_00001_.latent'},
            {'filename': '../' + self.checkpoint.name},
            {'filename': str(self.checkpoint)},
            {'filename': self.checkpoint.name + '.other'},
            {'subfolder': '../YouTubeMuhely'}, {'subfolder': 'other'},
            {'type': 'input'}, {'type': 'temp'},
        ]
        for locator in locators:
            result = copy.deepcopy(self.result)
            result['outputs']['15']['latents'][0].update(locator)
            with self.subTest(locator=locator): self.assertIsNone(self.recover(result=result))
        for prefix in ('YouTubeMuhely/check', 'YouTubeMuhely/jelenet_001_ABCDEF1234', '../' + PREFIX):
            with self.subTest(prefix=prefix): self.assertIsNone(self.recover(prefix=prefix))

    def test_missing_short_or_ambiguous_checkpoint_is_rejected(self):
        result = copy.deepcopy(self.result)
        result['outputs']['15']['latents'] *= 2
        self.assertIsNone(self.recover(result=result))
        self.checkpoint.write_bytes(b'partial')
        self.assertIsNone(self.recover())
        self.checkpoint.unlink()
        self.assertIsNone(self.recover())

    def test_redirected_checkpoint_paths_and_missing_temporal_controls_are_rejected(self):
        # No Windows symlink privileges are required to exercise the path gate.
        with patch.object(Path, 'is_symlink', return_value=True):
            self.assertIsNone(self.recover())
        self.request.assert_not_called()
        information = copy.deepcopy(self.information)
        for group in ('required', 'optional'):
            information['VAEDecodeTiled']['input'].get(group, {}).pop('temporal_size', None)
        self.assertIsNone(self.recover(information=information))
        self.request.assert_not_called()

    def test_other_or_unprovable_queue_state_never_triggers_retry(self):
        queues = [None, {}, {'queue_running': []},
            {'queue_running': [[0, 'other', {}, {}]], 'queue_pending': []},
            {'queue_running': [], 'queue_pending': [[0, 'original', {}, {}]]}]
        for queue in queues:
            with self.subTest(queue=queue):
                self.request.return_value = queue
                self.assertIsNone(self.recover())
        for error in (OSError('reset'), urllib.error.URLError('offline'),
                      ValueError('invalid JSON'), RuntimeError('HTTP 500')):
            with self.subTest(error=type(error).__name__):
                self.request.side_effect = error
                self.assertIsNone(self.recover())

    def test_existing_external_dead_or_changed_process_is_not_owned(self):
        original = self.process
        for state in ('external', 'dead', 'url_changed', 'prompt_changed'):
            self.engine._comfy_process = original
            self.engine._comfy_url = BASE
            self.engine._comfy_prompt_id = 'original'
            original.poll.return_value = None
            if state == 'external': self.engine._comfy_process = None
            elif state == 'dead': original.poll.return_value = 1
            elif state == 'url_changed': self.engine._comfy_url = BASE + '/other'
            else: self.engine._comfy_prompt_id = 'other'
            with self.subTest(state=state): self.assertIsNone(self.recover())

    def test_ownership_is_rechecked_after_queue_and_checkpoint_hashing(self):
        for state in ('process', 'url', 'prompt', 'exit'):
            self.engine._comfy_process = self.process
            self.engine._comfy_url = BASE
            self.engine._comfy_prompt_id = 'original'
            self.process.poll.return_value = None
            def changed(*args, **kwargs):
                if state == 'process': self.engine._comfy_process = Mock()
                elif state == 'url': self.engine._comfy_url = BASE + '/changed'
                elif state == 'prompt': self.engine._comfy_prompt_id = 'changed'
                else: self.process.poll.return_value = 1
                return empty_queue()
            self.request.side_effect = changed
            with self.subTest(state=state): self.assertIsNone(self.recover())

    def test_cancellation_propagates_before_requests_and_while_hashing(self):
        self.engine.cancel.set()
        with self.assertRaises(app.Cancelled): self.recover()
        self.request.assert_not_called()
        self.engine.cancel.clear()
        self.engine.check = Mock(side_effect=[None, None, app.Cancelled('hash cancelled')])
        with self.assertRaisesRegex(app.Cancelled, 'hash cancelled'): self.recover()


class GenerateRetryIntegrationTests(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.motor = self.base / 'motor'
        self.portable = self.motor / 'ComfyUI_windows_portable'
        (self.portable / 'ComfyUI/output/YouTubeMuhely').mkdir(parents=True)
        (self.portable / 'ComfyUI/main.py').write_text('# fixture')
        (self.portable / 'python_embeded').mkdir()
        (self.portable / 'python_embeded/python.exe').write_bytes(b'not executed')
        self.work = self.base / 'ideiglenes'; self.work.mkdir()
        self.source = self.base / 'source.png'
        Image.new('RGB', (48, 64), (155, 120, 90)).save(self.source)
        self.png = None
        self.engine = app.Engine()
        self.process = Mock(); self.process.poll.return_value = None
        self.process.wait.return_value = 0
        self.submitted = []; self.deletions = []; self.histories = []
        self.monitors = []; self.downloads = []; self.encodes = []
        self.retry_failure = False; self.cancel_retry = False
        self.queue_changed_after_proof = False; self.queue_reads = 0
        self.process_changed_at_final_queue = False
        self.download_failure = False; self.encode_failure = False
        self.project = app.Project(ai_root=str(self.motor), video_prompt='Dance naturally.',
            image_motion='Teljes alak • tánc')

    def ensure_server(self, engine, root, *args, **kwargs):
        engine._comfy_process = self.process
        engine._comfy_url = BASE
        engine._comfy_root = str(root)
        return BASE

    def request(self, base, route, payload=None, timeout=15):
        self.assertEqual(base, BASE)
        if route == '/object_info': return self.information
        if route == '/object_info/LoadImage': return {'LoadImage': self.information['LoadImage']}
        if route == '/prompt':
            graph = payload['prompt']; self.submitted.append(copy.deepcopy(graph))
            self.assertLessEqual(len(self.submitted), 2, 'A third prompt must never be submitted')
            if len(self.submitted) == 1:
                size = (graph['8']['inputs']['width'], graph['8']['inputs']['height'])
                png = io.BytesIO(); Image.new('RGB', size, (120, 80, 50)).save(png, format='PNG')
                self.png = png.getvalue()
                prefix = graph['15']['inputs']['filename_prefix'].removesuffix('_latent')
                self.first_error = failed_history('original', graph, prefix)
                self.saved = self.portable / 'ComfyUI/output/YouTubeMuhely' / self.first_error['outputs']['15']['latents'][0]['filename']
                self.saved.write_bytes(b'persisted latent belongs only to original prompt')
                return {'prompt_id': 'original'}
            return {'prompt_id': 'retry'}
        if route == '/history/original':
            self.histories.append('original'); return {'original': self.first_error}
        if route == '/history/retry':
            self.histories.append('retry')
            if self.cancel_retry:
                self.engine.cancel.set()
                return {}
            if self.retry_failure:
                result = copy.deepcopy(self.first_error)
                result['status']['messages'][0][1].update(prompt_id='retry', node_type='VAEDecodeTiled')
                return {'retry': result}
            images = [{'filename': 'frame_%05d.png' % index, 'subfolder': 'YouTubeMuhely', 'type': 'output'} for index in range(5)]
            return {'retry': {'status': {'status_str': 'success', 'completed': True},
                'outputs': {'10': {'images': images}}}}
        if route == '/queue':
            if payload is not None: self.deletions.extend(payload['delete'])
            else:
                self.queue_reads += 1
                if self.queue_changed_after_proof and self.queue_reads >= 2:
                    return {'queue_running': [], 'queue_pending': [[1, 'other-client', {}, {}]]}
                if self.process_changed_at_final_queue and self.queue_reads == 2:
                    self.engine._comfy_process = None
            return empty_queue()
        self.fail('Unexpected HTTP operation: ' + route)

    def download(self, url, timeout):
        self.assertTrue(url.startswith(BASE + '/view?'))
        self.downloads.append(url)
        if self.download_failure: raise OSError('frame download interrupted')
        return io.BytesIO(self.png)

    def encode(self, arguments):
        self.encodes.append(arguments)
        if self.encode_failure: raise RuntimeError('fixture encoder failed')
        sequence = Path(arguments[arguments.index('-i') + 1]).parent
        self.assertEqual(len(list(sequence.glob('frame_*.png'))), 5)
        for frame in sequence.glob('frame_*.png'):
            self.assertEqual(frame.read_bytes(), self.png)
            with Image.open(frame) as image: image.verify()
        Path(arguments[-1]).write_bytes(b'mocked successful local encoding')

    def progress(self, *args):
        monitor = Mock(); monitor.describe.return_value = ('', None)
        self.monitors.append(monitor); return monitor

    def generate(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(realistic_video, 'engine_files', return_value=self.base))
            stack.enter_context(patch.object(comfy_video, '_ensure_server', self.ensure_server))
            stack.enter_context(patch.object(comfy_video, '_request', self.request))
            stack.enter_context(patch.object(comfy_video, '_upload_image', return_value=self.reference))
            stack.enter_context(patch.object(comfy_video.urllib.request, 'urlopen', self.download))
            stack.enter_context(patch('comfy_progress.JobProgress', side_effect=self.progress))
            stack.enter_context(patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg-fixture'))
            stack.enter_context(patch.object(self.engine, 'run', side_effect=self.encode))
            stack.enter_context(patch.object(comfy_video.time, 'sleep'))
            return comfy_video.generate(self.engine, self.project, '', self.source,
                0, self.work, .25, backend=realistic_video)

    def test_one_failed_original_then_one_decode_only_success_downloads_valid_frames(self):
        video = self.generate()
        self.assertEqual(video.read_bytes(), b'mocked successful local encoding')
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.histories, ['original', 'retry'])
        self.assertIn('YouTubeWan14RamSampler', [n['class_type'] for n in self.submitted[0].values()])
        self.assertEqual(set(self.submitted[1]), {'3', '16', '9', '10'})
        self.assertEqual(self.submitted[1]['9']['inputs']['tile_size'], 256)
        self.assertEqual(len(self.downloads), 5); self.assertEqual(len(self.encodes), 1)
        self.assertEqual(self.deletions, [])
        recovery = json.loads((video.parent / 'decode_retry.json').read_text(encoding='utf-8'))
        self.assertEqual(recovery['attempt'], 1)
        self.assertTrue(recovery['completed']); self.assertTrue(recovery['submitted'])
        self.assertTrue(recovery['decoded']); self.assertTrue(recovery['frames_published'])
        self.assertTrue(recovery['encoded'])
        self.assertFalse(recovery['resampled'])
        self.assertEqual(recovery['checkpoint']['sha256'], hashlib.sha256(self.saved.read_bytes()).hexdigest())
        self.assertEqual(recovery['retry_prompt_id'], 'retry')
        settings = json.loads((video.parent / 'video_settings.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['decoder'], 'VAEDecodeTiled')
        self.assertEqual(settings['decoder_options']['tile_size'], 256)
        self.assertTrue((video.parent / 'history_before_decode_retry.json').is_file())
        for monitor in self.monitors:
            monitor.start.assert_called_once(); monitor.close.assert_called_once()

    def test_a_failed_retry_never_samples_again_and_cancels_the_active_retry(self):
        self.retry_failure = True
        with self.assertRaisesRegex(RuntimeError, 'egyszeri helyreállítása nem sikerült'): self.generate()
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.deletions, ['retry'])
        self.assertEqual(self.downloads, []); self.assertEqual(self.encodes, [])
        self.assertTrue(self.saved.is_file(), 'A failed decoder must preserve the long-running sample')
        self.process.terminate.assert_called_once()
        for monitor in self.monitors: monitor.close.assert_called_once()

    def test_cancellation_of_retry_cleans_up_the_retry_not_the_original(self):
        self.cancel_retry = True
        with self.assertRaises(app.Cancelled): self.generate()
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.deletions, ['retry'])
        self.assertEqual(self.downloads, []); self.assertEqual(self.encodes, [])
        self.process.terminate.assert_called_once()
        self.assertTrue(self.saved.is_file())
        for monitor in self.monitors: monitor.close.assert_called_once()

    def test_queue_change_between_checkpoint_proof_and_retry_post_prevents_submission(self):
        self.queue_changed_after_proof = True
        with self.assertRaises(RuntimeError): self.generate()
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.deletions, ['original'])
        self.assertTrue(self.saved.is_file())
        self.assertEqual(self.downloads, []); self.assertEqual(self.encodes, [])
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()
        for monitor in self.monitors: monitor.close.assert_called_once()

    def test_process_change_during_final_queue_check_prevents_retry_post(self):
        self.process_changed_at_final_queue = True
        with self.assertRaises(RuntimeError): self.generate()
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.deletions, ['original'])
        self.assertTrue(self.saved.is_file())
        self.assertEqual(self.downloads, []); self.assertEqual(self.encodes, [])
        self.process.terminate.assert_not_called()
        for monitor in self.monitors: monitor.close.assert_called_once()

    def test_download_failure_does_not_claim_published_or_encoded_success(self):
        self.download_failure = True
        with self.assertRaisesRegex(OSError, 'frame download interrupted'): self.generate()
        metadata = json.loads((self.base / 'AI_jelenetek/jelenet_001/decode_retry.json').read_text(encoding='utf-8'))
        self.assertTrue(metadata['decoded'])
        self.assertFalse(metadata['frames_published']); self.assertFalse(metadata['encoded'])
        self.assertFalse(metadata['completed']); self.assertEqual(metadata['error_type'], 'OSError')
        self.assertEqual(self.deletions, ['retry'])
        self.assertTrue(self.saved.is_file())

    def test_encode_failure_keeps_published_frames_without_claiming_completion(self):
        self.encode_failure = True
        with self.assertRaisesRegex(RuntimeError, 'fixture encoder failed'): self.generate()
        scene = self.base / 'AI_jelenetek/jelenet_001'
        metadata = json.loads((scene / 'decode_retry.json').read_text(encoding='utf-8'))
        self.assertTrue(metadata['decoded']); self.assertTrue(metadata['frames_published'])
        self.assertFalse(metadata['encoded']); self.assertFalse(metadata['completed'])
        self.assertEqual(len(list((scene / 'frames').glob('frame_*.png'))), 5)
        self.assertFalse((scene / 'video.mp4').exists())
        self.assertFalse((scene / 'video.partial.mp4').exists())
        self.assertEqual(self.deletions, ['retry'])
        self.assertTrue(self.saved.is_file())

    def test_detailed_reference_with_uniform_decode_never_publishes_or_encodes(self):
        source = Image.new('RGB', (48, 64), 'black')
        source.paste('white', (0, 0, 24, 64)); source.save(self.source)
        scene = self.base / 'AI_jelenetek/jelenet_001'
        previous = scene / 'frames'; previous.mkdir(parents=True)
        old = previous / 'frame_00000.png'; old.write_bytes(b'previous completed frame')
        with self.assertRaisesRegex(RuntimeError, 'egyszínű képkockákat'):
            self.generate()
        self.assertEqual(old.read_bytes(), b'previous completed frame')
        self.assertEqual(self.encodes, [])
        self.assertFalse((scene / 'video.mp4').exists())
        pending = list(scene.glob('frames.pending_*'))
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(list(pending[0].glob('*.png'))), 5)
        self.assertTrue(self.saved.is_file(), 'The checkpoint must survive an invalid decode')
        report = json.loads((scene / 'frame_integrity.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['reason'], 'uniform_output_from_detailed_reference')
        recovery = json.loads((scene / 'decode_retry.json').read_text(encoding='utf-8'))
        self.assertFalse(recovery['frames_published'])
        self.assertFalse(recovery['encoded']); self.assertFalse(recovery['completed'])

    def test_successful_decode_records_checked_frame_integrity(self):
        video = self.generate()
        report = json.loads((video.parent / 'frame_integrity.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['checked_frames'], 5)
        with Image.open(video.parent / 'frames/frame_00000.png') as frame:
            self.assertEqual(report['expected_size'], list(frame.size))


if __name__ == '__main__': unittest.main(verbosity=2)
