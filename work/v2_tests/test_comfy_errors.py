"""CPU-s regresszió: a hibadialógusban nem jelenhetnek meg a képtensorok."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))

from comfy_errors import ComfyExecutionError, ComfyOutOfMemoryError, execution_error, has_invalid_gpu_result


def status_with_error(**changes):
    error = {
        'exception_type': 'torch.OutOfMemoryError',
        'exception_message': ('HIP out of memory. Tried to allocate 71.08 GiB. '
                              'GPU 0 has a total capacity of 15.92 GiB of which 12.0 GiB is free.'),
        'node_id': '9', 'node_type': 'VAEDecodeTiled',
        'current_inputs': {'samples': 'tensor(' + ('[0., 0., 0.],\\n' * 10000) + ')'},
        'current_outputs': {'15': 'OUTPUT_SENTINEL' + '0.' * 10000},
        'traceback': ['TRACEBACK_SENTINEL'],
    }
    error.update(changes)
    return {'status_str': 'error', 'completed': False, 'messages': [
        ['execution_start', {'prompt_id': 'own'}], ['execution_error', error]]}


class ComfyErrorTests(unittest.TestCase):
    def test_actual_gpu_oom_is_short_and_stops_queue(self):
        status = status_with_error()
        error = execution_error(status, 'C:/project/history.json')
        self.assertIsInstance(error, ComfyOutOfMemoryError)
        self.assertIs(error.halt_queue, True)
        self.assertIn('videomemória', str(error))
        self.assertIn('71.08 GiB', str(error))
        self.assertIn('15.92 GiB', str(error))
        self.assertIn('9 / VAEDecodeTiled', str(error))
        self.assertTrue(str(error).endswith('C:/project/history.json'))
        self.assertLess(len(str(error)), 600)
        for hidden in ('tensor(', 'OUTPUT_SENTINEL', 'TRACEBACK_SENTINEL', 'current_inputs'):
            self.assertNotIn(hidden, str(error))
        self.assertIn('tensor(', status['messages'][1][1]['current_inputs']['samples'])

    def test_runtime_cuda_oom_is_fatal_too(self):
        error = execution_error(status_with_error(exception_type='RuntimeError',
            exception_message='CUDA out of memory. Tried to allocate 256 MiB'), 'history.json')
        self.assertIsInstance(error, ComfyOutOfMemoryError)
        self.assertIn('256 MiB', str(error))

    def test_cpu_oom_does_not_claim_gpu_memory(self):
        error = execution_error(status_with_error(exception_type='OutOfMemoryError',
            exception_message='DefaultCPUAllocator: not enough memory'), 'history.json')
        self.assertIsInstance(error, ComfyOutOfMemoryError)
        self.assertNotIn('videomemória', str(error))

    def test_generic_failure_has_type_and_first_line_without_tensors(self):
        error = execution_error(status_with_error(exception_type='RuntimeError',
            exception_message='Tensor size mismatch\ntensor(SECOND_LINE_SENTINEL)'), 'history.json')
        self.assertIsInstance(error, ComfyExecutionError)
        self.assertFalse(getattr(error, 'halt_queue', False))
        self.assertIn('RuntimeError: Tensor size mismatch', str(error))
        self.assertNotIn('SECOND_LINE_SENTINEL', str(error))

    def test_long_message_is_bounded_and_log_path_is_retained(self):
        error = execution_error(status_with_error(exception_type='RuntimeError',
            exception_message='word ' * 10000, node_type='name' * 1000), 'C:/project/history.json')
        body, path = str(error).split('\nRészletek: ')
        self.assertLessEqual(len(body), 600)
        self.assertEqual(path, 'C:/project/history.json')
        self.assertIn('…', body)

    def test_malformed_status_does_not_dump_arbitrary_objects(self):
        for malformed in (None, [], 'secret', {}, {'messages': 'secret'},
                          {'messages': [None, [], 'secret', ['execution_error', []]]},
                          {'messages': [['execution_error', {'exception_type': ['secret'],
                              'exception_message': {'secret': 'tensor'}, 'node_type': {'secret': 'tensor'}}]]}):
            with self.subTest(status=type(malformed).__name__):
                message = str(execution_error(malformed, 'history.json'))
                self.assertIn('nem tudta elkészíteni', message)
                self.assertNotIn('secret', message)
                self.assertNotIn('tensor', message)

    def test_unrelated_event_payload_is_ignored(self):
        status = {'messages': [['execution_cached', {'exception_type': 'OutOfMemoryError',
            'exception_message': 'HIP out of memory'}]]}
        self.assertNotIsInstance(execution_error(status, 'history.json'), ComfyOutOfMemoryError)

    def test_execution_interrupted_is_short_without_claiming_oom(self):
        error = execution_error({'messages': [['execution_interrupted', {'current_inputs': 'tensor'}]]},
                                'history.json')
        self.assertIn('megszakította', str(error))
        self.assertFalse(getattr(error, 'halt_queue', False))

    def test_invalid_gpu_marker_only_comes_from_exception_message(self):
        status = status_with_error()
        error = status['messages'][1][1]
        error['current_inputs']['prompt'] = 'YTMUHELY_INVALID_GPU_RESULT'
        error['current_outputs']['result'] = 'YTMUHELY_INVALID_GPU_RESULT'
        error['traceback'] = ['YTMUHELY_INVALID_GPU_RESULT']
        self.assertFalse(has_invalid_gpu_result(status))
        error['exception_message'] = 'YTMUHELY_INVALID_GPU_RESULT: latent has NaN values'
        self.assertTrue(has_invalid_gpu_result(status))

    def test_invalid_gpu_marker_checks_all_execution_errors_and_ignores_other_events(self):
        status = {'messages': [['execution_error', {'exception_message': 'YTMUHELY_INVALID_GPU_RESULT'}],
                              ['execution_error', {'exception_message': 'unrelated later error'}]]}
        self.assertTrue(has_invalid_gpu_result(status))
        for malformed in (None, [], {'messages': None}, {'messages': [[], None]},
                {'messages': [['execution_start', {'exception_message': 'YTMUHELY_INVALID_GPU_RESULT'}]]},
                {'messages': [['execution_error', {'exception_message': ['YTMUHELY_INVALID_GPU_RESULT']}]]}):
            with self.subTest(status=malformed):
                self.assertFalse(has_invalid_gpu_result(malformed))

    def test_invalid_gpu_marker_search_is_bounded_with_large_message(self):
        marker = 'YTMUHELY_INVALID_GPU_RESULT'
        status = status_with_error(exception_message=marker + 'x' * 1000000)
        self.assertTrue(has_invalid_gpu_result(status))
        status = status_with_error(exception_message='x' * 2048 + marker)
        self.assertFalse(has_invalid_gpu_result(status))
        status = status_with_error(exception_message='x' * (2048 - len(marker)) + marker)
        self.assertTrue(has_invalid_gpu_result(status))
        status = status_with_error(exception_message='x' * (2049 - len(marker)) + marker)
        self.assertFalse(has_invalid_gpu_result(status))


class ComfyErrorIntegrationTests(unittest.TestCase):
    def test_full_history_is_saved_but_dialog_and_queue_receive_short_oom(self):
        # A valódi generáló hibaágat használjuk, minden GPU-/hálózati művelet hamis.
        import test_comfy_recovery as fixtures
        fixture = fixtures.GenerateRetryIntegrationTests(methodName='runTest')
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = fixture.request
        def request(base, route, payload=None, timeout=15):
            result = original(base, route, payload, timeout)
            if route == '/history/original':
                result['original']['status'] = status_with_error()
                result['original']['status']['messages'][1][1]['current_inputs']['prompt'] = 'YTMUHELY_INVALID_GPU_RESULT'
            return result
        fixture.request = request
        guard = Mock()
        with patch('gpu_health.begin_monitor', return_value=guard), \
                patch('comfy_recovery.decode_recovery', return_value=None):
            with self.assertRaises(ComfyOutOfMemoryError) as caught:
                fixture.generate()
        self.assertTrue(caught.exception.halt_queue)
        self.assertLess(len(str(caught.exception)), 1000)
        self.assertNotIn('tensor(', str(caught.exception))
        history_path = fixture.base / 'AI_jelenetek/jelenet_001/history.json'
        saved = json.loads(history_path.read_text(encoding='utf-8'))
        expected = status_with_error()['messages'][1][1]['current_inputs']
        expected['prompt'] = 'YTMUHELY_INVALID_GPU_RESULT'
        self.assertEqual(saved['status']['messages'][1][1]['current_inputs'], expected)
        self.assertIn(str(history_path), str(caught.exception))
        self.assertEqual(len(fixture.submitted), 1)
        self.assertEqual(fixture.downloads, [])
        self.assertEqual(fixture.encodes, [])

    def test_second_decoder_oom_stops_queue_without_a_third_submission(self):
        import test_comfy_recovery as fixtures
        fixture = fixtures.GenerateRetryIntegrationTests(methodName='runTest')
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.retry_failure = True
        with patch('gpu_health.begin_monitor', return_value=Mock()):
            with self.assertRaises(ComfyOutOfMemoryError) as caught:
                fixture.generate()
        self.assertTrue(caught.exception.halt_queue)
        self.assertIn('egyszeri helyreállítása nem sikerült', str(caught.exception))
        self.assertIn('videomemória', str(caught.exception))
        self.assertEqual(len(fixture.submitted), 2)
        self.assertEqual(fixture.histories, ['original', 'retry'])
        self.assertEqual(fixture.deletions, ['retry'])
        self.assertEqual(fixture.downloads, [])
        self.assertEqual(fixture.encodes, [])
        self.assertTrue(fixture.saved.is_file())
        saved = json.loads((fixture.base / 'AI_jelenetek/jelenet_001/history.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status']['messages'][0][1]['prompt_id'], 'retry')


if __name__ == '__main__':
    unittest.main()
