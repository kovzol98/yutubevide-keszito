"""CPU-only integration: a reset/nonfinite result cannot become a finished clip."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source'), str(ROOT / 'work/v2_tests')]
import gpu_health
from frame_integrity import FrameIntegrityError
from PIL import Image
import test_comfy_recovery as fixtures


class ComfyGpuHealthIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Composition reuses the real history/recovery loop fixture without
        # collecting a second copy of its existing tests.
        self.f = fixtures.GenerateRetryIntegrationTests(methodName='runTest')
        self.f.setUpClass()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.guard = Mock()
        self.guard.available = True
        self.scene = self.f.base / 'AI_jelenetek/jelenet_001'

    def reset_error(self):
        return gpu_health.GpuResetError(gpu_health.ResetEvent(
            3477249, datetime(2026, 10, 21, 6, 29, 17, tzinfo=timezone.utc)))

    def success_history(self):
        original = self.f.request
        def request(base, route, payload=None, timeout=15):
            if route == '/history/original':
                self.f.histories.append('original')
                return {'original': {'status': {'status_str': 'success', 'completed': True},
                    'outputs': {'10': {'images': [
                        {'filename': 'frame_%05d.png' % i, 'subfolder': 'YouTubeMuhely', 'type': 'output'}
                        for i in range(5)]}}}}
            return original(base, route, payload, timeout)
        self.f.request = request

    def assert_no_result(self, recovery):
        self.assertEqual(len(self.f.submitted), 1)
        self.assertEqual(self.f.downloads, [])
        self.assertEqual(self.f.encodes, [])
        self.assertFalse((self.scene / 'video.mp4').exists())
        self.assertFalse((self.scene / 'frames').exists())
        self.assertFalse((self.scene / 'decode_retry.json').exists())
        recovery.assert_not_called()
        self.assertEqual(self.f.deletions, ['original'])

    def test_reset_during_progress_stops_before_history_and_exports(self):
        self.guard.check.side_effect = self.reset_error()
        with patch('gpu_health.begin_monitor', return_value=self.guard) as begin, \
                patch('comfy_recovery.decode_recovery') as recovery:
            with self.assertRaises(gpu_health.GpuResetError):
                self.f.generate()
        self.assertEqual(self.f.histories, [])
        self.assert_no_result(recovery)
        self.assertEqual(begin.call_args.kwargs['report_path'], self.scene / 'gpu_health.json')
        self.assertIs(begin.call_args.kwargs['cancel'].__self__, self.f.engine)

    def test_final_reset_check_rejects_history_success_before_downloading(self):
        self.success_history()
        def check(*, force=False):
            if force:
                raise self.reset_error()
        self.guard.check.side_effect = check
        with patch('gpu_health.begin_monitor', return_value=self.guard), \
                patch('comfy_recovery.decode_recovery') as recovery:
            with self.assertRaises(gpu_health.GpuResetError):
                self.f.generate()
        self.assertEqual(self.f.histories, ['original'])
        self.assert_no_result(recovery)
        self.assertTrue(any(call.kwargs.get('force') for call in self.guard.check.call_args_list))

    def test_reset_after_decode_oom_prevents_saved_latent_recovery(self):
        def check(*, force=False):
            if force:
                raise self.reset_error()
        self.guard.check.side_effect = check
        with patch('gpu_health.begin_monitor', return_value=self.guard), \
                patch('comfy_recovery.decode_recovery') as recovery:
            with self.assertRaises(gpu_health.GpuResetError):
                self.f.generate()
        self.assert_no_result(recovery)
        self.assertTrue(self.f.saved.is_file())

    def test_nonfinite_sampler_marker_is_distinct_fatal_error_and_never_retries(self):
        original = self.f.request
        def request(base, route, payload=None, timeout=15):
            result = original(base, route, payload, timeout)
            if route == '/history/original':
                result = copy.deepcopy(result)
                detail = result['original']['status']['messages'][0][1]
                detail.update(node_id='7', node_type='YouTubeWan14RamSampler',
                              exception_message='YTMUHELY_INVALID_GPU_RESULT: latent has NaN values')
            return result
        self.f.request = request
        with patch('gpu_health.begin_monitor', return_value=self.guard), \
                patch('comfy_recovery.decode_recovery') as recovery:
            with self.assertRaises(gpu_health.GpuResultError) as caught:
                self.f.generate()
        self.assertTrue(caught.exception.halt_queue)
        self.assert_no_result(recovery)

    def unavailable_monitor(self, **options):
        return gpu_health.begin_monitor(reader=lambda start, end: None, platform='nt', **options)

    def test_unavailable_event_log_keeps_real_frame_guard_and_rejects_uniform_result(self):
        self.success_history()
        photo = Image.new('RGB', (48, 64), 'black')
        photo.paste('white', (0, 0, 24, 64))
        photo.save(self.f.source)
        factory = gpu_health.begin_monitor
        with patch('gpu_health.begin_monitor', side_effect=lambda **options:
                   factory(reader=lambda start, end: None, platform='nt', **options)):
            with self.assertRaises(FrameIntegrityError):
                self.f.generate()
        self.assertEqual(len(self.f.downloads), 5)
        self.assertEqual(self.f.encodes, [])
        self.assertFalse((self.scene / 'video.mp4').exists())
        self.assertFalse((self.scene / 'frames').exists())
        self.assertEqual(json.loads((self.scene / 'frame_integrity.json').read_text(encoding='utf-8'))['status'], 'failed')
        self.assertEqual(json.loads((self.scene / 'gpu_health.json').read_text(encoding='utf-8'))['status'], 'unavailable')

    def test_unavailable_event_log_allows_validated_good_result(self):
        self.success_history()
        factory = gpu_health.begin_monitor
        with patch('gpu_health.begin_monitor', side_effect=lambda **options:
                   factory(reader=lambda start, end: None, platform='nt', **options)):
            video = self.f.generate()
        self.assertTrue(video.is_file())
        self.assertEqual(len(self.f.encodes), 1)
        self.assertEqual(json.loads((self.scene / 'frame_integrity.json').read_text(encoding='utf-8'))['status'], 'passed')

    def test_cancellation_is_not_replaced_by_later_gpu_check(self):
        self.success_history()
        self.guard.check.side_effect = [None, fixtures.app.Cancelled('User cancellation'), self.reset_error()]
        with patch('gpu_health.begin_monitor', return_value=self.guard):
            with self.assertRaisesRegex(fixtures.app.Cancelled, 'User cancellation'):
                self.f.generate()
        self.assertEqual(self.guard.check.call_count, 2)
        self.assertEqual(self.f.encodes, [])
        self.assertFalse((self.scene / 'video.mp4').exists())


if __name__ == '__main__':
    unittest.main()
