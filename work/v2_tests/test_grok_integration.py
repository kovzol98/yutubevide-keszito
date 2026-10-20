"""Grok routing with an offline video stand-in and actual local FFmpeg export."""
from pathlib import Path
from unittest.mock import patch
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
from PIL import Image
import imageio_ffmpeg
import ytmuhely as app
import grok_video

FAKE_KEY='test-only-not-a-real-api-key-805427'


class GrokRoutingTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'work/grok_tests'/uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.photo=self.base/'photo.png'
        Image.new('RGB',(64,96),'#bd9869').save(self.photo)
        self.engine=app.Engine()
        self.engine.grok_api_key=FAKE_KEY

    def tearDown(self):
        assert self.base.resolve().is_relative_to((ROOT/'work/grok_tests').resolve())
        shutil.rmtree(self.base)

    def project(self,**kwargs):
        values=dict(title='Offline Grok integration',visual_mode=app.GROK_MODE,
            images=[str(self.photo)],video_prompt='A person makes one gentle sidestep.',
            narration='Nincs narráció',seconds=1,output=str(self.base/'output'),
            format='720p • 16:9',encoder='CPU',subtitles=False)
        values.update(kwargs)
        return app.Project(**values)

    def test_new_project_stays_local_and_has_no_serialized_credential(self):
        project=app.Project()
        self.assertEqual(project.visual_mode,'AI-videó • helyi')
        self.assertEqual(project.grok_max_scenes,1)
        data=app.asdict(project)
        self.assertNotIn('grok_api_key',data)
        self.assertNotIn(FAKE_KEY,json.dumps(data))

    def test_missing_key_rejects_before_adapter_or_local_setup(self):
        self.engine.grok_api_key=''
        with patch.dict(os.environ,{'XAI_API_KEY':''}),patch.object(grok_video,'generate_scene') as generate:
            with self.assertRaisesRegex(ValueError,'API-kulcs'):
                self.engine.build(self.project())
        generate.assert_not_called()

    def test_scene_cap_rejects_before_narration_or_paid_request(self):
        with patch.object(self.engine,'narrate') as narrate,patch.object(grok_video,'generate_scene') as generate:
            with self.assertRaisesRegex(ValueError,'2 fizetős Grok-jelenet'):
                self.engine.build(self.project(images=[str(self.photo)]*2,text='A story.',narration='Angol női LJ • offline'))
        narrate.assert_not_called();generate.assert_not_called()

    def test_invalid_cap_never_submits(self):
        for cap in (True,0,101,1.5,'2'):
            with self.subTest(cap=cap),patch.object(grok_video,'generate_scene') as generate:
                with self.assertRaises(ValueError):self.engine.build(self.project(grok_max_scenes=cap))
                generate.assert_not_called()

    def test_later_long_narration_rejects_before_any_paid_request(self):
        text='One two three four five six seven eight nine ten eleven twelve.\n\nAnother scene.'
        with patch.object(self.engine,'codec',return_value=[]),patch.object(self.engine,'narrate'),\
             patch.object(self.engine,'duration',side_effect=[3,18]),patch.object(grok_video,'generate_scene') as generate:
            with self.assertRaisesRegex(ValueError,'1–15'):
                self.engine.build(self.project(text=text,narration='Angol női LJ • offline',grok_max_scenes=2))
        generate.assert_not_called()

    def test_text_route_uses_shorts_ratio_without_any_local_model(self):
        work=self.base/'work';work.mkdir()
        with patch.object(grok_video,'generate_scene',return_value=self.base/'video.mp4') as generate,\
             patch.object(self.engine,'verify_grok_video') as verify,\
             patch('comfy_video.generate',side_effect=AssertionError('Local model started')):
            result=self.engine.generate_clip(self.project(images=[],format='Shorts • 9:16'),'An English scene.',None,0,work,5)
        self.assertIsNone(generate.call_args.args[1])
        self.assertEqual(generate.call_args.kwargs['aspect_ratio'],'9:16')
        self.assertEqual(generate.call_args.args[-1],FAKE_KEY)
        verify.assert_called_once_with(result,5)

    def test_actual_local_export_with_offline_cloud_video_and_music(self):
        movie=self.base/'offline_cloud_fixture.mp4'
        music=self.base/'music.wav'
        commands=[
            [app.ffmpeg(),'-v','error','-f','lavfi','-i','testsrc2=size=64x96:rate=16',
             '-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(movie)],
            [app.ffmpeg(),'-v','error','-f','lavfi','-i','sine=frequency=330:sample_rate=48000',
             '-t','1',str(music)]]
        for command in commands:
            subprocess.run(command,check=True,capture_output=True,creationflags=app.CREATE_NO_WINDOW)
        with patch.object(grok_video,'generate_scene',return_value=movie) as generate,\
             patch.object(self.engine,'run',wraps=self.engine.run) as commands,\
             patch('realistic_video.engine_files',side_effect=AssertionError('Local model setup')),\
             patch('comfy_video.engine_files',side_effect=AssertionError('Comfy setup')),\
             patch('gpu_resources.probe_gpu_users',side_effect=AssertionError('Local GPU probe')):
            destination=self.engine.build(self.project(music=str(music)))
        self.assertEqual(generate.call_count,1)
        output=destination/'video.mp4'
        count,seconds=imageio_ffmpeg.count_frames_and_secs(str(output))
        self.assertEqual(count,30)
        self.assertLessEqual(abs(seconds-1),1/30+.01)  # AAC padding can extend the container.
        filters=[call.args[0][call.args[0].index('-vf')+1] for call in commands.call_args_list if '-vf' in call.args[0]]
        self.assertTrue(any('tpad=stop_mode=clone' in value for value in filters))
        self.assertFalse(any('hqdn3d' in value for value in filters))
        with self.assertRaisesRegex(RuntimeError,'rövidebb'):
            self.engine.verify_grok_video(movie,2)
        for path in destination.rglob('*'):
            if path.is_file() and path.suffix in ('.json','.txt','.log','.srt'):
                self.assertNotIn(FAKE_KEY,path.read_text(encoding='utf-8'))
        self.assertTrue((destination/'hang_alap').is_dir())

    def test_transport_header_is_not_accepted_as_decoded_video(self):
        broken=self.base/'fake.mp4'
        broken.write_bytes(b'\x00\x00\x00\x20ftyp'+b'fake'*40)
        with self.assertRaises((ValueError,RuntimeError)):
            self.engine.verify_grok_video(broken)

    def test_cloud_plan_does_not_claim_local_model_or_native_dimensions(self):
        from video_plan import describe_video_plan
        plan=describe_video_plan(app.GROK_MODE,'Jelenetleírás szerinti mozgás',
            'Élethű • arc és fény','Részletes • 1280×704',[],
            'Shorts • 9:16',grok_resolution='1080p')
        self.assertEqual(plan.pipeline,'grok-text')
        self.assertIsNone(plan.native_size)
        self.assertIn('1080p',plan.generation_hint)


if __name__=='__main__':unittest.main(verbosity=2)
