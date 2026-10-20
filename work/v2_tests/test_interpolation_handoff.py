"""GPU handoff must preserve shared jobs and confirm the owned child exited."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import subprocess,sys,unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
import comfy_video,realistic_video
from ytmuhely import Cancelled


def queue(running=(),pending=()):
    return {'queue_running':[[0,key,{},{}] for key in running],
            'queue_pending':[[1,key,{},{}] for key in pending]}


class InterpolationHandoffTests(unittest.TestCase):
    def owner(self,confirm_exit=True):
        process=Mock();process.poll.return_value=None
        def wait(*args,**kwargs):
            if confirm_exit:process.poll.return_value=0
            return 0
        process.wait.side_effect=wait
        engine=SimpleNamespace(_comfy_process=process,_comfy_url='http://127.0.0.1:8190',
            _comfy_root='example',_comfy_prompt_id='ours',_comfy_log_handle=Mock(),
            check=Mock(),log=Mock())
        return engine,process

    def test_idle_owned_child_is_stopped_and_exit_is_confirmed(self):
        engine,process=self.owner()
        with patch.object(comfy_video,'_request',return_value=queue()) as request:
            self.assertTrue(realistic_video._release_for_interpolation(engine))
        self.assertEqual([call.args[1] for call in request.call_args_list],['/queue','/queue'])
        process.terminate.assert_called_once();process.wait.assert_called_once()
        self.assertEqual(process.poll(),0)
        self.assertIsNone(engine._comfy_process)

    def test_any_initial_running_or_pending_job_uses_cpu_without_stopping_it(self):
        for state in (queue(running=['other']),queue(pending=['other']),
                      queue(running=['ours']),queue(pending=['ours'])):
            with self.subTest(queue=state):
                engine,process=self.owner()
                with patch.object(comfy_video,'_request',return_value=state) as request:
                    self.assertFalse(realistic_video._release_for_interpolation(engine))
                self.assertEqual(request.call_count,1)
                process.terminate.assert_not_called();process.kill.assert_not_called()
                self.assertIs(engine._comfy_process,process)

    def test_busy_or_unreadable_recheck_preserves_child_and_uses_cpu(self):
        for changed in (queue(running=['other']),queue(pending=['other']),{},
                        OSError('queue closed'),ValueError('bad JSON')):
            with self.subTest(changed=changed):
                engine,process=self.owner()
                with patch.object(comfy_video,'_request',side_effect=[queue(),changed]) as request:
                    self.assertFalse(realistic_video._release_for_interpolation(engine))
                self.assertEqual(request.call_count,2)
                process.terminate.assert_not_called();process.kill.assert_not_called()
                self.assertIsNone(process.poll())

    def test_unreadable_or_malformed_initial_queue_uses_cpu(self):
        for state in (None,{},[],{'queue_running':[]},queue(running=['']),
                      OSError('offline'),ValueError('invalid JSON'),RuntimeError('HTTP500')):
            with self.subTest(state=state):
                engine,process=self.owner()
                with patch.object(comfy_video,'_request',side_effect=[state]):
                    self.assertFalse(realistic_video._release_for_interpolation(engine))
                process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_unknown_owner_or_missing_endpoint_does_not_touch_a_server(self):
        for field in ('_comfy_process','_comfy_url'):
            with self.subTest(field=field):
                engine,process=self.owner();setattr(engine,field,None)
                with patch.object(comfy_video,'_request') as request:
                    self.assertFalse(realistic_video._release_for_interpolation(engine))
                request.assert_not_called()
                process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_ownership_change_during_queue_read_uses_cpu_and_preserves_both_children(self):
        engine,process=self.owner();replacement=Mock()
        def changed(*args,**kwargs):
            engine._comfy_process=replacement
            return queue()
        with patch.object(comfy_video,'_request',side_effect=changed) as request:
            self.assertFalse(realistic_video._release_for_interpolation(engine))
        self.assertEqual(request.call_count,1)
        for child in (process,replacement):
            child.terminate.assert_not_called();child.kill.assert_not_called()
        self.assertIs(engine._comfy_process,replacement)

    def test_detached_child_with_unconfirmed_exit_never_permits_vulkan(self):
        engine,process=self.owner(confirm_exit=False)
        with patch.object(comfy_video,'_request',return_value=queue()):
            self.assertFalse(realistic_video._release_for_interpolation(engine))
        process.wait.assert_called_once()
        self.assertIsNone(process.poll())

    def test_shutdown_timeout_does_not_permit_vulkan(self):
        engine,process=self.owner()
        with patch.object(comfy_video,'_request',return_value=queue()),\
             patch.object(comfy_video,'stop_engine',side_effect=subprocess.TimeoutExpired('owned-child',8)):
            self.assertFalse(realistic_video._release_for_interpolation(engine))
        self.assertIsNone(process.poll())

    def test_cancellation_after_queue_read_never_stops_or_interpolates(self):
        engine,process=self.owner();engine.check.side_effect=[None,Cancelled('cancelled')]
        with patch.object(comfy_video,'_request',return_value=queue()):
            with self.assertRaises(Cancelled):realistic_video._release_for_interpolation(engine)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_cancellation_from_shutdown_propagates(self):
        engine,process=self.owner()
        with patch.object(comfy_video,'_request',return_value=queue()),\
             patch.object(comfy_video,'stop_engine',side_effect=Cancelled('cancelled')):
            with self.assertRaises(Cancelled):realistic_video._release_for_interpolation(engine)


if __name__=='__main__':unittest.main()
