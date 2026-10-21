"""Cleanup failures cannot turn a fatal GPU result into an ordinary queue error."""
from contextlib import ExitStack
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source'), str(ROOT / 'work/v2_tests')]
import comfy_video
import gpu_health
import ytmuhely as app
import test_comfy_recovery as fixtures


class ComfyCleanupFailureTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GenerateRetryIntegrationTests(methodName='runTest')
        self.f.setUpClass(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.guard = Mock()

    def test_gpu_error_survives_hung_child_unlink_and_monitor_close_failures(self):
        primary = gpu_health.GpuResultError('Fatal invalid GPU result')
        self.guard.check.side_effect = primary
        monitor = Mock(); monitor.close.side_effect = OSError('monitor shutdown unavailable')
        self.f.progress = Mock(return_value=monitor)
        original_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.name == 'video.partial.mp4':
                raise PermissionError('partial file still held')
            return original_unlink(path, *args, **kwargs)
        with patch('gpu_health.begin_monitor', return_value=self.guard), \
                patch.object(comfy_video, '_cancel_prompt', side_effect=subprocess.TimeoutExpired('child', 8)), \
                patch.object(Path, 'unlink', unlink):
            with self.assertRaises(gpu_health.GpuResultError) as caught:
                self.f.generate()
        self.assertIs(caught.exception, primary)
        self.assertTrue(caught.exception.halt_queue)
        monitor.close.assert_called_once()
        self.assertEqual(self.f.encodes, [])

    def test_cancellation_survives_failed_child_cleanup(self):
        primary = app.Cancelled('User cancelled')
        self.guard.check.side_effect = primary
        with patch('gpu_health.begin_monitor', return_value=self.guard), \
                patch.object(comfy_video, '_cancel_prompt', side_effect=subprocess.TimeoutExpired('child', 8)):
            with self.assertRaises(app.Cancelled) as caught:
                self.f.generate()
        self.assertIs(caught.exception, primary)

    def test_monitor_close_failure_after_success_is_reported(self):
        original = self.f.request
        def request(base, route, payload=None, timeout=15):
            if route == '/history/original':
                return {'original': {'status': {'status_str': 'success', 'completed': True},
                    'outputs': {'10': {'images': [
                        {'filename': 'frame_%05d.png' % number, 'subfolder': 'YouTubeMuhely', 'type': 'output'}
                        for number in range(5)]}}}}
            return original(base, route, payload, timeout)
        self.f.request = request
        failure = OSError('monitor failed after otherwise successful render')
        monitor = Mock(); monitor.close.side_effect = failure
        self.f.progress = Mock(return_value=monitor)
        with patch('gpu_health.begin_monitor', return_value=self.guard):
            try:raise LookupError('A caller may already be handling another error')
            except LookupError:
                with self.assertRaises(OSError) as caught:
                    self.f.generate()
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.f.encodes), 1)


class EngineCleanupFailureTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/v2_tests/cleanup_failure_fixtures'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.logs = []
        self.engine = app.Engine(self.logs.append)
        self.project = app.Project(title='Cleanup safety', text='', video_prompt='A leaf moves.',
            narration='Nincs narráció', output=str(self.folder / 'exports'))

    def tearDown(self):
        assert self.folder.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.folder)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(comfy_video, 'engine_files'))
        stack.enter_context(patch('gpu_resources.probe_gpu_users', return_value=[]))
        return stack

    def test_gpu_error_survives_final_server_timeout_and_failed_partial_removal(self):
        primary = gpu_health.GpuResultError('Fatal invalid GPU result')
        def create(engine, folder, count):
            (folder / 'video.mp4').write_bytes(b'unfinished output')
            raise primary
        original_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.name == 'video.mp4':
                raise PermissionError('output is still held')
            return original_unlink(path, *args, **kwargs)
        with self.patches(), patch('scene_library.create', side_effect=create), \
                patch.object(comfy_video, 'stop_engine', side_effect=subprocess.TimeoutExpired('child', 8)), \
                patch.object(Path, 'unlink', unlink):
            with self.assertRaises(gpu_health.GpuResultError) as caught:
                self.engine.build(self.project)
        self.assertIs(caught.exception, primary)
        self.assertTrue(caught.exception.halt_queue)
        self.assertTrue(any('kimenet eltávolítása' in line for line in self.logs))
        self.assertTrue(any('AI-motor leállítása' in line for line in self.logs))

    def test_cancellation_survives_final_cleanup_error(self):
        primary = app.Cancelled('User cancelled rendering')
        with self.patches(), patch('scene_library.create', side_effect=primary), \
                patch.object(comfy_video, 'stop_engine', side_effect=PermissionError('child unavailable')):
            with self.assertRaises(app.Cancelled) as caught:
                self.engine.build(self.project)
        self.assertIs(caught.exception, primary)

    def test_keyboard_interrupt_survives_final_cleanup_error(self):
        primary = KeyboardInterrupt('User stopped rendering')
        with self.patches(), patch('scene_library.create', side_effect=primary), \
                patch.object(comfy_video, 'stop_engine', side_effect=PermissionError('child unavailable')):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.engine.build(self.project)
        self.assertIs(caught.exception, primary)

    def test_cleanup_error_after_otherwise_successful_build_is_reported(self):
        native = self.folder / 'native.mp4'; native.write_bytes(b'fake native clip')
        failure = subprocess.TimeoutExpired('child', 8)
        def run(arguments, cwd=None, **kwargs):
            target = Path(arguments[-1])
            if not target.is_absolute(): target = Path(cwd) / target
            target.write_bytes(b'local rendering fixture')
        def export(engine, source, destination, *args, **kwargs):
            Path(destination).write_bytes(b'finished export fixture')
        with self.patches(), ExitStack() as stack:
            stack.enter_context(patch('scene_library.create'))
            stack.enter_context(patch('scene_library.add_scene'))
            stack.enter_context(patch('scene_library.complete'))
            stack.enter_context(patch('local_resume.supported', return_value=False))
            stack.enter_context(patch.object(self.engine, 'codec', return_value=['-c:v', 'libx264']))
            stack.enter_context(patch.object(self.engine, 'generate_clip', return_value=native))
            stack.enter_context(patch.object(self.engine, 'run', side_effect=run))
            stack.enter_context(patch('soundtrack_cache.save_source'))
            stack.enter_context(patch('background_music.export_video', side_effect=export))
            stack.enter_context(patch.object(app, 'ffmpeg', return_value='fixture-ffmpeg'))
            stack.enter_context(patch.object(comfy_video, 'stop_engine', side_effect=failure))
            try:raise LookupError('A caller may already be handling another error')
            except LookupError:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    self.engine.build(self.project)
        self.assertIs(caught.exception, failure)


if __name__ == '__main__':
    unittest.main()
