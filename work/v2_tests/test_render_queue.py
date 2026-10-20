"""Queue persistence, process ownership and sequential worker contracts."""
from copy import deepcopy
from dataclasses import asdict, make_dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

import render_queue as rq
from ytmuhely import Cancelled, Project


class FakeEngine:
    def __init__(self, log, progress, *, root, record, gate=None, fail=False,
                 missing=False, cancel_itself=False, assertion=None):
        self.log, self.progress = log, progress
        self.cancel = threading.Event()
        self.root, self.record, self.gate = root, record, gate
        self.fail, self.missing = fail, missing
        self.cancel_itself, self.assertion = cancel_itself, assertion

    def build(self, project):
        self.record.append(('start', project.title))
        if self.assertion:
            self.assertion(project)
        self.log('Jelenet készül: ' + project.title)
        self.progress(22.2)
        self.progress(22.2)
        self.progress(float('nan'))
        self.progress(-10)
        self.progress(200)
        if self.gate is not None:
            while not self.gate.wait(.01):
                if self.cancel.is_set():
                    raise Cancelled('Megszakítva.')
        if self.cancel_itself:
            self.cancel.set()
        if self.cancel.is_set():
            raise Cancelled('Megszakítva.')
        if self.fail:
            raise RuntimeError('Próbahiba')
        target = self.root / uuid.uuid4().hex
        target.mkdir()
        if not self.missing:
            (target / 'video.mp4').write_bytes(b'test video output')
        self.record.append(('finish', project.title))
        return target


class RenderQueueTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/v2_tests/queue_fixtures'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'munkasor.json'
        self.source = self.folder / 'input.mp4'
        self.source.write_bytes(b'input video')
        self.stores, self.runners, self.gates = [], [], []
        self.record, self.events = [], []
        self.power_patch = patch.object(rq, '_keep_awake', return_value=True)
        self.power_patch.start()

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for runner in self.runners:
            runner.cancel_current()
            self.assertTrue(runner.join(5), 'A munkasor tesztje beragadt.')
        for store in self.stores:
            store.close()
        self.power_patch.stop()
        if self.folder.resolve().parent == self.parent.resolve():
            shutil.rmtree(self.folder)

    def project(self, title='Próba', **changes):
        return Project(**dict(dict(title=title, visual_mode='Saját videók',
                    videos=[str(self.source)], narration='Nincs narráció',
                    output=str(self.folder / 'exports')), **changes))

    def store(self, path=None):
        result = rq.QueueStore(path or self.path)
        self.stores.append(result)
        return result

    def runner(self, store, **options):
        factory = options.pop('factory', None)
        events = options.pop('on_event', lambda kind, data: self.events.append((kind, data)))
        if factory is None:
            factory = lambda log, progress: FakeEngine(log, progress, root=self.folder,
                                                       record=self.record, **options)
        result = rq.QueueRunner(store, factory, events)
        self.runners.append(result)
        return result

    def gate(self):
        result = threading.Event()
        self.gates.append(result)
        return result

    def wait_running(self, runner):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if runner.active_engine is not None:
                return runner.active_engine
            time.sleep(.005)
        self.fail('A próba feldolgozó nem indult el.')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_snapshot_is_detached_and_relative_paths_are_frozen(self):
        store = self.store()
        original = asdict(self.project())
        original['videos'] = ['input.mp4']
        original['output'] = 'exports'
        added = store.add(original, base=self.folder)
        original['videos'].clear()
        added['project']['videos'].clear()
        added['status'] = 'complete'
        snapshot = store.items()
        self.assertEqual(snapshot[0]['status'], 'pending')
        self.assertEqual(snapshot[0]['project']['videos'], [str(self.source)])
        self.assertEqual(snapshot[0]['project']['output'], str(self.folder / 'exports'))
        snapshot[0]['project']['videos'].clear()
        self.assertEqual(len(store.items()[0]['project']['videos']), 1)
        self.assertEqual(self.read()['schema'], 1)
        self.assertEqual(set(store.items()[0]), rq.JOB_FIELDS)

    def test_saved_queue_can_be_reopened_and_owns_immutable_settings(self):
        store = self.store()
        first, second = store.add(self.project('Első')), store.add(self.project('Második'))
        store.move(second['id'], -1)
        store.close()
        reopened = self.store()
        self.assertEqual([job['id'] for job in reopened.items()], [second['id'], first['id']])
        self.assertEqual(reopened.items()[0]['project']['narration'], 'Nincs narráció')

    def test_frozen_main_project_dataclass_is_accepted_but_extra_fields_are_not(self):
        store = self.store()
        data = asdict(self.project())
        duplicate = make_dataclass('Project', [(name, type(value)) for name, value in data.items()])
        duplicate.__module__ = '__main__'
        item = store.add(duplicate(**data))
        self.assertEqual(item['project'], data)
        credential = make_dataclass('UnsafeProject', [(name, type(value)) for name, value in data.items()]
                                   + [('api_key', str)])
        with self.assertRaises(ValueError):
            store.add(credential(**data, api_key='not allowed'))

    def test_rejects_paid_and_internet_voice_jobs_without_saving(self):
        store = self.store()
        for changes in ({'visual_mode': 'Grok videó • internet'},
                        {'narration': 'Angol női • internet'}, {'narration': 'Angol férfi • internet'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.add(self.project(**changes))
        self.assertEqual(store.items(), [])
        self.assertFalse(self.path.exists())

    def test_rejects_unknown_and_credential_fields(self):
        store = self.store()
        for name in ('api_key', 'grok_api_key', 'XAI_API_KEY', 'extra'):
            data = asdict(self.project())
            data[name] = 'unwanted secret'
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.add(data)
        self.assertFalse(self.path.exists())

    def test_local_ai_rejects_remote_or_authenticated_comfy(self):
        store = self.store()
        for url in ('https://example.com', 'http://localhost.evil.test:8188',
                    'http://user:secret@localhost:8188', 'http://localhost:8188?key=secret',
                    'file:///tmp/video', 'http://localhost:bad'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                store.add(self.project(visual_mode='AI-videó • helyi', text='A scene', comfy_url=url))

    def test_accepts_all_free_modes_and_offline_narrations(self):
        store = self.store()
        for mode in ('AI-videó • helyi', 'AI-videó • helyi Wan', 'Saját videók', 'Képes diavideó'):
            for narration in ('Angol női LJ • offline', 'Nincs narráció'):
                job = store.add(self.project(visual_mode=mode, text='A scene', narration=narration))
                self.assertEqual(job['status'], 'pending')
        self.assertEqual(len(store.items()), 8)
        self.assertEqual(self.record, [])

    def test_basic_validation_missing_files_numeric_and_empty_work(self):
        store = self.store()
        empty = self.folder / 'empty.mp4'
        empty.touch()
        cases = ({'videos': [str(self.folder / 'absent.mp4')]}, {'videos': [str(empty)]},
                 {'videos': []}, {'seconds': float('nan')}, {'seconds': True},
                 {'music': str(self.folder / 'absent.wav')},
                 {'narration': 'Saját hangfájl', 'audio': str(self.folder / 'absent.wav')},
                 {'visual_mode': 'AI-videó • helyi'}, {'visual_mode': 'Képes diavideó'},
                 {'output': str(self.source)})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.add(self.project(**changes))
        self.assertFalse(self.path.exists())

    def test_missing_sources_do_not_prevent_opening_saved_queue(self):
        store = self.store()
        store.add(self.project())
        store.close()
        self.source.unlink()
        reopened = self.store()
        self.assertEqual(reopened.items()[0]['status'], 'pending')

    def test_running_is_recovered_as_interrupted_without_rendering(self):
        store = self.store()
        job = store.add(self.project())
        store.close()
        data = self.read()
        data['jobs'][0].update(status='running', progress=42, message='régi munka')
        self.write(data)
        with patch.object(rq.QueueRunner, 'start', side_effect=AssertionError('Nem indulhat el automatikusan')):
            reopened = self.store()
        item = reopened.items()[0]
        self.assertEqual(item['id'], job['id'])
        self.assertEqual(item['status'], 'interrupted')
        self.assertEqual(item['progress'], 42)
        self.assertEqual(self.read()['jobs'][0]['status'], 'interrupted')
        reopened.retry(item['id'])
        self.assertEqual(reopened.items()[0]['status'], 'pending')
        self.assertEqual(reopened.items()[0]['progress'], 0)

    def test_retry_is_explicit_and_limited_to_terminal_failures(self):
        store = self.store()
        job = store.add(self.project())
        with self.assertRaises(ValueError):
            store.retry(job['id'])
        for status in ('failed', 'cancelled', 'interrupted'):
            store._update(job['id'], status=status, error='hiba', result='régi', progress=33)
            retried = store.retry(job['id'])
            self.assertEqual((retried['status'], retried['error'], retried['result'], retried['progress']),
                             ('pending', '', '', 0))
        store._update(job['id'], status='complete')
        with self.assertRaises(ValueError):
            store.retry(job['id'])

    def test_second_store_and_process_cannot_own_same_queue(self):
        store = self.store()
        with self.assertRaises(rq.QueueError):
            rq.QueueStore(self.path)
        script = "import sys;sys.path[:0]=sys.argv[2:];from render_queue import QueueStore,QueueError\ntry:s=QueueStore(sys.argv[1])\nexcept QueueError:sys.exit(23)\ns.close();sys.exit(0)"
        args = [sys.executable, '-X', 'utf8', '-B', '-c', script, str(self.path),
                str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
        process = subprocess.run(args, capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(process.returncode, 23, process.stderr.decode('utf-8', errors='replace'))
        store.close()
        process = subprocess.run(args, capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))

    def test_unclean_process_exit_releases_lock_and_recovers_running_state(self):
        script = "import sys,os,json;sys.path[:0]=sys.argv[3:];from render_queue import QueueStore; s=QueueStore(sys.argv[1]);j=s.add(json.loads(sys.argv[2]));s._update(j['id'],status='running',progress=37);os._exit(0)"
        process = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script,
                                  str(self.path), json.dumps(asdict(self.project())),
                                  str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')],
                                 capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(self.read()['jobs'][0]['status'], 'running')
        recovered = self.store()
        self.assertEqual(recovered.items()[0]['status'], 'interrupted')
        self.assertEqual(recovered.items()[0]['progress'], 37)
        self.assertEqual(self.record, [])

    def test_concurrent_additions_never_lose_or_duplicate_jobs(self):
        store = self.store()
        errors = []
        def add(index):
            try:
                store.add(self.project('Párhuzamos ' + str(index)))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=add, args=(index,)) for index in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        jobs = store.items()
        self.assertEqual(len(jobs), 16)
        self.assertEqual(len({job['id'] for job in jobs}), 16)
        self.assertEqual({job['title'] for job in jobs}, {'Párhuzamos ' + str(index) for index in range(16)})
        self.assertEqual(self.read()['jobs'], jobs)

    def test_corrupt_file_is_refused_without_overwrite_and_lock_is_released(self):
        for raw in (b'{broken', b'{"schema":1,"schema":1,"jobs":[]}',
                    b'{"schema":99,"jobs":[]}', b'{"schema":1,"jobs":[{}]}',
                    b'{"schema":1,"jobs":[],"api_key":"secret"}'):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(rq.QueueError):
                rq.QueueStore(self.path)
            self.assertEqual(self.path.read_bytes(), raw)
            self.path.write_text('{"schema":1,"jobs":[]}', encoding='utf-8')
            store = self.store()
            store.close()

    def test_invalid_saved_project_and_duplicate_ids_are_refused(self):
        store = self.store()
        store.add(self.project())
        good = self.read()
        store.close()
        cases = []
        data = deepcopy(good); data['jobs'].append(deepcopy(data['jobs'][0])); cases.append(data)
        data = deepcopy(good); data['jobs'][0]['project']['api_key'] = 'secret'; cases.append(data)
        data = deepcopy(good); data['jobs'][0]['status'] = ['pending']; cases.append(data)
        data = deepcopy(good); data['jobs'][0]['project']['visual_mode'] = 'Grok videó • internet'; cases.append(data)
        data = deepcopy(good); data['jobs'][0]['project']['videos'] = ['input.mp4']; cases.append(data)
        data = deepcopy(good); data['jobs'][0]['unexpected'] = True; cases.append(data)
        for data in cases:
            self.write(data)
            before = self.path.read_bytes()
            with self.subTest(data=data), self.assertRaises(rq.QueueError):
                rq.QueueStore(self.path)
            self.assertEqual(self.path.read_bytes(), before)

    def test_mutations_are_transactional_when_save_fails(self):
        store = self.store()
        first, second = store.add(self.project('A')), store.add(self.project('B'))
        store._update(first['id'], status='failed')
        operations = (lambda: store.add(self.project('C')), lambda: store.remove(first['id']),
                      lambda: store.move(second['id'], -1), lambda: store.retry(first['id']),
                      lambda: store._update(second['id'], status='running'))
        for operation in operations:
            before, raw = store.items(), self.path.read_bytes()
            with patch.object(store, '_write_atomic', side_effect=OSError('Tele a lemez')):
                with self.assertRaises(OSError):
                    operation()
            self.assertEqual(store.items(), before)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_atomic_replace_failure_keeps_original_and_cleans_temporary(self):
        store = self.store()
        store.add(self.project())
        raw, before = self.path.read_bytes(), store.items()
        with patch.object(rq.os, 'replace', side_effect=PermissionError('foglalt')):
            with self.assertRaises(PermissionError):
                store.add(self.project('Második'))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(store.items(), before)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_transient_windows_reader_sharing_errors_retry_without_losing_queue(self):
        store = self.store()
        first = store.add(self.project('A'))
        original_replace = rq.os.replace
        for code in (5, 32, 33):
            attempts = []
            def replace(temporary, destination):
                attempts.append(True)
                if len(attempts) < 3:
                    error = PermissionError('Másik olvasó ideiglenesen használja.')
                    error.winerror = code
                    raise error
                return original_replace(temporary, destination)
            with self.subTest(code=code), patch.object(rq.os, 'replace', side_effect=replace), \
                    patch.object(rq.time, 'sleep') as sleep:
                added = store.add(self.project('Olvasási próba ' + str(code)))
                self.assertEqual(len(attempts), 3)
                self.assertEqual(sleep.call_count, 2)
            self.assertEqual(store.items()[-1]['id'], added['id'])
            self.assertEqual(store.items()[0]['id'], first['id'])
            self.assertEqual(self.read()['jobs'], store.items())
            self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_permanent_windows_sharing_failure_is_bounded_and_rolls_back(self):
        store = self.store()
        store.add(self.project('A'))
        raw, before = self.path.read_bytes(), store.items()
        error = PermissionError('Tartós fájlzárolás.')
        error.winerror = 32
        with patch.object(rq.os, 'replace', side_effect=error) as replace, \
                patch.object(rq.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                store.add(self.project('B'))
            self.assertEqual(replace.call_count, rq.REPLACE_ATTEMPTS)
            self.assertEqual(sleep.call_count, rq.REPLACE_ATTEMPTS - 1)
            self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 1.2)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(store.items(), before)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'A fájlmegosztási szabály Windows-specifikus.')
    def test_actual_windows_reader_can_close_while_atomic_writer_retries(self):
        store = self.store()
        store.add(self.project('A'))
        original_replace = rq.os.replace
        blocked = threading.Event()
        errors = []
        def replace(temporary, destination):
            try:
                return original_replace(temporary, destination)
            except OSError as error:
                if getattr(error, 'winerror', None) in (5, 32, 33):
                    blocked.set()
                raise
        def add():
            try:
                store.add(self.project('B'))
            except Exception as error:
                errors.append(error)
        reader = self.path.open('rb')
        thread = threading.Thread(target=add)
        try:
            with patch.object(rq.os, 'replace', side_effect=replace):
                thread.start()
                self.assertTrue(blocked.wait(2), 'A valódi olvasó nem fogta meg a Windows fájlcserét.')
                self.assertEqual(json.loads(reader.read())['jobs'][0]['title'], 'A')
                reader.close()
                thread.join(5)
                self.assertFalse(thread.is_alive())
        finally:
            reader.close()
            if thread.ident is not None:
                thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual([job['title'] for job in store.items()], ['A', 'B'])
        self.assertEqual(self.read()['jobs'], store.items())
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_worker_executes_sequentially_and_persists_progress_and_results(self):
        store = self.store()
        store.add(self.project('A'))
        store.add(self.project('B'))
        def assertion(project):
            jobs = self.read()['jobs']
            self.assertEqual(sum(job['status'] == 'running' for job in jobs), 1)
            self.assertEqual(next(job['title'] for job in jobs if job['status'] == 'running'), project.title)
        runner = self.runner(store, assertion=assertion)
        self.assertTrue(runner.start())
        self.assertTrue(runner.join(5))
        self.assertEqual(self.record, [('start', 'A'), ('finish', 'A'), ('start', 'B'), ('finish', 'B')])
        self.assertEqual([job['status'] for job in store.items()], ['complete', 'complete'])
        self.assertTrue(all(Path(job['result']).is_file() for job in store.items()))
        self.assertTrue(all(job['progress'] == 100 for job in store.items()))
        self.assertTrue(any(kind == 'progress' and data['progress'] == 22.2 for kind, data in self.events))
        self.assertTrue(all(0 <= data['progress'] <= 100 for kind, data in self.events if kind == 'progress'))
        self.assertEqual(self.read()['jobs'], store.items())
        runner.start(); runner.join(5)
        self.assertEqual(len(self.record), 4, 'Új indításkor a kész videókat kihagyjuk.')

    def test_job_failure_is_retained_and_next_job_continues(self):
        store = self.store()
        store.add(self.project('A'))
        store.add(self.project('B'))
        def factory(log, progress):
            return FakeEngine(log, progress, root=self.folder, record=self.record,
                              fail=not self.record)
        runner = self.runner(store, factory=factory)
        runner.start(); self.assertTrue(runner.join(5))
        first, second = store.items()
        self.assertEqual((first['status'], first['error'], first['result']), ('failed', 'Próbahiba', ''))
        self.assertEqual(second['status'], 'complete')
        runner.start(); runner.join(5)
        self.assertEqual(len(self.record), 3)

    def test_waiting_input_deleted_becomes_failed_without_invoking_engine(self):
        store = self.store()
        store.add(self.project())
        self.source.unlink()
        runner = self.runner(store, factory=lambda *args: self.fail('Nem indulhat feldolgozó'))
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual(store.items()[0]['status'], 'failed')
        self.assertIn('hiányzik', store.items()[0]['error'])

    def test_missing_output_is_failed(self):
        store = self.store()
        store.add(self.project())
        runner = self.runner(store, missing=True)
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual(store.items()[0]['status'], 'failed')
        self.assertIn('video.mp4', store.items()[0]['error'])

    def test_factory_failure_is_failed_and_next_job_runs(self):
        store = self.store()
        store.add(self.project('A')); store.add(self.project('B'))
        calls = []
        def factory(log, progress):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError('Motor nem tölthető')
            return FakeEngine(log, progress, root=self.folder, record=self.record)
        runner = self.runner(store, factory=factory)
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['failed', 'complete'])

    def test_stop_after_current_preserves_current_and_pending_jobs(self):
        store = self.store()
        store.add(self.project('A')); store.add(self.project('B'))
        gate = self.gate()
        runner = self.runner(store, gate=gate)
        runner.start()
        engine = self.wait_running(runner)
        runner.stop_after_current()
        self.assertFalse(engine.cancel.is_set())
        gate.set()
        self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['complete', 'pending'])
        self.assertTrue(self.events[-1][1]['stopped'])
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['complete', 'complete'])

    def test_cancel_current_stops_following_and_can_be_explicitly_retried(self):
        store = self.store()
        first = store.add(self.project('A')); store.add(self.project('B'))
        gate = self.gate()
        runner = self.runner(store, gate=gate)
        runner.start(); engine = self.wait_running(runner)
        runner.cancel_current()
        self.assertTrue(engine.cancel.is_set())
        self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['cancelled', 'pending'])
        self.assertEqual(store.items()[0]['error'], '')
        store.retry(first['id'])
        gate.set(); runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['complete', 'complete'])

    def test_engine_cancellation_also_stops_following(self):
        store = self.store()
        store.add(self.project('A')); store.add(self.project('B'))
        runner = self.runner(store, cancel_itself=True)
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['cancelled', 'pending'])

    def test_cancel_during_factory_does_not_lose_cancellation(self):
        store = self.store()
        store.add(self.project('A')); store.add(self.project('B'))
        entered, gate = self.gate(), self.gate()
        def factory(log, progress):
            entered.set()
            gate.wait(5)
            return FakeEngine(log, progress, root=self.folder, record=self.record)
        runner = self.runner(store, factory=factory)
        runner.start(); self.assertTrue(entered.wait(5))
        self.assertIsNone(runner.active_engine)
        runner.cancel_current(); gate.set()
        self.assertTrue(runner.join(5))
        self.assertEqual([job['status'] for job in store.items()], ['cancelled', 'pending'])

    def test_pending_jobs_can_be_added_reordered_and_removed_while_running(self):
        store = self.store()
        first = store.add(self.project('A')); second = store.add(self.project('B'))
        gate = self.gate()
        runner = self.runner(store, gate=gate)
        runner.start(); self.wait_running(runner)
        third = store.add(self.project('C'))
        store.move(third['id'], -1)
        store.remove(second['id'])
        for action in (lambda: store.remove(first['id']), lambda: store.move(first['id'], 1),
                       lambda: store.retry(first['id']), lambda: store.move(third['id'], -1)):
            with self.assertRaises(ValueError):
                action()
        gate.set(); self.assertTrue(runner.join(5))
        self.assertEqual(self.record, [('start', 'A'), ('finish', 'A'), ('start', 'C'), ('finish', 'C')])

    def test_one_runner_per_store_and_repeated_start_is_safe(self):
        store = self.store()
        store.add(self.project())
        gate = self.gate()
        runner, other = self.runner(store, gate=gate), self.runner(store)
        runner.start(); self.wait_running(runner)
        self.assertFalse(runner.start())
        with self.assertRaises(rq.QueueError):
            other.start()
        with self.assertRaises(rq.QueueError):
            store.close()
        gate.set(); self.assertTrue(runner.join(5))
        self.assertTrue(other.start()); self.assertTrue(other.join(5))
        self.assertEqual(len(self.record), 2)

    def test_event_callback_errors_and_mutations_do_not_corrupt_results(self):
        store = self.store()
        store.add(self.project())
        def callback(kind, data):
            if kind == 'job':
                data['project']['videos'].clear()
                data['status'] = 'broken'
            raise RuntimeError('UI bezárult')
        runner = self.runner(store, on_event=callback)
        runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual(store.items()[0]['status'], 'complete')
        self.assertEqual(store.items()[0]['project']['videos'], [str(self.source)])

    def test_fatal_disk_error_stops_worker_and_preserves_saved_status(self):
        store = self.store()
        store.add(self.project())
        runner = self.runner(store)
        raw = self.path.read_bytes()
        with patch.object(store, '_write_atomic', side_effect=OSError('Tele a lemez')):
            runner.start(); self.assertTrue(runner.join(5))
        self.assertEqual(store.items()[0]['status'], 'pending')
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn('Tele a lemez', runner.last_error)
        self.assertTrue(any(kind == 'error' for kind, _ in self.events))
        self.assertEqual(self.events[-1][0], 'done')

    def test_keep_awake_is_scoped_and_restored_after_success_failure_and_cancel(self):
        for options in ({}, {'fail': True}, {'cancel_itself': True}):
            store = self.store(self.folder / (uuid.uuid4().hex + '.json'))
            store.add(self.project())
            with patch.object(rq, '_keep_awake', return_value=True) as power:
                runner = self.runner(store, **options)
                runner.start(); self.assertTrue(runner.join(5))
                self.assertEqual([call.args[0] for call in power.call_args_list], [True, False])

    def test_keep_awake_failure_warns_but_still_renders_and_restores(self):
        store = self.store()
        store.add(self.project())
        with patch.object(rq, '_keep_awake', side_effect=[OSError('Unavailable'), True]) as power:
            runner = self.runner(store)
            runner.start(); self.assertTrue(runner.join(5))
            self.assertEqual([call.args[0] for call in power.call_args_list], [True, False])
        self.assertEqual(store.items()[0]['status'], 'complete')
        self.assertTrue(any(kind == 'log' and data['id'] == '' and 'alvást' in data['message']
                            for kind, data in self.events))

    def test_close_is_idempotent_and_rejects_later_operations(self):
        store = self.store()
        store.close(); store.close()
        for action in (store.items, lambda: store.add(self.project()), lambda: self.runner(store).start()):
            with self.assertRaises(rq.QueueError):
                action()

    def test_module_import_does_not_load_tk_or_video_engine(self):
        script = "import sys;sys.path.insert(0,sys.argv[1]);import render_queue;assert 'tkinter' not in sys.modules;assert 'ytmuhely' not in sys.modules"
        result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT / 'work/v2_source')],
                                capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
