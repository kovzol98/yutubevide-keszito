"""Real FFmpeg exports around model-free Engine motion/framing integration."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
from PIL import Image
from imageio_ffmpeg import read_frames
import comfy_video
from image_motion import SCENE_MOTION
from reference_framing import FULL_FRAME,SUBJECT_FRAME
import realistic_video
import portrait_video
import ytmuhely as app


class MotionBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent=ROOT/'work/v2_tests/motion_build_fixtures'
        cls.folder=cls.parent/uuid.uuid4().hex
        cls.folder.mkdir(parents=True)
        cls.photo=cls.folder/'Reference photo.png'
        Image.new('RGB',(192,144),'#7c6b58').save(cls.photo)
        cls.second=cls.folder/'Second photo.png'
        Image.new('RGB',(320,180),'#8d7461').save(cls.second)
        cls.native=cls.folder/'Native scene.mp4'
        subprocess.run([app.ffmpeg(),'-hide_banner','-nostdin','-v','error','-y',
            '-f','lavfi','-i','testsrc2=size=96x64:rate=30','-t','1',
            '-c:v','libx264','-threads','2','-preset','ultrafast',
            '-pix_fmt','yuv420p','-an',str(cls.native)],check=True,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=app.CREATE_NO_WINDOW,timeout=60)

    @classmethod
    def tearDownClass(cls):
        assert cls.folder.resolve().is_relative_to(cls.parent.resolve())
        shutil.rmtree(cls.folder)

    def project(self,**changes):
        # A short title also exercises clones inside a deeply nested Windows
        # workspace without exceeding the process working-directory limit.
        values=dict(title='Motion test',text='Natural movement in a single scene.',
            images=[str(self.photo)],video_prompt='The person slowly lowers one arm.',
            visual_mode='AI-videó • helyi',image_motion=SCENE_MOTION,
            image_engine=realistic_video.QUALITY_ENGINE,framing=FULL_FRAME,
            narration='Nincs narráció',encoder='CPU • összes mag',seconds=1,
            enhancement='Eredeti megjelenés',format='Fotó eredeti aránya',
            output=str(self.folder/('output_'+uuid.uuid4().hex)),subtitles=True)
        values.update(changes)
        return app.Project(**values)

    def build_real_export(self,project,prepared_sizes,*,face_found=True):
        engine=app.Engine()
        exports=[]
        original_run=engine.run

        def run(args,cwd=None,**kwargs):
            if '-vf' in args:
                caption=Path(cwd)/'caption_0000.ass'
                # Record every actual caption before Engine removes its work
                # folder. The filename is carried by the actual filter graph.
                filter_text=args[args.index('-vf')+1]
                subtitle=next((piece[4:] for piece in filter_text.split(',') if piece.startswith('ass=')),None)
                if subtitle is not None:caption=Path(cwd)/subtitle
                exports.append(dict(filter=filter_text,caption=caption.read_text(encoding='utf-8')))
            return original_run(args,cwd=cwd,**kwargs)

        def generated(p,text,image,index,work,duration):
            scene=Path(work).parent/'AI_jelenetek'/('jelenet_%03d'%(index+1))
            scene.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',prepared_sizes[index],'#9a7e61').save(scene/'elokeszitett_foto.png')
            (scene/'forras_arc.json').write_text(json.dumps(dict(face_found=face_found,
                prepared_size=list(prepared_sizes[index]),source_crop=dict(
                    applied=face_found,reason='subject_crop' if face_found else 'no_face'))),encoding='utf-8')
            return self.native

        with ExitStack() as stack:
            # Only the expensive model stage and installation lookup are
            # substituted. Rendering, subtitles, soundtrack cache and final
            # MP4 export execute the product's real FFmpeg pipeline.
            stack.enter_context(patch.object(realistic_video,'engine_files',return_value=self.folder))
            stack.enter_context(patch('gpu_resources.probe_gpu_users',return_value=[]))
            stack.enter_context(patch.object(engine,'generate_clip',side_effect=generated))
            stack.enter_context(patch.object(engine,'run',side_effect=run))
            stack.enter_context(patch.object(app.os,'cpu_count',return_value=2))
            destination=engine.build(project)
        reader=read_frames(str(destination/'video.mp4'))
        try:
            metadata=next(reader)
            frames=sum(1 for _ in reader)
        finally:reader.close()
        self.assertEqual(frames,len(prepared_sizes)*30)
        self.assertTrue((destination/'hang_alap/alapvideo.mp4').is_file())
        return destination,metadata,exports

    def assert_rejected_before_export(self,project,message):
        engine=app.Engine()
        with ExitStack() as stack:
            installers=[stack.enter_context(patch.object(module,'engine_files',return_value=self.folder))
                        for module in (realistic_video,portrait_video,comfy_video)]
            generate=stack.enter_context(patch.object(engine,'generate_clip'))
            render=stack.enter_context(patch.object(engine,'run'))
            with self.assertRaisesRegex(ValueError,message):engine.build(project)
            generate.assert_not_called()
            render.assert_not_called()
        self.assertFalse(Path(project.output).exists())
        return installers

    def test_blank_description_does_not_generate_export_or_create_destination(self):
        self.assert_rejected_before_export(self.project(video_prompt=' \n\t '),'konkrét mozdulat')

    def test_explicit_action_description_allows_a_real_complete_export(self):
        destination,metadata,exports=self.build_real_export(self.project(),[(192,144)])
        self.assertEqual(metadata['size'],(192,144))
        self.assertEqual(len(exports),1)
        self.assertIn('PlayResX: 192',exports[0]['caption'])
        self.assertIn('Natural movement',exports[0]['caption'])
        self.assertTrue((destination/'video.mp4').is_file())

    def test_source_aspect_and_all_captions_use_first_actual_prepared_crop(self):
        project=self.project(framing=SUBJECT_FRAME,images=[str(self.photo),str(self.second)])
        _,metadata,exports=self.build_real_export(project,[(108,144),(144,108)])
        self.assertEqual(metadata['size'],(108,144))
        self.assertEqual(len(exports),2)
        for item in exports:
            self.assertIn('scale=108:144:',item['filter'])
            self.assertIn('PlayResX: 108\nPlayResY: 144',item['caption'])

    def test_face_not_found_preserves_actual_full_prepared_photo_export(self):
        project=self.project(framing=SUBJECT_FRAME)
        _,metadata,exports=self.build_real_export(project,[(192,144)],face_found=False)
        self.assertEqual(metadata['size'],(192,144))
        self.assertIn('PlayResX: 192\nPlayResY: 144',exports[0]['caption'])

    def test_fixed_export_format_does_not_adopt_the_prepared_crop_size(self):
        project=self.project(framing=SUBJECT_FRAME,format='720p • 16:9')
        _,metadata,exports=self.build_real_export(project,[(108,144)])
        self.assertEqual(metadata['size'],(1280,720))
        self.assertIn('PlayResX: 1280\nPlayResY: 720',exports[0]['caption'])

    def test_legacy_engine_cannot_apply_quality_only_subject_crop(self):
        self.assert_rejected_before_export(self.project(framing=SUBJECT_FRAME,
            image_engine=realistic_video.LEGACY_ENGINE),'Élethű motort')

    def test_portrait_mode_cannot_apply_scene_subject_crop(self):
        self.assert_rejected_before_export(self.project(framing=SUBJECT_FRAME,
            image_motion=portrait_video.PORTRAIT_MOTION),'Élethű motort')


if __name__=='__main__':unittest.main()
