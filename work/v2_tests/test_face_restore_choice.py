"""Face reconstruction is an explicit choice, separate from export sharpness."""
from pathlib import Path
from types import SimpleNamespace
import json,shutil,sys,unittest,uuid
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
import realistic_video as quality
import ytmuhely as app

class FaceChoiceTests(unittest.TestCase):
    def test_new_and_legacy_projects_do_not_reconstruct_source_face(self):
        self.assertIs(app.Project().face_restore,False)
        self.assertIs(app.Project(**{'title':'Régi projekt'}).face_restore,False)

    def test_invalid_flag_is_rejected_before_any_external_process(self):
        with patch('realistic_video.engine_files') as files,patch('subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError,'arcjavítás'):
                app.Engine().build(app.Project(face_restore='false'))
        files.assert_not_called();process.assert_not_called()

    def test_worker_choice_is_independent_of_export_enhancement(self):
        parent=ROOT/'work/v2_tests/face_choice_fixtures';folder=parent/uuid.uuid4().hex
        folder.mkdir(parents=True);work=folder/'ideiglenes';work.mkdir()
        try:
            for face_restore in (False,True):
                for enhancement in ('Finom képjavítás','Eredeti megjelenés'):
                    with self.subTest(face_restore=face_restore,enhancement=enhancement):
                        project=app.Project(face_restore=face_restore,enhancement=enhancement,ai_root=str(folder))
                        engine=app.Engine()
                        def prepare(engine,portrait,config,scene,*args):
                            picture=app.Image.new('RGB',(32,32),(40,70,100))
                            picture.paste((180,140,80),(8,8,24,24));picture.save(config['output'])
                            return {'face_found':True}
                        def native(*args,**kwargs):
                            scene=Path(kwargs['scene_dir']);frames=scene/'frames';frames.mkdir(parents=True,exist_ok=True)
                            size=tuple(kwargs['canvas']);count=round(args[6]*16)
                            for number in range(count):
                                picture=app.Image.new('RGB',size,(number,70,100))
                                picture.paste((180,140,80),(8,8,24,24))
                                picture.save(frames/('frame_%05d.png'%number))
                            (scene/'video_settings.json').write_text(json.dumps(dict(width=size[0],height=size[1],frames=count,
                                fps=16,decoder='VAEDecodeTiled')),encoding='utf-8')
                        with (patch.object(quality,'engine_files',return_value=folder),
                              patch.object(quality,'_worker',side_effect=prepare) as worker,
                              patch('image_motion.source_dimensions',return_value=(32,32)),
                              patch.object(quality.comfy_video,'generate',side_effect=native),
                              patch('motion_smooth.generate',return_value=folder/'scene.mp4')):
                            quality.generate(engine,project,'','source.jpg',0,work,2)
                        self.assertIs(worker.call_args.args[2]['enhanced'],face_restore)
        finally:
            assert folder.resolve().is_relative_to(parent.resolve());shutil.rmtree(folder)

if __name__=='__main__':unittest.main()
