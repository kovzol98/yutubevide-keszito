"""CPU-only ownership regression: final cleanup cannot kill another client's job."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json,shutil,sys,unittest,uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'work/v2_source'))
import comfy_video
from PIL import Image

def malformed_queues():
    return [None, [], {}, {'queue_running': []}, {'queue_pending': []},
        {'queue_running': None, 'queue_pending': []},
        {'queue_running': [], 'queue_pending': ()},
        {'queue_running': '', 'queue_pending': []},
        *({'queue_running': [entry], 'queue_pending': []} for entry in
          ([], None, {}, 'other', [0], [0, None], [0, 42], [0, []], [0, ''], [0, '   '])),
        {'queue_running': [], 'queue_pending': [[]]}]

class FinalCleanupTests(unittest.TestCase):
    def owner(self):
        process=Mock();process.poll.return_value=None;process.wait.return_value=0
        handle=Mock()
        engine=SimpleNamespace(_comfy_process=process,_comfy_url='http://127.0.0.1:8190',
            _comfy_root='C:/example/AI_Motor',_comfy_prompt_id='ours',
            _comfy_log_handle=handle,log=Mock())
        return engine,process,handle

    @staticmethod
    def queue(running=(),pending=()):
        return {'queue_running':[[0,prompt,{},{}] for prompt in running],
                'queue_pending':[[1,prompt,{},{}] for prompt in pending]}

    def test_foreign_pending_job_survives_final_cleanup(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(running=['ours'],pending=['someone-else'])):
            comfy_video.stop_engine(engine)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_foreign_running_job_survives_final_cleanup(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(running=['someone-else'],pending=['ours'])):
            comfy_video.stop_engine(engine)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_own_running_job_may_be_stopped(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(running=['ours'])):
            comfy_video.stop_engine(engine)
        process.terminate.assert_called_once();process.wait.assert_called()

    def test_own_pending_job_may_be_stopped(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(pending=['ours'])):
            comfy_video.stop_engine(engine)
        process.terminate.assert_called_once();process.wait.assert_called()

    def test_empty_queue_may_be_stopped(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue()):
            comfy_video.stop_engine(engine)
        process.terminate.assert_called_once();process.wait.assert_called()

    def test_unreadable_queue_preserves_the_process(self):
        for error in (OSError('connection reset'),ValueError('invalid JSON'),RuntimeError('HTTP500')):
            with self.subTest(error=type(error).__name__):
                engine,process,_=self.owner()
                with patch.object(comfy_video,'_request',side_effect=error):
                    comfy_video.stop_engine(engine)
                process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_malformed_queue_preserves_the_process(self):
        for queue in malformed_queues():
            with self.subTest(queue=queue):
                engine,process,handle=self.owner()
                with patch.object(comfy_video,'_request',return_value=queue):
                    comfy_video.stop_engine(engine)
                process.terminate.assert_not_called();process.kill.assert_not_called()
                handle.close.assert_called_once()
                self.assertIsNone(engine._comfy_process)

    def test_malformed_queue_cancellation_only_deletes_the_owned_prompt(self):
        for queue in malformed_queues():
            with self.subTest(queue=queue):
                engine,process,_=self.owner();log=Mock()
                with patch.object(comfy_video,'_request',return_value=queue) as request:
                    comfy_video._cancel_prompt(engine,engine._comfy_url,'ours',log)
                self.assertEqual(request.call_args_list[0].args,
                    ('http://127.0.0.1:8190','/queue',{'delete':['ours']}))
                self.assertEqual(request.call_count,2)
                process.terminate.assert_not_called();process.kill.assert_not_called()
                self.assertIs(engine._comfy_process,process)

    def test_foreign_job_survives_cancellation_of_our_prompt(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(running=['ours'],pending=['other'])):
            comfy_video._cancel_prompt(engine,engine._comfy_url,'ours',Mock())
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_owned_prompt_cancellation_still_stops_exclusive_server(self):
        engine,process,_=self.owner()
        with patch.object(comfy_video,'_request',return_value=self.queue(running=['ours'])):
            comfy_video._cancel_prompt(engine,engine._comfy_url,'ours',Mock())
        process.terminate.assert_called_once()

    def test_reused_external_server_is_never_stopped(self):
        engine,process,_=self.owner();engine._comfy_process=None
        with patch.object(comfy_video,'_request') as request:
            comfy_video.stop_engine(engine)
        request.assert_not_called();process.terminate.assert_not_called()

class InterpolationHandoffIntegrationTests(unittest.TestCase):
    def run_scene(self,queue):
        import realistic_video
        base=ROOT/'work/cleanup_tests'/uuid.uuid4().hex
        base.mkdir(parents=True)
        try:
            work=base/'ideiglenes';work.mkdir()
            engine,process,handle=FinalCleanupTests().owner()
            engine.check=Mock()
            def exited(*args,**kwargs):
                process.poll.return_value=0
                return 0
            process.wait.side_effect=exited
            project=SimpleNamespace(ai_root=str(base),enhancement='Finom képjavítás',
                ai_quality='Részletes • 1280×704')
            def prepare(engine,portrait,config,scene,*args):
                picture=Image.new('RGB',(32,32),(40,70,100))
                picture.paste((180,140,80),(8,8,24,24));picture.save(config['output'])
                return {'face_found':True}
            def native(*args,**kwargs):
                scene=Path(kwargs['scene_dir']);frames=scene/'frames';frames.mkdir(parents=True)
                size=tuple(kwargs['canvas']);count=round(args[6]*16)
                for number in range(count):
                    picture=Image.new('RGB',size,(number,70,100))
                    picture.paste((180,140,80),(8,8,24,24))
                    picture.save(frames/('frame_%05d.png'%number))
                (scene/'video_settings.json').write_text(json.dumps({'width':size[0],'height':size[1],
                    'fps':16,'frames':count,'decoder':'VAEDecodeTiled'}))
            def interpolate(*args,force_cpu=False,**kwargs):
                if not force_cpu:self.assertEqual(process.poll(),0)
                return base/'result.mp4'
            with patch.object(realistic_video,'engine_files',return_value=base),\
                 patch.object(realistic_video,'_worker',side_effect=prepare),\
                 patch('image_motion.source_dimensions',return_value=(32,32)),\
                 patch.object(comfy_video,'generate',side_effect=native),\
                 patch.object(comfy_video,'_request',return_value=queue) as request,\
                 patch('motion_smooth.generate',side_effect=interpolate) as smooth:
                result=realistic_video.generate(engine,project,'','source.jpg',0,work,5)
            self.assertEqual(result,base/'result.mp4');smooth.assert_called_once()
            return request.call_args_list,smooth.call_args.kwargs['force_cpu'],process,handle
        finally:
            assert base.resolve().is_relative_to((ROOT/'work/cleanup_tests').resolve())
            shutil.rmtree(base)

    def test_malformed_queue_preserves_server_and_uses_cpu(self):
        for queue in malformed_queues():
            with self.subTest(queue=queue):
                calls,cpu,process,_=self.run_scene(queue)
                self.assertEqual([call.args[1] for call in calls],['/queue'])
                self.assertTrue(cpu)
                process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_busy_queue_preserves_server_and_uses_cpu(self):
        calls,cpu,process,_=self.run_scene(FinalCleanupTests.queue(running=['other']))
        self.assertEqual([call.args[1] for call in calls],['/queue'])
        self.assertTrue(cpu)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_proven_idle_child_exits_before_gpu_interpolation(self):
        calls,cpu,process,handle=self.run_scene(FinalCleanupTests.queue())
        self.assertEqual([call.args[1] for call in calls],['/queue','/queue'])
        self.assertFalse(cpu)
        process.terminate.assert_called_once();process.wait.assert_called_once()
        self.assertEqual(process.poll(),0)
        handle.close.assert_called_once()

if __name__=='__main__':unittest.main()
