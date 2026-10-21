"""Install the small bundled numerical guard without downloads or busy resets."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import comfy_video
import realistic_video
import ytmuhely as app


class FiniteNodeSyncTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/v2_tests/finite_node_sync_fixtures'
        self.folder = self.parent / uuid.uuid4().hex
        self.source = self.folder / 'bundle/tools/quality_nodes/__init__.py'
        self.source.parent.mkdir(parents=True)
        self.content = b'# bundled numerical guard\nGUARD_VERSION = 16\n'
        self.source.write_bytes(self.content)
        self.motor = self.folder / 'motor'
        self.target = self.motor / 'ComfyUI_windows_portable/ComfyUI/custom_nodes/YouTubeMuhely_RAM/__init__.py'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'# old unguarded node\n')
        self.logs = []
        self.engine = app.Engine(self.logs.append)
        self.process = Mock()
        self.process.poll.return_value = None

    def tearDown(self):
        assert self.folder.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.folder)

    def sync(self):
        with patch.object(realistic_video, 'asset_home', return_value=self.folder / 'bundle'), \
                patch('urllib.request.urlopen') as network:
            realistic_video.prepare_runtime(self.engine, self.motor)
            network.assert_not_called()

    def owned_server(self):
        self.engine._comfy_process = self.process
        self.engine._comfy_url = 'http://127.0.0.1:18190'

    def test_stale_small_node_is_replaced_exactly_without_model_download(self):
        model = self.motor / 'models/diffusion_models/existing_model.gguf'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'large model stands in for existing user model')
        before = hashlib.sha256(model.read_bytes()).hexdigest()
        self.sync()
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(), before)
        self.assertEqual(list(self.target.parent.glob('*.tmp')), [])
        self.assertTrue(any('nem kell újra letölteni' in line for line in self.logs))

    def test_matching_node_returns_without_a_rewrite_server_query_or_cleanup(self):
        self.target.write_bytes(self.content)
        stamp = self.target.stat().st_mtime_ns
        self.owned_server()
        with patch.object(comfy_video, '_request') as request, patch.object(comfy_video, 'cleanup') as cleanup:
            self.sync()
        request.assert_not_called()
        cleanup.assert_not_called()
        self.assertEqual(self.target.stat().st_mtime_ns, stamp)
        self.process.terminate.assert_not_called()

    def test_missing_node_is_installed_from_bundle(self):
        self.target.unlink()
        self.sync()
        self.assertEqual(self.target.read_bytes(), self.content)

    def test_busy_owned_server_is_never_interrupted_or_overwritten(self):
        self.owned_server()
        old = self.target.read_bytes()
        for queue in ({'queue_running': [[0, 'running', {}, {}]], 'queue_pending': []},
                      {'queue_running': [], 'queue_pending': [[0, 'queued', {}, {}]]}):
            with self.subTest(queue=queue), patch.object(comfy_video, '_request', return_value=queue), \
                    patch.object(comfy_video, 'cleanup') as cleanup:
                with self.assertRaisesRegex(RuntimeError, 'futó feladat'):
                    self.sync()
                cleanup.assert_not_called()
                self.assertEqual(self.target.read_bytes(), old)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_unproven_queue_does_not_reset_server_or_overwrite_node(self):
        self.owned_server()
        old = self.target.read_bytes()
        for queue in (None, {}, {'queue_running': []}):
            with self.subTest(queue=queue), patch.object(comfy_video, '_request', return_value=queue), \
                    patch.object(comfy_video, 'cleanup') as cleanup:
                with self.assertRaises(ValueError):
                    self.sync()
                cleanup.assert_not_called()
                self.assertEqual(self.target.read_bytes(), old)

    def test_idle_owned_server_is_cleaned_up_before_atomic_replacement(self):
        self.owned_server()
        events = []
        original = self.target.read_bytes()
        def queue(*args, **kwargs):
            events.append('queue')
            return {'queue_running': [], 'queue_pending': []}
        def cleanup(engine,terminate=True):
            events.append('cleanup')
            self.assertIs(engine, self.engine)
            self.assertEqual(self.target.read_bytes(), original)
            self.engine._comfy_process = None
            self.process.poll.return_value = 0
        with patch.object(comfy_video, '_request', side_effect=queue) as request, \
                patch.object(comfy_video, 'cleanup', side_effect=cleanup) as clean:
            self.sync()
        self.assertEqual(events, ['queue', 'queue', 'cleanup'])
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args == ('http://127.0.0.1:18190', '/queue')
                            and call.kwargs == {'timeout': 5} for call in request.call_args_list))
        clean.assert_called_once_with(self.engine, terminate=True)
        self.assertEqual(self.target.read_bytes(), self.content)

    def test_job_arriving_after_first_idle_check_preserves_running_server_and_module(self):
        self.owned_server()
        old = self.target.read_bytes()
        queues = [{'queue_running': [], 'queue_pending': []},
                  {'queue_running': [], 'queue_pending': [[0, 'new-client', {}, {}]]}]
        with patch.object(comfy_video, '_request', side_effect=queues), \
                patch.object(comfy_video, 'cleanup') as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'leállása nem igazolható'):
                self.sync()
        cleanup.assert_not_called()
        self.process.terminate.assert_not_called()
        self.assertEqual(self.target.read_bytes(), old)

    def test_changed_process_at_either_queue_check_is_never_stopped_or_overwritten(self):
        old = self.target.read_bytes()
        for switch_at in (1, 2):
            with self.subTest(switch_at=switch_at):
                self.owned_server()
                replacement = Mock(); replacement.poll.return_value = None
                queries = []
                def queue(*args, **kwargs):
                    queries.append(1)
                    if len(queries) == switch_at:
                        self.engine._comfy_process = replacement
                    return {'queue_running': [], 'queue_pending': []}
                with patch.object(comfy_video, '_request', side_effect=queue), \
                        patch.object(comfy_video, 'cleanup') as cleanup:
                    with self.assertRaises(RuntimeError):
                        self.sync()
                cleanup.assert_not_called()
                self.process.terminate.assert_not_called()
                replacement.terminate.assert_not_called()
                self.assertIs(self.engine._comfy_process, replacement)
                self.assertEqual(self.target.read_bytes(), old)

    def test_copy_failure_preserves_previous_node_and_leaves_no_partial_module(self):
        old = self.target.read_bytes()
        original = OSError('disk write unavailable')
        with patch.object(realistic_video.shutil, 'copyfile', side_effect=original):
            with self.assertRaises(OSError) as caught:
                self.sync()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(list(self.target.parent.glob('*.tmp')), [])

    def test_cancellation_before_copy_preserves_the_installed_module(self):
        old = self.target.read_bytes()
        self.engine.cancel.set()
        with self.assertRaises(app.Cancelled):
            self.sync()
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(list(self.target.parent.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
