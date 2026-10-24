"""Windows upgrade recognition, isolated GPU checks and fatal native failures."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'work/v2_source'), str(ROOT/'work/packages')]
import windows_support as w

class WindowsSupportTests(unittest.TestCase):
    def payload(self, **changes):
        data=dict(gpu_ready=True,finite=True,correct=True,hip='7.2',cuda='',torch='2.9.1',
                  gpu='AMD Radeon RX 9070 XT',memory_bytes=16*1024**3)
        data.update(changes)
        return b'non-json runtime notice\n'+w._MARKER.encode()+json.dumps(data).encode()

    def test_windows11_upgrade_uses_build(self):
        info=w.classify_windows(26300,'AMD64',release='26H2',revision=9711)
        self.assertTrue(info['supported'])
        self.assertEqual(w.system_label(info),'Windows 11 • 26H2 • build 26300.9711')

    def test_windows10_is_not_supported_amd_target(self):
        self.assertFalse(w.classify_windows(19045,'AMD64')['supported'])

    def test_arm64_and_32_bit_process_are_not_supported(self):
        self.assertFalse(w.classify_windows(26300,'ARM64')['supported'])
        self.assertFalse(w.classify_windows(26300,'AMD64',32)['supported'])

    def test_unknown_build_is_not_advertised_as_supported(self):
        self.assertFalse(w.classify_windows(None,'AMD64')['supported'])
        self.assertFalse(w.classify_windows(True,'AMD64')['supported'])

    @unittest.skipUnless(w.os.name=='nt', 'Windows version API')
    def test_sys_build_wins_over_kernel_dll_platform_version(self):
        import types
        fake=types.SimpleNamespace(build=26300,platform_version=(10,0,26100))
        with patch.object(w.sys,'getwindowsversion',return_value=fake,create=True), patch.object(w.platform,'machine',return_value='AMD64'):
            self.assertEqual(w.system_info()['build'],26300)

    def test_gpu_requires_real_correct_finite_result(self):
        self.assertEqual(w._parse_gpu(self.payload())['status'],'PASS')
        for change in ({'gpu_ready':False},{'finite':False},{'correct':False},
                       {'correct':'True'},{'hip':'','cuda':''}):
            with self.subTest(change=change):
                self.assertEqual(w._parse_gpu(self.payload(**change))['status'],'FAIL')

    def test_subprocess_output_is_not_copied_wholesale(self):
        result=w._parse_gpu(self.payload(inputs='private tensor',extra='secret'))
        self.assertNotIn('inputs',result)
        self.assertNotIn('extra',result)
        self.assertEqual(result['backend'],'AMD ROCm / HIP')

    def test_malformed_or_oversized_response(self):
        for raw in (b'',b'garbage',b'\xff',b'YTM_GPU_CHECK:[]',b'x'*(1024**2+1)):
            self.assertEqual(w._parse_gpu(raw)['status'],'unavailable')

    def test_native_crash_stops_queue_and_handles_signed_exit(self):
        for code in (3221225477,-1073741819):
            error=w.native_failure(code)
            self.assertTrue(error.halt_queue)
            self.assertIn('0xC0000005',str(error))
            self.assertLess(len(str(error)),500)
        self.assertIsNone(w.native_failure(1))

    def test_missing_motor_does_not_spawn(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/temp') as folder, patch.object(w.subprocess,'Popen') as popen:
            self.assertEqual(w.probe_gpu(folder)['status'],'missing')
            popen.assert_not_called()

    def test_cancelled_probe_does_not_spawn(self):
        cancel=threading.Event();cancel.set()
        with patch.object(w.subprocess,'Popen') as popen:
            self.assertEqual(w.probe_gpu(ROOT,cancel)['status'],'cancelled')
            popen.assert_not_called()

    def test_invalid_timeout_does_not_spawn(self):
        for timeout in (True,0,91,float('nan'),float('inf')):
            with self.assertRaises(ValueError):w.probe_gpu(ROOT,timeout=timeout)

    def fixture(self,folder):
        base=Path(folder)/'motor with spaces'/'ComfyUI_windows_portable'
        (base/'python_embeded').mkdir(parents=True)
        (base/'ComfyUI').mkdir()
        (base/'python_embeded/python.exe').touch()
        (base/'ComfyUI/main.py').touch()
        return base

    def test_fatal_child_exit_is_clear_and_does_not_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/temp') as folder:
            base=self.fixture(folder)
            child=Mock(returncode=3221225477)
            child.communicate.return_value=(b'',None)
            with patch.object(w.subprocess,'Popen',return_value=child) as popen:
                result=w.probe_gpu(base)
            self.assertEqual(popen.call_count,1)
            self.assertEqual(result['exit_code'],'0xC0000005')
            self.assertEqual(result['status'],'FAIL')

    def test_probe_timeout_stops_only_its_child(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/temp') as folder:
            base=self.fixture(folder);child=Mock(returncode=None)
            child.poll.return_value=None
            child.communicate.return_value=(b'',None)
            with patch.object(w.subprocess,'Popen',return_value=child), \
                 patch.object(w.time,'monotonic',side_effect=[0,2]):
                result=w.probe_gpu(base,timeout=1)
            self.assertEqual(result['status'],'timeout')
            child.terminate.assert_called_once()

    def test_gpu_fails_environment_check_even_on_windows11(self):
        with patch.object(w,'system_info',return_value=w.classify_windows(26300,'AMD64')), \
             patch.object(w,'probe_gpu',return_value=dict(status='FAIL',reason='GPU failed')):
            report=w.check_environment(ROOT)
        self.assertEqual(report['status'],'FAIL')
        self.assertIn('GPU failed',w.describe(report))

    def test_windows_event_probe_does_not_wait_for_pipe_eof(self):
        import gpu_health
        child=Mock(returncode=0)
        def start(command,**options):
            self.assertNotEqual(options['stdout'],subprocess.PIPE)
            options['stdout'].write(b'[]')
            return child
        with patch.object(gpu_health.subprocess,'Popen',side_effect=start):
            result=gpu_health._bounded_query(['owned-probe'],timeout=4,creationflags=0)
        self.assertEqual(result.stdout,'[]')
        child.communicate.assert_not_called()

    def test_windows_event_timeout_stays_bounded_after_kill(self):
        import gpu_health
        child=Mock()
        child.poll.return_value=None
        child.wait.side_effect=subprocess.TimeoutExpired('owned-probe',4)
        with patch.object(gpu_health.subprocess,'Popen',return_value=child):
            with self.assertRaises(subprocess.TimeoutExpired):
                gpu_health._bounded_query(['owned-probe'],timeout=4,creationflags=0)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        child.communicate.assert_not_called()
        self.assertEqual(child.wait.call_count,3)

    @unittest.skipUnless(w.os.name=='nt', 'Windows no-replace rename')
    def test_resume_journal_works_when_hard_links_are_denied(self):
        import local_resume
        with tempfile.TemporaryDirectory(dir=ROOT/'work/temp') as folder:
            root=Path(folder)
            with patch.object(local_resume.os,'link',side_effect=PermissionError('hard links unavailable')):
                local_resume._atomic_create(root,{'schema':1,'complete':True})
                self.assertEqual(json.loads((root/'folytatas.json').read_text()),{'schema':1,'complete':True})
                with self.assertRaises(ValueError):
                    local_resume._atomic_create(root,{'schema':2})
                self.assertEqual(json.loads((root/'folytatas.json').read_text())['schema'],1)
                self.assertFalse(list(root.glob('.folytatas.pending_*')))

if __name__=='__main__':unittest.main()