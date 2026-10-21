"""Read-only GPU reset checks: job cursor, throttling and Windows query faults."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import gpu_health as health


@contextmanager
def report_folder():
    parent = ROOT / 'work/v2_tests/gpu_health_fixtures'
    folder = parent / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        if folder.resolve().parent == parent.resolve():
            shutil.rmtree(folder)


class GpuHealthTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime(2026, 10, 21, 6, 4, 41, tzinfo=timezone.utc)
        self.now = self.started
        self.tick = 10.0

    def event(self, record=101, seconds=1, **changes):
        result = {'record_id': record, 'timestamp': (self.started + timedelta(seconds=seconds)).isoformat(),
                  'event_id': 4101, 'provider': 'Display'}
        result.update(changes)
        return result

    def monitor(self, reader, **kwargs):
        return health.begin_monitor(reader=reader, clock=lambda: self.now,
            monotonic=lambda: self.tick, platform='nt', **kwargs)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.tick += seconds

    def test_baseline_reads_only_one_minute_before_submission(self):
        reader = Mock(return_value=[self.event(record=99, seconds=-1)])
        monitor = self.monitor(reader)
        reader.assert_called_once_with(self.started - timedelta(seconds=60), self.started)
        self.assertEqual(monitor.baseline_record_id, 99)
        self.assertTrue(monitor.available)

    def test_progress_checks_are_throttled_to_twenty_seconds(self):
        reader = Mock(return_value=[])
        monitor = self.monitor(reader)
        for _ in range(19):
            self.advance(1)
            monitor.check()
        self.assertEqual(reader.call_count, 1)
        self.advance(1)
        monitor.check()
        self.assertEqual(reader.call_count, 2)
        reader.assert_called_with(self.started, self.now)

    def test_forced_success_check_bypasses_poll_interval(self):
        reader = Mock(side_effect=[[], [self.event()]])
        monitor = self.monitor(reader)
        self.advance(1)
        with self.assertRaises(health.GpuResetError):
            monitor.check(force=True)
        self.assertEqual(reader.call_count, 2)

    def test_old_reset_does_not_fail_new_render_even_if_reader_returns_it(self):
        old = self.event(record=100, seconds=-1)
        reader = Mock(side_effect=[[old], [old]])
        monitor = self.monitor(reader)
        self.advance(20)
        monitor.check()
        self.assertIsNone(monitor.reset_event)

    def test_same_cursor_record_does_not_fail_render(self):
        reader = Mock(side_effect=[[self.event(record=100, seconds=0)], [self.event(record=100, seconds=1)]])
        monitor = self.monitor(reader)
        self.advance(20)
        monitor.check()
        self.assertIsNone(monitor.reset_event)

    def test_new_reset_error_retains_exact_record_and_utc_timestamp(self):
        reset = self.event(record=24791, timestamp='2026-10-21T08:29:17.5569997+02:00')
        monitor = self.monitor(Mock(side_effect=[[], [reset]]))
        self.advance(1500)
        with self.assertRaises(health.GpuResetError) as caught:
            monitor.check()
        self.assertEqual(caught.exception.record_id, 24791)
        self.assertEqual(caught.exception.timestamp.isoformat(), '2026-10-21T06:29:17.556999+00:00')
        self.assertIn('Display 4101', str(caught.exception))
        self.assertIn('24791', str(caught.exception))

    def test_reset_remains_fatal_without_more_event_queries(self):
        reader = Mock(side_effect=[[], [self.event()]])
        monitor = self.monitor(reader)
        self.advance(20)
        for force in (False, True, False):
            with self.assertRaises(health.GpuResetError):
                monitor.check(force=force)
        self.assertEqual(reader.call_count, 2)

    def test_non_windows_does_not_read_event_log(self):
        reader = Mock(side_effect=AssertionError('No query expected'))
        monitor = health.begin_monitor(reader=reader, platform='posix')
        monitor.check(force=True)
        reader.assert_not_called()
        self.assertFalse(monitor.available)

    def test_temporarily_unavailable_event_log_is_retried_without_failures(self):
        reader = Mock(side_effect=[None, PermissionError('denied'), []])
        monitor = self.monitor(reader)
        self.assertFalse(monitor.available)
        self.advance(20)
        monitor.check()
        self.assertFalse(monitor.available)
        self.advance(20)
        monitor.check()
        self.assertTrue(monitor.available)

    def test_reset_after_failed_baseline_is_still_detected(self):
        monitor = self.monitor(Mock(side_effect=[None, [self.event()]]))
        self.advance(20)
        with self.assertRaises(health.GpuResetError):
            monitor.check()

    def test_unrelated_or_malformed_event_records_are_ignored(self):
        invalid = [self.event(event_id=1001), self.event(provider='Schannel'),
            self.event(record=True), self.event(record=-1), self.event(timestamp='nonsense'),
            self.event(timestamp='2026-10-21T06:04:42'), None, '4101', []]
        monitor = self.monitor(Mock(side_effect=[[], invalid]))
        self.advance(20)
        monitor.check()
        self.assertIsNone(monitor.reset_event)

    def test_future_event_is_ignored_until_its_timestamp_is_in_job_range(self):
        event = self.event(seconds=100)
        reader = Mock(side_effect=[[], [event], [event]])
        monitor = self.monitor(reader)
        self.advance(20)
        monitor.check()
        self.advance(100)
        with self.assertRaises(health.GpuResetError):
            monitor.check()

    def test_earliest_new_reset_is_returned_when_multiple_events_exist(self):
        monitor = self.monitor(Mock(side_effect=[[], [self.event(record=102, seconds=5), self.event(record=101, seconds=2)]]))
        self.advance(20)
        with self.assertRaises(health.GpuResetError) as caught:
            monitor.check()
        self.assertEqual(caught.exception.record_id, 101)

    def test_clock_moving_backwards_does_not_query_invalid_interval(self):
        reader = Mock(return_value=[])
        monitor = self.monitor(reader)
        self.now -= timedelta(seconds=10)
        self.tick += 20
        monitor.check(force=True)
        self.assertEqual(reader.call_count, 1)

    def test_poll_interval_requires_finite_fifteen_to_thirty_seconds(self):
        for interval in (0, 14, 31, float('inf'), float('nan'), '20'):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.monitor(Mock(return_value=[]), interval=interval)

    def test_both_confirmed_reset_and_invalid_output_stop_queue(self):
        reset = health.ResetEvent(1, self.started)
        for error in (health.GpuResetError(reset), health.GpuResultError(), health.GpuResultError('Invalid result')):
            self.assertIsInstance(error, health.GpuHealthError)
            self.assertTrue(error.halt_queue)

    def test_cancel_propagates_before_query_without_reading_log(self):
        class TestCancelled(Exception):
            pass
        reader = Mock(return_value=[])
        with self.assertRaises(TestCancelled):
            self.monitor(reader, cancel=Mock(side_effect=TestCancelled()))
        reader.assert_not_called()

    def test_cancel_propagates_after_timed_out_query(self):
        class TestCancelled(Exception):
            pass
        cancel = Mock(side_effect=[None, TestCancelled()])
        with self.assertRaises(TestCancelled):
            self.monitor(Mock(side_effect=subprocess.TimeoutExpired('powershell.exe', 4)), cancel=cancel)
        self.assertEqual(cancel.call_count, 2)

    def test_saved_report_distinguishes_unavailable_and_watching(self):
        with report_folder() as folder:
            report = Path(folder) / 'gpu_health.json'
            monitor = self.monitor(Mock(side_effect=[None, []]), report_path=report)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'unavailable')
            self.assertFalse(data['available'])
            self.assertIn('could not be read', data['reason'])
            self.advance(20)
            monitor.check()
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'watching')
            self.assertTrue(data['available'])
            self.assertIsNone(data['reason'])
            self.assertEqual(data['last_checked_at'], self.now.isoformat())

    def test_reset_report_preserves_exact_windows_timestamp(self):
        with report_folder() as folder:
            report = Path(folder) / 'gpu_health.json'
            stamp = '2026-10-21T06:04:42.1234567Z'
            monitor = self.monitor(Mock(side_effect=[[], [self.event(timestamp=stamp)]]), report_path=report)
            self.advance(20)
            with self.assertRaises(health.GpuResetError):
                monitor.check(force=True)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'reset')
            self.assertEqual(data['reset_event'], {'provider': 'Display', 'event_id': 4101, 'record_id': 101, 'timestamp': stamp})
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_unwritable_report_does_not_hide_confirmed_reset(self):
        with report_folder() as folder:
            report = Path(folder) / 'gpu_health.json'
            with patch.object(health.os, 'replace', side_effect=PermissionError('blocked')):
                monitor = self.monitor(Mock(side_effect=[[], [self.event()]]), report_path=report)
                self.advance(20)
                with self.assertRaises(health.GpuResetError):
                    monitor.check()
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_non_windows_report_explicitly_records_unsupported_platform(self):
        with report_folder() as folder:
            report = Path(folder) / 'gpu_health.json'
            health.begin_monitor(platform='posix', report_path=report)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'unavailable')
            self.assertIn('platform', data['reason'])


class WindowsEventReaderTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 21, 6, 4, 41, tzinfo=timezone.utc)
        self.end = self.start + timedelta(seconds=20)

    def result(self, output='[]', returncode=0):
        return SimpleNamespace(stdout=output, stderr='', returncode=returncode)

    def test_query_is_hidden_bounded_read_only_and_does_not_capture_messages(self):
        with patch.object(health.os, 'name', 'nt'), patch.object(health.subprocess, 'run', return_value=self.result()) as run:
            self.assertEqual(health.read_display_resets(self.start, self.end), [])
        args, options = run.call_args
        argv = args[0]
        self.assertIsInstance(argv, list)
        self.assertIn('-NonInteractive', argv)
        script = base64.b64decode(argv[-1]).decode('utf-16le')
        self.assertIn("ProviderName='Display'; Id=4101", script)
        self.assertIn('StartTime=$jobStart; EndTime=$jobEnd', script)
        self.assertIn('.LocalDateTime', script)
        self.assertIn("Join-Path $PSHOME", script)
        self.assertIn('-MaxEvents 32', script)
        self.assertNotIn('.Message', script)
        self.assertNotIn('Set-', script)
        self.assertEqual(options['timeout'], 4.0)
        self.assertEqual(options['creationflags'], health.CREATE_NO_WINDOW)
        self.assertNotIn('shell', options)

    def test_json_event_is_normalized_with_record_id_and_utc_time(self):
        data = [{'record_id': 789, 'timestamp': '2026-10-21T08:04:45.1234567+02:00', 'event_id': 4101, 'provider': 'Display'}]
        with patch.object(health.os, 'name', 'nt'), patch.object(health.subprocess, 'run', return_value=self.result(json.dumps(data))):
            events = health.read_display_resets(self.start, self.end)
        self.assertEqual(events[0].record_id, 789)
        self.assertEqual(events[0].timestamp.tzinfo, timezone.utc)

    def test_failed_or_malformed_queries_are_nonfatal(self):
        for result in (self.result('', 3), self.result('invalid'), self.result('{}'), self.result('x' * 65537)):
            with self.subTest(result=result), patch.object(health.os, 'name', 'nt'), patch.object(health.subprocess, 'run', return_value=result):
                self.assertIsNone(health.read_display_resets(self.start, self.end))

    def test_missing_powershell_and_timeout_are_nonfatal(self):
        for error in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired('powershell.exe', 4)):
            with self.subTest(error=error), patch.object(health.os, 'name', 'nt'), patch.object(health.subprocess, 'run', side_effect=error):
                self.assertIsNone(health.read_display_resets(self.start, self.end))

    def test_non_windows_reader_never_starts_subprocess(self):
        with patch.object(health.os, 'name', 'posix'), patch.object(health.subprocess, 'run') as run:
            self.assertIsNone(health.read_display_resets(self.start, self.end))
        run.assert_not_called()

    def test_unrelated_json_records_are_discarded(self):
        data = [{'record_id': 789, 'timestamp': self.start.isoformat(), 'event_id': 1001, 'provider': 'Windows Error Reporting'}]
        with patch.object(health.os, 'name', 'nt'), patch.object(health.subprocess, 'run', return_value=self.result(json.dumps(data))):
            self.assertEqual(health.read_display_resets(self.start, self.end), [])


if __name__ == '__main__':
    unittest.main()
