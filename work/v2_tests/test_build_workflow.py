"""Whole imported clips, original sound and early media failure through Engine."""
from array import array
from pathlib import Path
import json,math,shutil,subprocess,sys,unittest,uuid,wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
from PIL import Image
import imageio_ffmpeg
import ytmuhely as app
import soundtrack_cache


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent=ROOT/'work/v2_tests/build_workflow_fixtures'
        cls.folder=cls.parent/uuid.uuid4().hex;cls.folder.mkdir(parents=True)
        cls.engine=app.Engine();cls.ffmpeg=app.ffmpeg()
        cls.first=cls.folder/'Első videó.mp4';cls.second=cls.folder/'Második videó.mp4'
        for path,freq,duration in ((cls.first,440,.6),(cls.second,660,1)):
            cls.run_ffmpeg(['-f','lavfi','-i','testsrc2=size=96x64:rate=30',
                '-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=48000',
                '-t',str(duration),'-c:v','libx264','-preset','fast','-threads','2',
                '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',path])
        cls.voice=cls.folder/'Saját hang.wav'
        cls.music=cls.folder/'Új zene.wav'
        cls.delayed=cls.folder/'Késleltetett eredeti hang.mp4'
        cls.run_ffmpeg(['-f','lavfi','-i','testsrc2=size=96x64:rate=30:duration=1.2',
            '-itsoffset','0.5','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.7',
            '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','fast','-threads','2',
            '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',cls.delayed])
        for path,freq in ((cls.voice,880),(cls.music,220)):
            cls.run_ffmpeg(['-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=48000:duration=1.2',
                '-c:a','pcm_s16le',path])
        app.SIZES['Tesztméret']=(96,64)

    @classmethod
    def tearDownClass(cls):
        app.SIZES.pop('Tesztméret',None)
        resolved=cls.folder.resolve()
        if resolved.is_relative_to(cls.parent.resolve()):shutil.rmtree(resolved)

    @classmethod
    def run_ffmpeg(cls,args):
        return subprocess.run([cls.ffmpeg,'-v','error','-nostdin','-y',*[str(a) for a in args]],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)

    def project(self,**kwargs):
        values=dict(title='Videópróba',visual_mode='Saját videók',videos=[str(self.first)],
            narration='Nincs narráció',format='Tesztméret',encoder='CPU • összes mag',
            output=str(self.folder/'eredmenyek'),enhancement='Eredeti megjelenés',subtitles=False)
        values.update(kwargs);return app.Project(**values)

    def pcm(self,path):
        data=self.run_ffmpeg(['-i',path,'-map','0:a:0','-ar','48000','-ac','1','-f','s16le','-']).stdout
        samples=array('h');samples.frombytes(data);return samples

    def amplitude(self,samples,freq,start=.15,end=.35):
        data=samples[round(start*48000):round(end*48000)];self.assertTrue(data)
        real=sum(v*math.cos(2*math.pi*freq*i/48000) for i,v in enumerate(data))
        imag=sum(v*math.sin(2*math.pi*freq*i/48000) for i,v in enumerate(data))
        return 2*math.hypot(real,imag)/len(data)

    def frame_count(self,path):
        frames=imageio_ffmpeg.read_frames(str(path),output_params=['-fps_mode','passthrough'])
        next(frames);return sum(1 for _ in frames)

    def test_all_photos_are_decoded_before_first_ai_scene(self):
        good=self.folder/'Jó kép.png';Image.new('RGB',(96,64),'tan').save(good)
        broken=self.folder/'Hibás kép.jpg';broken.write_bytes(b'not a photo')
        project=self.project(visual_mode='AI-videó • helyi',videos=[],images=[str(good),str(broken)])
        with (patch('realistic_video.engine_files'),patch.object(app.Engine,'generate_clip') as generate,
                patch.object(app.Engine,'codec') as codec):
            with self.assertRaisesRegex(ValueError,'kép nem olvasható'):
                app.Engine().build(project)
        generate.assert_not_called();codec.assert_not_called()

    def test_bad_second_video_fails_before_first_scene_export(self):
        broken=self.folder/'Hibás videó.mp4';broken.write_bytes(b'not a video')
        with patch.object(app.Engine,'codec') as codec:
            with self.assertRaises(ValueError):
                app.Engine().build(self.project(videos=[str(self.first),str(broken)]))
        codec.assert_not_called()

    def test_empty_own_narration_is_rejected_before_rendering(self):
        empty=self.folder/'Üres narráció.wav'
        with wave.open(str(empty),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(48000)
        with patch.object(app.Engine,'codec') as codec:
            with self.assertRaisesRegex(ValueError,'saját narráció nem tartalmaz'):
                app.Engine().build(self.project(narration='Saját hangfájl',audio=str(empty)))
        codec.assert_not_called()
        with self.assertRaisesRegex(ValueError,'narráció hangfájlja üres'):
            app.Engine().duration(empty,self.folder)
        self.assertFalse((self.folder/'measure.wav').exists())

    def test_unknown_narration_never_falls_back_to_windows_default_voice(self):
        with patch.object(app.Engine,'run') as run:
            with self.assertRaisesRegex(ValueError,'támogatott narrációs'):
                app.Engine().build(self.project(narration='Unknown Windows voice',text='English speech.'))
            with self.assertRaisesRegex(ValueError,'támogatott angol narrációs'):
                app.Engine().narrate('English speech.',self.folder/'unknown_voice.mp3','Unknown Windows voice')
        run.assert_not_called()

    def test_original_sound_and_full_lengths_survive_two_clips(self):
        dest=app.Engine().build(self.project(videos=[str(self.first),str(self.second)]))
        self.assertEqual(self.frame_count(dest/'video.mp4'),48)
        samples=self.pcm(dest/'video.mp4')
        self.assertGreater(self.amplitude(samples,440),2500)
        self.assertGreater(self.amplitude(samples,660,.85,1.05),2500)
        self.assertTrue((dest/'hang_alap/hang_alap.json').is_file())
        import scene_library
        library=scene_library.load(dest)
        self.assertEqual(library.status,'complete')
        self.assertEqual([item.frames for item in library.scenes],[18,30])
        self.assertTrue(all(item.video.is_file() for item in library.scenes))
        self.assertFalse((dest/'ideiglenes').exists())

    def test_muting_and_fixed_length_are_explicit_options(self):
        dest=app.Engine().build(self.project(video_volume=0,use_video_length=False,seconds=1))
        self.assertEqual(self.frame_count(dest/'video.mp4'),30)
        samples=self.pcm(dest/'video.mp4')
        self.assertLess(max(abs(v) for v in samples),2)

    def test_long_caption_does_not_repeat_imported_video(self):
        text='One two three four five six seven eight nine ten eleven twelve thirteen fourteen.'
        dest=app.Engine().build(self.project(text=text,subtitles=True))
        self.assertEqual(self.frame_count(dest/'video.mp4'),18)
        captions=(dest/'felirat.srt').read_text(encoding='utf-8')
        self.assertIn('fourteen',captions)

    def test_original_sound_and_own_narration_survive_cached_remix(self):
        dest=app.Engine().build(self.project(narration='Saját hangfájl',audio=str(self.voice)))
        original=(dest/'video.mp4').read_bytes()
        samples=self.pcm(dest/'video.mp4')
        self.assertGreater(self.amplitude(samples,440),1800)
        self.assertGreater(self.amplitude(samples,880),1800)
        source=soundtrack_cache.load_source(dest)
        self.assertTrue(source.mix_narration)
        with patch.object(app.Engine,'generate_clip',side_effect=AssertionError('Remix must not regenerate AI')):
            result=soundtrack_cache.remix(app.Engine(),dest,music=str(self.music),music_volume=40)
        self.assertEqual((dest/'video.mp4').read_bytes(),original)
        samples=self.pcm(result)
        self.assertGreater(self.amplitude(samples,440,.4,.6),1800)
        self.assertGreater(self.amplitude(samples,880,.4,.6),1800)
        self.assertGreater(self.amplitude(samples,220,.4,.6),600)
        self.assertEqual(self.frame_count(result),36)

    def test_delayed_original_audio_stays_in_sync_through_export_and_music_remix(self):
        dest=app.Engine().build(self.project(videos=[str(self.delayed)]))
        result=soundtrack_cache.remix(app.Engine(),dest,music=str(self.music),music_volume=40)
        for movie in (dest/'video.mp4',result):
            with self.subTest(movie=movie.name):
                self.assertEqual(self.frame_count(movie),36)
                samples=self.pcm(movie)
                self.assertLess(self.amplitude(samples,440,.1,.3),20)
                self.assertGreater(self.amplitude(samples,440,.65,.85),2500)
        self.assertGreater(self.amplitude(self.pcm(result),220,.1,.3),300)

    def test_delayed_own_narration_keeps_initial_silence_and_full_voice_in_cached_remix(self):
        voice=str(self.delayed)
        duration=app.Engine().duration(voice,self.folder)
        self.assertGreater(duration,1.19)
        self.assertLess(duration,1.24)
        dest=app.Engine().build(self.project(videos=[voice],narration='Saját hangfájl',
            audio=voice,video_volume=0))
        result=soundtrack_cache.remix(app.Engine(),dest)
        expected_frames=math.ceil(duration*30)
        self.assertEqual(expected_frames,37)
        for movie in (dest/'video.mp4',result):
            with self.subTest(movie=movie.name):
                self.assertEqual(self.frame_count(movie),expected_frames)
                samples=self.pcm(movie)
                self.assertLess(self.amplitude(samples,440,.1,.3),20)
                self.assertGreater(self.amplitude(samples,440,.65,.85),1800)
                self.assertGreater(self.amplitude(samples,440,1.05,1.15),1800)
        cached=soundtrack_cache.load_source(dest)
        self.assertGreater(app.Engine().duration(cached.narration,self.folder),1.19)
        self.assertLess(self.amplitude(self.pcm(cached.narration),440,.1,.3),20)

    def test_audio_only_container_start_is_normalized_before_measuring_own_voice(self):
        shifted=self.folder/'Eltolt kezdetű saját hang.m4a'
        self.run_ffmpeg(['-i',self.voice,'-map','0:a:0','-c:a','aac',
            '-output_ts_offset','5',shifted])
        duration=app.Engine().duration(str(shifted),self.folder)
        self.assertGreater(duration,1.19)
        self.assertLess(duration,1.25)

    def test_failed_final_mix_can_be_recovered_from_project_or_cached_movie(self):
        output=self.folder/'Sikertelen végső keverés'
        with patch('background_music.export_video',side_effect=RuntimeError('final mix failed')):
            with self.assertRaisesRegex(RuntimeError,'final mix failed'):
                app.Engine().build(self.project(output=str(output)))
        folders=list(output.iterdir());self.assertEqual(len(folders),1)
        dest=folders[0]
        self.assertFalse((dest/'video.mp4').exists())
        self.assertTrue((dest/'projekt.json').is_file())
        import scene_library
        library=scene_library.load(dest)
        self.assertEqual(library.status,'building')
        self.assertEqual(len(library.scenes),1)
        self.assertEqual(self.frame_count(library.scenes[0].video),18)
        self.assertFalse((dest/'ideiglenes').exists())
        for selected in (dest/'projekt.json',dest/'hang_alap/alapvideo.mp4',dest/'hang_alap/hang_alap.json'):
            self.assertEqual(soundtrack_cache.project_folder_for_selection(selected),dest)
        result=soundtrack_cache.remix(app.Engine(),dest)
        self.assertEqual(self.frame_count(result),18)
        self.assertGreater(self.amplitude(self.pcm(result),440),2500)


if __name__=='__main__':unittest.main()
