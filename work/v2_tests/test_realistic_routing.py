import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v2_source'))
import ytmuhely as app
import realistic_video as quality
from image_motion import DEFAULT_IMAGE_MOTION,SCENE_MOTION

class RoutingTests(unittest.TestCase):
    def test_photo_uses_quality_and_explicit_legacy_remains_available(self):
        engine=app.Engine();project=app.Project()
        with patch('realistic_video.generate',return_value='quality') as new,patch('comfy_video.generate',return_value='legacy') as old:
            self.assertEqual(engine.generate_clip(project,'','photo.jpg',0,Path('.'),2),'quality')
            self.assertEqual(engine.generate_clip(project,'',None,0,Path('.'),2),'legacy')
            project.image_engine=quality.LEGACY_ENGINE
            self.assertEqual(engine.generate_clip(project,'','photo.jpg',0,Path('.'),2),'legacy')
            self.assertEqual(new.call_count,1);self.assertEqual(old.call_count,2)
    def test_head_motion_keeps_the_faster_portrait_route(self):
        project=app.Project(image_motion=DEFAULT_IMAGE_MOTION)
        with patch('portrait_video.generate',return_value='portrait'),patch('realistic_video.generate') as new:
            self.assertEqual(app.Engine().generate_clip(project,'','photo.jpg',0,Path('.'),5),'portrait')
            new.assert_not_called()
    def test_ownership_is_forwarded_to_the_actual_engine(self):
        engine=app.Engine();proxy=quality._CoreProgress(engine);process=object()
        proxy._comfy_process=process
        self.assertIs(engine._comfy_process,process)
        self.assertIs(proxy.cancel,engine.cancel)
        engine.cancel.set()
        with self.assertRaises(app.Cancelled):proxy.check()
    def test_progress_reserves_time_for_interpolation(self):
        reported=[];engine=app.Engine(progress=reported.append);proxy=quality._CoreProgress(engine)
        proxy.report_scene_progress(100)
        self.assertEqual(reported[-1],85.5)
    def test_invalid_quality_choice_fails_before_starting_a_model(self):
        with self.assertRaisesRegex(ValueError,'fotóanimációs motor'):
            app.Engine().build(app.Project(image_engine='unknown'))

if __name__=='__main__':unittest.main()
