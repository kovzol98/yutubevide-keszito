"""GPU observations stay bounded, read only, and distinguish no data from empty."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import gpu_resources as gpu


def row(pid, byte_count, adapter='0', **extra):
    return dict(instance=f'pid_{pid}_luid_0x00000000_0x000126f6_phys_{adapter}',
                bytes=byte_count, **extra)


class CounterParsingTests(unittest.TestCase):
    def test_sums_adapters_per_process_and_orders_largest_first(self):
        rows = [row(21, gpu.GIB), row(21, 2 * gpu.GIB, '1'), row(12, 4 * gpu.GIB)]
        self.assertEqual(gpu.parse_counter_rows(rows, {21: 'AMDRSServ', 12: 'AMDChat'}, own_pid=99),
                         [dict(pid=12, name='AMDChat', bytes=4 * gpu.GIB),
                          dict(pid=21, name='AMDRSServ', bytes=3 * gpu.GIB)])

    def test_duplicate_instances_do_not_double_memory(self):
        original = row(10, 1234)
        duplicate = dict(original, instance=original['instance'].upper())
        self.assertEqual(gpu.parse_counter_rows([original, duplicate], [], own_pid=99),
                         [dict(pid=10, name='Folyamat 10', bytes=1234)])

    def test_windows_counter_and_process_field_names_are_supported(self):
        rows = [dict(InstanceName='pid_15_luid_a_phys_0', CookedValue=4567)]
        self.assertEqual(gpu.parse_counter_rows(rows, [dict(Id=15, ProcessName='Python')], own_pid=99),
                         [dict(pid=15, name='Python', bytes=4567)])

    def test_own_process_and_system_pids_are_excluded(self):
        self.assertEqual(gpu.parse_counter_rows([row(pid, 1234) for pid in (0, 4, 99)], [], own_pid=99), [])

    def test_bad_memory_values_are_ignored(self):
        rows = [row(10 + index, value) for index, value in enumerate(
            (None, True, False, 'bad', float('nan'), float('inf'), -1, 0, 2 ** 64, 0.1))]
        self.assertEqual(gpu.parse_counter_rows(rows, [], own_pid=99), [])

    def test_unrecognised_instances_and_malformed_rows_are_ignored(self):
        rows = [None, [], 'text', {}, dict(instance=123, bytes=100),
                dict(instance='badpid_15_luid', bytes=100),
                dict(instance='pid_-15_luid', bytes=100),
                dict(instance='pid_15.5_luid', bytes=100)]
        self.assertEqual(gpu.parse_counter_rows(rows, [], own_pid=99), [])
        self.assertEqual(gpu.parse_counter_rows({}, [], own_pid=99), [])

    def test_process_paths_are_never_displayed(self):
        processes = [{'pid': 10, 'name': 'C:\\Sensitive\\program.exe'},
                     {'pid': 11, 'name': '/private/process'},
                     {'pid': True, 'name': 'bad'}, None]
        result = gpu.parse_counter_rows([row(10, 123), row(11, 99)], processes, own_pid=99)
        self.assertEqual([item['name'] for item in result], ['Folyamat 10', 'Folyamat 11'])

    def test_names_are_printable_and_bounded(self):
        result = gpu.parse_counter_rows([row(10, 123)], {10: '\x00' + 'a' * 120}, own_pid=99)
        self.assertEqual(result[0]['name'], 'a' * 96)


class ObservationTests(unittest.TestCase):
    def process(self, payload=None, returncode=0):
        process = Mock()
        process.returncode = returncode
        process.poll.return_value = returncode
        process.communicate.return_value = (json.dumps(
            payload if payload is not None else dict(rows=[], processes=[])).encode('utf-8'), b'')
        return process

    def probe(self, process, cancel=None, **patches):
        with patch.object(gpu.os, 'name', 'nt'), patch.object(gpu.subprocess, 'Popen', return_value=process) as launch:
            result = gpu.probe_gpu_users(cancel)
        self.assertEqual(launch.call_count, 1)
        return result, launch

    def test_successful_empty_sample_is_empty_list(self):
        process = self.process()
        result, launch = self.probe(process)
        self.assertEqual(result, [])
        args, options = launch.call_args
        self.assertEqual(args[0][0], 'powershell.exe')
        for option in ('-NoProfile', '-NonInteractive', '-NoLogo'):
            self.assertIn(option, args[0])
        self.assertIs(options['stdin'], subprocess.DEVNULL)
        self.assertNotIn('shell', options)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_success_returns_names_pids_bytes_without_other_process_data(self):
        process = self.process(dict(rows=[row(10, 4 * gpu.GIB)], processes=[dict(pid=10, name='AMD.Chat')]))
        result, _ = self.probe(process)
        self.assertEqual(result, [dict(pid=10, name='AMD.Chat', bytes=4 * gpu.GIB)])

    def test_utf8_bom_and_non_ascii_process_name(self):
        process = self.process()
        data = dict(rows=[row(10, gpu.GIB)], processes=[dict(pid=10, name='Őrszem')])
        process.communicate.return_value = ('\ufeff' + json.dumps(data, ensure_ascii=False)).encode('utf-8'), b''
        result, _ = self.probe(process)
        self.assertEqual(result[0]['name'], 'Őrszem')

    def test_unsupported_os_does_not_start_a_process(self):
        with patch.object(gpu.os, 'name', 'posix'), patch.object(gpu.subprocess, 'Popen') as launch:
            self.assertIsNone(gpu.probe_gpu_users())
            launch.assert_not_called()

    def test_already_cancelled_does_not_start_a_process(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(gpu.os, 'name', 'nt'), patch.object(gpu.subprocess, 'Popen') as launch:
            self.assertIsNone(gpu.probe_gpu_users(cancel))
            launch.assert_not_called()

    def test_start_failure_is_unknown_not_empty(self):
        with patch.object(gpu.os, 'name', 'nt'), patch.object(gpu.subprocess, 'Popen', side_effect=OSError('unavailable')):
            self.assertIsNone(gpu.probe_gpu_users())

    def test_nonzero_exit_and_bad_payloads_are_unknown(self):
        for output, code in [(b'{}', 1), (b'', 0), (b'null', 0), (b'bad json', 0),
                             (b'\xff', 0), (b'[]', 0), (b'{}', 0),
                             (b'{"rows":{},"processes":[]}', 0),
                             (b'{"rows":[],"processes":{}}', 0),
                             (b'x' * (gpu._MAX_JSON_BYTES + 1), 0)]:
            with self.subTest(output=output[:30], returncode=code):
                process = self.process(returncode=code)
                process.communicate.return_value = output, b''
                result, _ = self.probe(process)
                self.assertIsNone(result)

    def test_polling_wait_is_bounded_without_busy_sleep(self):
        process = self.process()
        process.communicate.side_effect = [subprocess.TimeoutExpired('probe', .1), (b'{"rows":[],"processes":[]}', b'')]
        result, _ = self.probe(process)
        self.assertEqual(result, [])
        for call in process.communicate.call_args_list:
            self.assertLessEqual(call.kwargs['timeout'], .1)

    def test_deadline_terminates_only_the_created_child(self):
        process = self.process()
        process.poll.return_value = None
        with patch.object(gpu.time, 'monotonic', side_effect=[0, 5]):
            result, _ = self.probe(process)
        self.assertIsNone(result)
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        process.communicate.assert_called_once_with(timeout=.2)

    def test_cancellation_during_query_stops_only_created_child(self):
        cancel = threading.Event()
        process = self.process()
        process.poll.return_value = None
        def communicate(timeout):
            if not cancel.is_set():
                cancel.set()
                raise subprocess.TimeoutExpired('probe', timeout)
            return b'', b''
        process.communicate.side_effect = communicate
        result, _ = self.probe(process, cancel)
        self.assertIsNone(result)
        process.terminate.assert_called_once_with()

    def test_cancellation_just_as_output_arrives_is_not_success(self):
        cancel = threading.Event()
        process = self.process()
        def communicate(timeout):
            cancel.set()
            return b'{"rows":[],"processes":[]}', b''
        process.communicate.side_effect = communicate
        result, _ = self.probe(process, cancel)
        self.assertIsNone(result)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_stubborn_child_is_killed_without_taskkill_or_process_lookup(self):
        process = self.process()
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired('probe', .2), (b'', b'')]
        gpu._stop_probe(process)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_args_list[-1].kwargs, dict(timeout=.2))


class DescriptionsTests(unittest.TestCase):
    def test_unknown_and_empty_are_distinct(self):
        self.assertIn('nem szolgáltatott', gpu.describe_gpu_users(None))
        self.assertIn('nincs kimutatható', gpu.describe_gpu_users([]))

    def test_only_substantial_users_are_named(self):
        text = gpu.describe_gpu_users([dict(pid=10, name='Small', bytes=gpu.GIB),
                                       dict(pid=11, name='AMDChat', bytes=int(5.8 * gpu.GIB))])
        self.assertIn('AMDChat (5,8 GiB)', text)
        self.assertNotIn('Small', text)
        self.assertIn('lassíthatja', text)

    def test_decimal_localisation_does_not_change_process_names(self):
        text = gpu.describe_gpu_users([dict(pid=10, name='AMD.Chat', bytes=int(2.5 * gpu.GIB))])
        self.assertIn('AMD.Chat (2,5 GiB)', text)

    def test_list_is_limited_and_invalid_values_cannot_be_formatted_as_real_data(self):
        users = [dict(pid=20 + i, name=f'Program {i}', bytes=(2 + i) * gpu.GIB) for i in range(10)]
        users.extend([dict(pid=True, name='bad', bytes=10 * gpu.GIB),
                      dict(pid=1, name='bad', bytes=float('nan')), None])
        text = gpu.describe_gpu_users(users)
        self.assertIn('további 2 folyamat', text)
        self.assertEqual(text.count(' GiB)'), 8)
        self.assertNotIn('bad', text)

    def test_custom_threshold_uses_hungarian_decimal(self):
        self.assertIn('0,5 GiB', gpu.describe_gpu_users([], gpu.GIB // 2))


if __name__ == '__main__':
    unittest.main()
