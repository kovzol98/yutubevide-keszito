"""Regression: no detached head layers and no causal-VAE tile boundaries."""
import importlib.util,json,shutil,sys,unittest,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'work/v2_source'))
import ytmuhely as app,comfy_video,realistic_video

class DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info=json.loads((ROOT/'work/realism/quality_object_info.json').read_text(encoding='utf-8'))
        cls.reference=cls.info['LoadImage']['input']['required']['image'][0][0]
    def graph(self,width,height,frames,quality=True):
        builder=realistic_video.workflow if quality else comfy_video._workflow
        return builder(self.info,'A person moves naturally.',width,height,frames,4,42,'test',self.reference,
            image_motion='Teljes alak • tánc')
    def test_normal_decodes_one_continuous_time_sequence_in_both_engines(self):
        for quality in (True,False):
            for frames in (33,81,161):
                with self.subTest(quality=quality,frames=frames):
                    node=self.graph(480,640,frames,quality)['9']
                    self.assertEqual(node['class_type'],'VAEDecodeTiled')
                    self.assertGreaterEqual(node['inputs']['temporal_size']//4,(frames+3)//4)
                    self.assertEqual(node['inputs']['tile_size'],512)
    def test_detailed_tiles_only_in_space_for_the_whole_scene(self):
        for frames in (33,81,161,1921,2881):
            with self.subTest(frames=frames):
                node=self.graph(832,1088,frames)['9'];inputs=node['inputs']
                self.assertEqual(node['class_type'],'VAEDecodeTiled')
                self.assertEqual(inputs['tile_size'],512)
                self.assertGreaterEqual(inputs['temporal_size']//4,(frames+3)//4)
                self.assertLessEqual(inputs['temporal_size'],4096)
    def test_overlong_temporal_window_is_rejected_before_sampling(self):
        with self.assertRaisesRegex(ValueError,'túl hosszú'):self.graph(832,1088,5001)
    def test_quality_checkpoints_the_exact_sample_before_decoding(self):
        graph=self.graph(480,640,81)
        self.assertEqual(graph['15']['class_type'],'SaveLatent')
        self.assertEqual(graph['15']['inputs']['samples'],['7',0])
        self.assertEqual(graph['9']['inputs']['samples'],['15',0])
        self.assertEqual(self.info['SaveLatent']['output'],['LATENT'])
    def test_nodes_and_connections_match_the_installed_api_schema(self):
        for size in ((480,640),(832,1088)):
            graph=self.graph(*size,81)
            for node in graph.values():
                schema=self.info[node['class_type']]
                fields={**schema['input'].get('required',{}),**schema['input'].get('optional',{})}
                self.assertLessEqual(set(node['inputs']),set(fields))
                for name,value in node['inputs'].items():
                    if isinstance(value,list) and len(value)==2 and value[0] in graph:
                        output=self.info[graph[value[0]]['class_type']]['output'][value[1]]
                        self.assertEqual(fields[name][0],output)

class WholeSceneTests(unittest.TestCase):
    @staticmethod
    def prepared_photo(config):
        picture=app.Image.new('RGB',(48,64),(45,70,95))
        picture.paste((185,145,85),(12,16,36,48))
        picture.save(config['output'])

    @staticmethod
    def native_fixture(work,duration,kwargs):
        scene=Path(kwargs['scene_dir']);frames=scene/'frames'
        frames.mkdir(parents=True,exist_ok=True)
        canvas=tuple(kwargs['canvas']);count=round(duration*16)
        for number in range(count):
            picture=app.Image.new('RGB',canvas,(number,60,80))
            picture.paste((190,150,90),(canvas[0]//4,canvas[1]//4,canvas[0]*3//4,canvas[1]*3//4))
            picture.save(frames/('frame_%05d.png'%number))
        (scene/'video_settings.json').write_text(json.dumps({'width':canvas[0],
            'height':canvas[1],'fps':16,'frames':count,'decoder':'VAEDecode'}))
        return scene/'video.mp4'

    def test_cache_cleanup_failure_does_not_discard_a_completed_native_scene(self):
        for failure in (OSError('Server closed after generation'),RuntimeError('Queue unavailable')):
            with self.subTest(error=type(failure).__name__):
                base=ROOT/'work/coherent_tests'/uuid.uuid4().hex
                base.mkdir(parents=True)
                try:
                    work=base/'ideiglenes';work.mkdir();logs=[]
                    engine=app.Engine(logs.append)
                    engine._comfy_process=object()
                    engine._comfy_url='http://127.0.0.1:18888'
                    project=app.Project(ai_root=str(base))
                    def source_worker(engine,portrait,config,scene,*args):
                        self.assertEqual(config['action'],'prepare')
                        self.prepared_photo(config)
                        return {'face_found':True}
                    def native(engine,project,text,image,index,work,duration,**kwargs):
                        return self.native_fixture(work,duration,kwargs)
                    def smooth(engine,root,frames,fps,scene,*,force_cpu=False):
                        self.assertTrue(force_cpu)
                        self.assertEqual(frames,scene/'frames')
                        self.assertEqual(len(list(frames.glob('frame_*.png'))),80)
                        self.assertEqual(fps,16)
                        result=scene/'final.mp4';result.write_bytes(b'completed interpolation')
                        return result
                    with patch.object(realistic_video,'engine_files',return_value=base),patch.object(realistic_video,'_worker',source_worker),patch.object(comfy_video,'generate',native),patch.object(comfy_video,'_request',side_effect=failure) as request,patch('motion_smooth.generate',side_effect=smooth) as interpolation:
                        result=realistic_video.generate(engine,project,'','photo.jpg',0,work,5)
                    self.assertEqual(result.read_bytes(),b'completed interpolation')
                    request.assert_called_once_with(engine._comfy_url,'/queue',timeout=5)
                    interpolation.assert_called_once()
                    metadata=json.loads((result.parent/'mozgas_egyseg.json').read_text(encoding='utf-8'))
                    self.assertEqual(metadata['native_frames'],80)
                    self.assertFalse(metadata['head_replacement']);self.assertFalse(metadata['static_background'])
                    self.assertTrue(any('CPU-val folytatódik' in line for line in logs))
                finally:
                    assert base.resolve().is_relative_to((ROOT/'work/coherent_tests').resolve())
                    shutil.rmtree(base)
    def test_quality_uses_native_video_frames_without_a_second_head_or_background(self):
        base=ROOT/'work/coherent_tests'/uuid.uuid4().hex
        base.mkdir(parents=True)
        try:
            work=base/'ideiglenes';work.mkdir();calls=[];originals=[]
            def source_worker(engine,portrait,config,scene,*args):
                calls.append(config['action']);self.prepared_photo(config)
                return {'face_found':True}
            def native(engine,project,text,image,index,work,duration,**kwargs):
                result=self.native_fixture(work,duration,kwargs)
                originals.extend(path.read_bytes() for path in sorted((result.parent/'frames').glob('frame_*.png')))
                return result
            def smooth(engine,root,frames,fps,scene,*,force_cpu=False):
                self.assertTrue(force_cpu)
                self.assertEqual(frames,scene/'frames')
                self.assertEqual((frames/'frame_00000.png').read_bytes(),originals[0])
                self.assertTrue(all(path.read_bytes() in originals for path in frames.glob('frame_*.png')))
                self.assertEqual(len(list(frames.glob('frame_*.png'))),80)
                self.assertEqual(fps,16);return scene/'final.mp4'
            with patch.object(realistic_video,'engine_files',return_value=base),patch.object(realistic_video,'_worker',source_worker),patch.object(comfy_video,'generate',native),patch('motion_smooth.generate',smooth):
                result=realistic_video.generate(app.Engine(),app.Project(ai_root=str(base)),'','photo.jpg',0,work,5)
            self.assertEqual(calls,['prepare'])
            metadata=json.loads((result.parent/'mozgas_egyseg.json').read_text(encoding='utf-8'))
            self.assertFalse(metadata['head_replacement']);self.assertFalse(metadata['static_background'])
            self.assertEqual(metadata['native_frames'],80)
        finally:
            assert base.resolve().is_relative_to((ROOT/'work/coherent_tests').resolve())
            shutil.rmtree(base)
    def test_removed_head_compositor_cannot_be_selected_via_worker_config(self):
        code=(ROOT/'work/v2_source/quality_face_worker.py').read_text(encoding='utf-8')
        self.assertNotIn('def protect(',code)
        self.assertNotIn('load_wrapper',code)
        self.assertNotIn('DetailPreserver',code)
    def test_old_head_overlay_is_rejected_before_loading_models(self):
        import quality_face_worker as worker
        with patch.object(sys,'argv',['worker','legacy.json']),patch.object(Path,'read_text',return_value='{"action":"protect"}'),patch.object(worker,'initialize') as initialize:
            with self.assertRaisesRegex(ValueError,'fejráillesztés megszűnt'):worker.main()
            initialize.assert_not_called()

if __name__=='__main__':unittest.main()
