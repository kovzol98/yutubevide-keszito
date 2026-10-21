"""Fatal GPU/decoded-frame failures must preserve later jobs as pending."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
import render_queue as rq
import gpu_health
from frame_integrity import FrameIntegrityError
from ytmuhely import Project


class GpuQueueTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/v2_tests/gpu_queue_fixtures'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.source = self.folder / 'own_clip.mp4'
        self.source.write_bytes(b'owned input fixture, never decoded')
        self.path = self.folder / 'munkasor.json'
        self.store = rq.QueueStore(self.path)
        self.runner = None
        self.jobs_started = []
        self.events = []
        self.power = patch.object(rq, '_keep_awake', return_value=True)
        self.power.start()
        for title in ('A', 'B'):
            self.store.add(Project(title=title, visual_mode='Saját videók', videos=[str(self.source)],
                                   narration='Nincs narráció', output=str(self.folder / 'exports')))

    def tearDown(self):
        if self.runner is not None:
            self.runner.cancel_current()
            self.assertTrue(self.runner.join(5))
        self.store.close()
        self.power.stop()
        if self.folder.resolve().parent == self.parent.resolve():
            shutil.rmtree(self.folder)

    def assert_stops_queue(self, error):
        owner = self
        class Engine:
            def __init__(self, log, progress):
                self.cancel = threading.Event()
            def build(self, project):
                owner.jobs_started.append(project.title)
                raise error
        self.runner = rq.QueueRunner(self.store, Engine,
            lambda kind, data: self.events.append((kind, data)))
        self.assertTrue(self.runner.start())
        self.assertTrue(self.runner.join(5))
        first, second = self.store.items()
        self.assertEqual(self.jobs_started, ['A'])
        self.assertEqual([first['status'], second['status']], ['failed', 'pending'])
        self.assertEqual(first['error'], str(error))
        self.assertEqual(first['result'], '')
        self.assertEqual(second['progress'], 0)
        self.assertEqual(second['error'], '')
        self.assertEqual(second['result'], '')
        self.assertFalse(self.runner.is_running)
        self.assertIsNone(self.runner.active_engine)
        self.assertEqual(self.runner.last_error, str(error))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['jobs'], self.store.items())
        self.assertTrue(any(kind == 'error' for kind, _ in self.events))
        self.assertEqual(self.events[-1], ('done', {'stopped': True, 'error': str(error)}))

    def test_confirmed_driver_reset_stops_after_failed_first_job(self):
        event = gpu_health.ResetEvent(3477249, datetime(2026, 10, 21, 6, 29, 17, tzinfo=timezone.utc))
        self.assert_stops_queue(gpu_health.GpuResetError(event))

    def test_nonfinite_gpu_result_stops_after_failed_first_job(self):
        self.assert_stops_queue(gpu_health.GpuResultError())

    def test_uniform_or_corrupt_frame_failure_stops_after_failed_first_job(self):
        self.assert_stops_queue(FrameIntegrityError('Invalid generated frames'))


if __name__ == '__main__':
    unittest.main()
