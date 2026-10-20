"""Actual Tk queue controls, imports and main-window busy ownership checks."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

import render_queue_ui as ui


class FakeStore:
    def __init__(self):
        self.jobs = []
        self.added_bases = []

    def items(self):
        return copy.deepcopy(self.jobs)

    def add(self, project, base=None):
        if not isinstance(project, dict) or not project.get('title'):
            raise ValueError('Érvénytelen projekt.')
        job = {'id': str(len(self.jobs)), 'title': project['title'],
               'project': copy.deepcopy(project), 'status': 'pending',
               'progress': 0, 'message': '', 'result': '', 'error': ''}
        self.jobs.append(job)
        self.added_bases.append(base)
        return copy.deepcopy(job)


class FakeRunner:
    def __init__(self):
        self.is_running = False
        self.active_engine = None
        self.last_error = ''
        self.starts = self.stops = self.cancels = 0
        self.start_result = True
        self.start_error = None

    def start(self):
        self.starts += 1
        if self.start_error:
            raise self.start_error
        self.is_running = self.start_result
        return self.start_result

    def stop_after_current(self):
        self.stops += 1

    def cancel_current(self):
        self.cancels += 1


class QueueUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest('A tényleges Tk felület nem érhető el: ' + str(error))
        self.root.withdraw()
        self.root.attributes('-alpha', 0)
        ttk.Style(self.root).theme_use('clam')
        self.parent = ROOT / 'work/v2_tests/queue_ui_fixtures'
        self.folder = self.parent / uuid.uuid4().hex[:10]
        self.folder.mkdir(parents=True)
        self.store = FakeStore()
        self.runner = FakeRunner()
        self.guard_calls = []
        self.failed_calls = []
        self.guard_result = True
        self.window = ui.show(self.root, self.store, self.runner,
            get_project=lambda: {'title': 'Aktuális', 'images': ['photo.jpg']},
            on_start=lambda: self.guard_calls.append(True) or self.guard_result,
            on_start_failed=lambda: self.failed_calls.append(True), hidden=True)
        self.view = self.window.queue_view
        self.window.geometry('900x620')
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'root'):
            self.root.destroy()
        if hasattr(self, 'folder') and self.folder.resolve().parent == self.parent.resolve():
            shutil.rmtree(self.folder)

    def test_snapshot_addition_and_running_addition(self):
        self.view.add_button.invoke()
        self.assertEqual(self.store.jobs[0]['title'], 'Aktuális')
        self.assertEqual(self.view.tree.selection(), ('0',))
        self.runner.is_running = True
        self.view.refresh()
        self.view.add_button.invoke()
        self.assertEqual(len(self.store.jobs), 2)
        self.assertFalse(self.view.add_button.instate(['disabled']))

    def test_import_partial_batch_and_relative_base(self):
        project = self.folder / 'saját projekt.json'
        project.write_text(json.dumps({'title': 'Mentett', 'images': ['photo.jpg']}), encoding='utf-8-sig')
        broken = self.folder / 'hibás.json'
        broken.write_text('not json', encoding='utf-8')
        array = self.folder / 'tömb.json'
        array.write_text('[]', encoding='utf-8')
        added, errors = self.view.add_project_files((project, broken, array))
        self.assertEqual(added, 1)
        self.assertEqual(len(errors), 2)
        self.assertEqual(self.store.added_bases, [self.folder.absolute()])
        self.assertEqual(self.store.jobs[0]['project']['images'], ['photo.jpg'])
        self.assertIn('2 fájl nem adható hozzá', self.view.status.cget('text'))

    def test_start_guard_rejects_main_render_conflict(self):
        self.guard_result = False
        self.view.start()
        self.assertEqual(self.guard_calls, [True])
        self.assertEqual(self.runner.starts, 0)
        self.assertEqual(self.failed_calls, [])

    def test_false_start_releases_main_busy_state(self):
        self.runner.start_result = False
        self.view.start()
        self.assertEqual(self.guard_calls, [True])
        self.assertEqual(self.failed_calls, [True])

    def test_start_failure_releases_main_busy_state(self):
        self.runner.start_error = OSError('A lemez megtelt.')
        self.view.start()
        self.assertEqual(self.failed_calls, [True])
        self.assertIn('A lemez megtelt.', self.view.status.cget('text'))

    def test_close_only_destroys_view_keeps_worker(self):
        self.runner.is_running = True
        self.view.close()
        self.root.update()
        self.assertTrue(self.runner.is_running)
        self.assertEqual(self.runner.stops, 0)
        self.assertEqual(self.runner.cancels, 0)
        self.assertTrue(self.view._destroyed)
        self.assertIsNone(self.view._after)

    def test_running_job_controls_and_fatal_disk_error(self):
        self.view.add_current()
        self.store.jobs[0]['status'] = 'running'
        self.runner.is_running = True
        self.view.refresh()
        self.assertTrue(self.view.remove_button.instate(['disabled']))
        self.assertTrue(self.view.retry_button.instate(['disabled']))
        self.assertTrue(self.view.start_button.instate(['disabled']))
        self.assertFalse(self.view.cancel_button.instate(['disabled']))
        self.runner.last_error = 'Nem menthető a munkasor.'
        self.runner.is_running = False
        self.view.refresh()
        self.assertIn(self.runner.last_error, self.view.status.cget('text'))

    def test_result_only_opens_finished_existing_file(self):
        self.view.add_current()
        video = self.folder / 'elkészült.mp4'
        video.write_bytes(b'fixture')
        self.store.jobs[0].update(status='complete', result=str(video))
        self.view.refresh()
        with patch.object(ui.os, 'startfile', create=True) as open_file:
            self.view.result_button.invoke()
            open_file.assert_called_once_with(str(video))
        video.unlink()
        with patch.object(ui.os, 'startfile', create=True) as open_file:
            self.view.open_result()
            open_file.assert_not_called()
        self.assertIn('már nem található', self.view.status.cget('text'))


if __name__ == '__main__':
    unittest.main()
