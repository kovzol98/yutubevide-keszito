"""Explain startup failures without suppressing cancellation or old log data.

All server processes, clocks and network probes are fixtures. No ComfyUI server,
model, GPU, user media or external connection is used by these checks.
"""
from pathlib import Path
import json
import os
import shutil
import sys
from types import SimpleNamespace
import unittest
import urllib.error
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'work/packages'), str(ROOT/'work/v2_source')]
import comfy_video
import motor_diagnostic
import realistic_video
from ytmuhely import Cancelled


class ProbeDiagnosticTests(unittest.TestCase):
    def test_connection_failure_retains_original_error_for_diagnostics(self):
        detail = {}
        error = urllib.error.URLError('fixture connection unavailable')
        with patch.object(comfy_video, '_request', side_effect=error):
            self.assertFalse(comfy_video._probe('http://127.0.0.1:8199', diagnostics=detail))
        self.assertEqual(detail['error_type'], 'URLError')
        self.assertIn('fixture connection unavailable', detail['error'])

    def test_invalid_server_response_has_a_diagnostic_explanation(self):
        detail = {}
        with patch.object(comfy_video, '_request', return_value={'unexpected': True}):
            self.assertFalse(comfy_video._probe('http://127.0.0.1:8199', diagnostics=detail))
        self.assertTrue(detail.get('error_type'))
        self.assertTrue(detail.get('error'))

    def test_valid_server_response_still_becomes_ready(self):
        detail = {}
        with patch.object(comfy_video, '_request', return_value={'queue_running': [], 'queue_pending': []}) as request:
            self.assertTrue(comfy_video._probe('http://127.0.0.1:8199', diagnostics=detail))
        request.assert_called_once_with('http://127.0.0.1:8199', '/queue', timeout=2)

    def test_cancellation_is_not_converted_into_a_failed_probe(self):
        cancelled = Cancelled('fixture cancellation')
        with patch.object(comfy_video, '_request', side_effect=cancelled):
            with self.assertRaises(Cancelled) as caught:
                comfy_video._probe('http://127.0.0.1:8199', diagnostics={})
        self.assertIs(caught.exception, cancelled)


class StartupDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT/'work/temp'
        self.parent.mkdir(parents=True, exist_ok=True)
        self.root = self.parent/('startup_'+uuid.uuid4().hex)
        self.root.mkdir()
        self.engine = SimpleNamespace(check=Mock(), log=Mock())
        self.process = Mock()
        self.process.pid = 12345
        self.process.poll.return_value = None
        self.clock = [0.0]

    def tearDown(self):
        handle = getattr(self.engine, '_comfy_log_handle', None)
        if handle is not None:
            handle.close()
        assert self.root.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.root)

    def advance(self, seconds):
        # Reach the production timeout in a few fixture iterations.
        self.clock[0] += max(float(seconds), 90.0)

    def launch_patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(comfy_video, '_portable', return_value=self.root/'portable'))
        stack.enter_context(patch.object(comfy_video, '_free_port', return_value=8199))
        stack.enter_context(patch.object(comfy_video.subprocess, 'Popen', return_value=self.process))
        stack.enter_context(patch.object(comfy_video.time, 'monotonic', side_effect=lambda:self.clock[0]))
        stack.enter_context(patch.object(comfy_video.time, 'sleep', side_effect=self.advance))
        return stack

    @staticmethod
    def unavailable(base, diagnostics=None):
        if diagnostics is not None:
            diagnostics.update(error_type='URLError', error='fixture probe failed')
        return False

    def test_timeout_reports_last_probe_and_log_location(self):
        with self.launch_patches(), \
             patch.object(comfy_video, '_probe', side_effect=self.unavailable), \
             patch.object(comfy_video, 'cleanup'):
            with self.assertRaises(RuntimeError) as caught:
                comfy_video._ensure_server(self.engine, self.root, force_owned=True)
        self.assertIn('fixture probe failed', str(caught.exception))
        self.assertIn(str(self.root/'comfy_motor.log'), str(caught.exception))
        detail = getattr(self.engine, '_comfy_startup_diagnostics', None)
        self.assertIsInstance(detail, dict)
        self.assertTrue(detail)

    def test_exit_error_includes_only_the_new_startup_session(self):
        log = self.root/'comfy_motor.log'
        log.write_text('OLD_SESSION_PRIVATE_SENTINEL\n', encoding='utf-8')
        self.process.poll.return_value = 1

        def start(*args, **kwargs):
            kwargs['stdout'].write(b'NEW_STARTUP_FAILURE_SENTINEL\n')
            return self.process

        with self.launch_patches(), \
             patch.object(comfy_video.subprocess, 'Popen', side_effect=start), \
             patch.object(comfy_video, 'cleanup'):
            with self.assertRaises(RuntimeError) as caught:
                comfy_video._ensure_server(self.engine, self.root, force_owned=True)
        self.assertIn('NEW_STARTUP_FAILURE_SENTINEL', str(caught.exception))
        self.assertNotIn('OLD_SESSION_PRIVATE_SENTINEL', str(caught.exception))

    def test_cleanup_failure_does_not_replace_original_cancellation(self):
        cancelled = Cancelled('fixture user cancelled startup')

        def check():
            if getattr(self.engine, '_comfy_process', None) is not None:
                raise cancelled

        self.engine.check.side_effect = check
        with self.launch_patches(), \
             patch.object(comfy_video, '_probe', side_effect=self.unavailable), \
             patch.object(comfy_video, 'cleanup', side_effect=OSError('fixture secondary cleanup failure')):
            with self.assertRaises(Cancelled) as caught:
                comfy_video._ensure_server(self.engine, self.root, force_owned=True)
        self.assertIs(caught.exception, cancelled)

    def test_cleanup_failure_does_not_replace_timeout_diagnostic(self):
        with self.launch_patches(), \
             patch.object(comfy_video, '_probe', side_effect=self.unavailable), \
             patch.object(comfy_video, 'cleanup', side_effect=OSError('fixture secondary cleanup failure')):
            with self.assertRaises(RuntimeError) as caught:
                comfy_video._ensure_server(self.engine, self.root, force_owned=True)
        self.assertIn('fixture probe failed', str(caught.exception))
        self.assertNotIn('fixture secondary cleanup failure', str(caught.exception))


class DiagnosticOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT/'work/temp'
        self.parent.mkdir(parents=True, exist_ok=True)
        self.root = self.parent/('motor_ownership_'+uuid.uuid4().hex)
        self.root.mkdir()
        self.process = Mock()
        self.process.pid = 9876
        self.stopped = False
        self.process.poll.side_effect = lambda:0 if self.stopped else None
        self.process.terminate.side_effect = self.mark_stopped
        self.process.wait.return_value = 0

    def mark_stopped(self):
        self.stopped = True

    def tearDown(self):
        assert self.root.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.root)

    def diagnose(self, final_queue):
        request = self.root/'request.json'
        request.write_text(json.dumps({'ai_root':str(self.root), 'report':'result.json'}), encoding='utf-8')
        process = self.process
        self.engine = None

        def start(engine, root, **kwargs):
            self.engine = engine
            engine._comfy_process = process
            engine._comfy_url = 'http://127.0.0.1:8199'
            engine._comfy_root = str(root)
            return engine._comfy_url

        queue_calls = [0]

        def query(base, route, payload=None, timeout=15):
            if route == '/queue':
                queue_calls[0] += 1
                if queue_calls[0] == 1:
                    return {'queue_running': [], 'queue_pending': []}
                if isinstance(final_queue, BaseException):
                    raise final_queue
                return final_queue
            if route == '/system_stats':
                return {'system': {}}
            if route == '/object_info':
                return {name: {} for name in realistic_video.EXTRA_NODES}
            raise AssertionError('Unexpected local fixture route: ' + route)

        with patch.object(comfy_video, '_ensure_server', start), \
             patch.object(comfy_video, '_request', side_effect=query):
            result = motor_diagnostic.diagnose(request)
        self.assertEqual(queue_calls[0], 2)
        self.assertEqual(json.loads((self.root/'result.json').read_text(encoding='utf-8')), result)
        return result

    def test_owned_idle_server_is_stopped_after_successful_check(self):
        result = self.diagnose({'queue_running': [], 'queue_pending': []})
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['owned_process_stopped'])
        self.process.terminate.assert_called_once_with()

    def test_another_clients_new_task_is_preserved_and_reported(self):
        secret = 'fixture-private-proxy-password'
        with patch.dict(os.environ, {'HTTP_PROXY':'http://user:'+secret+'@proxy.invalid:1234'}):
            result = self.diagnose({'queue_running': [[0, 'other-client-task', {}]], 'queue_pending': []})
        self.assertEqual(result['status'], 'FAIL')
        self.assertFalse(result['owned_process_stopped'])
        self.assertTrue(result['owned_process_preserved'])
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()
        self.assertIsNone(self.engine._comfy_process)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn('proxy.invalid', json.dumps(result))

    def test_an_unreadable_queue_preserves_server_and_reports_failure(self):
        result = self.diagnose(urllib.error.URLError('fixture unreadable queue'))
        self.assertEqual(result['status'], 'FAIL')
        self.assertFalse(result['owned_process_stopped'])
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
