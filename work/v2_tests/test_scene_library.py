"""Real CPU media checks for retained scenes and sound-preserving picture edits."""
from dataclasses import asdict
from array import array
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

import background_music
import scene_library as library
import soundtrack_cache
from ytmuhely import Cancelled, Engine, Project, ffmpeg


class SceneLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/scene_library_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.binary = ffmpeg()
        cls.engine = Engine()
        cls.red = cls.root / 'első piros.mp4'
        cls.blue = cls.root / 'második kék.mp4'
        cls.green = cls.root / "csere zöld ' teszt.mp4"
        cls.short = cls.root / 'rövid.mp4'
        cls.broken = cls.root / 'sérült.mp4'
        cls.broken.write_bytes(b'not a movie')
        for filename, color, frequency, seconds in ((cls.red, 'red', 440, 1),
                (cls.blue, 'blue', 660, 1), (cls.green, 'green', 2000, 1.5),
                (cls.short, 'white', 2500, .5)):
            cls.ff(['-f', 'lavfi', '-i', f'color={color}:size=96x64:rate=30',
                    '-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000',
                    '-t', str(seconds), '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                    '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-ar', '48000', '-ac', '2', filename])
        cls.voice = cls.root / 'külső narráció.wav'
        cls.music = cls.root / 'külső zene.wav'
        for filename, frequency in ((cls.voice, 880), (cls.music, 220)):
            cls.ff(['-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000',
                    '-t', '2', '-c:a', 'pcm_s16le', filename])
        cls.ass = cls.root / 'eredeti.ass'
        cls.ass.write_text('[Script Info]\nScriptType: v4.00+\nPlayResX: 96\nPlayResY: 64\n'
            '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, '
            'Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,Arial,10,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,0,0,0,1\n'
            '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello\n', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().parent == cls.parent.resolve():
            shutil.rmtree(cls.root)

    @classmethod
    def ff(cls, args):
        return subprocess.run([cls.binary, '-hide_banner', '-nostdin', '-y', *map(str, args)],
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def setUp(self):
        self.project = self.root / uuid.uuid4().hex
        self.project.mkdir()
        self.engine = Engine()

    def build(self, *, own=False, captions=False):
        library.create(self.engine, self.project, 2)
        for index, clip in enumerate((self.red, self.blue)):
            library.add_scene(self.engine, self.project, clip, 30,
                caption_file=self.ass if captions else None,
                captions_enabled=captions, cues=((0, 1, 'Hello'),) if captions else (),
                label=f'Jelenet {index+1}', size=(96, 64))
        (self.project / 'lista.txt').write_text("file 'jelenetek/0001.mp4'\nduration 1\n"
                                               "file 'jelenetek/0002.mp4'\nduration 1\n", encoding='utf-8')
        merged = self.project / 'alap.mp4'
        self.ff(['-f', 'concat', '-safe', '0', '-i', self.project / 'lista.txt',
                 '-c', 'copy', merged])
        narration = self.voice if own else ''
        soundtrack_cache.save_source(self.engine, merged, self.project, 2,
                                     narration=narration, mix_narration=own)
        background_music.export_video(self.engine, merged, self.project / 'video.mp4', 2,
            music=self.music, music_volume=40, narration=narration, mix_narration=own)
        p = Project(title='Saját próba', videos=[str(self.red), str(self.blue)],
                    text='English narration', audio=str(narration),
                    narration='Saját hangfájl' if own else 'Nincs narráció', music=str(self.music))
        data = asdict(p)
        data['api_key'] = 'MUST_NOT_COPY_CREDENTIAL'
        (self.project / 'projekt.json').write_text(json.dumps(data), encoding='utf-8')
        library.complete(self.engine, self.project)
        return library.load(self.project)

    def tree(self):
        return {str(p.relative_to(self.project)): self.sha(p)
                for p in self.project.rglob('*') if p.is_file() and
                not p.relative_to(self.project).parts[0].startswith('Jelenetcsere_')}

    def packets(self, path, kind='a'):
        output = self.ff(['-v', 'error', '-i', path, '-map', f'0:{kind}:0',
                          '-c', 'copy', '-f', 'framehash', '-']).stdout
        return [line for line in output.splitlines() if not line.startswith(b'#')]

    def pixels(self, path):
        return self.ff(['-v', 'error', '-i', path, '-map', '0:V:0', '-an',
                        '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']).stdout

    def amplitude(self,path,frequency):
        output=self.ff(['-v','error','-i',path,'-map','0:a:0','-vn',
                        '-ar','48000','-ac','1','-c:a','pcm_s16le','-f','s16le','-']).stdout
        values=array('h',output)[9600:28800]
        real=sum(v*math.cos(2*math.pi*frequency*n/48000) for n,v in enumerate(values))
        imag=sum(v*math.sin(2*math.pi*frequency*n/48000) for n,v in enumerate(values))
        return 2*math.hypot(real,imag)/len(values)

    def test_building_previews_are_durable(self):
        result = library.create(self.engine, self.project, 2)
        self.assertEqual(result.status, 'building')
        self.assertEqual(result.scenes, ())
        library.add_scene(self.engine, self.project, self.red, 30, size=(96,64), label='One')
        result = library.load(self.project)
        self.assertEqual(result.expected_scenes, 2)
        scene = result.scenes[0]
        self.assertEqual((scene.index, scene.duration, scene.size, scene.label), (0, 1, (96,64), 'One'))
        self.assertEqual(self.sha(scene.video), self.sha(self.red))
        with self.assertRaises(ValueError):
            library.export_variant(self.engine, self.project, {0: str(self.green)})
        with self.assertRaises(ValueError):
            library.complete(self.engine, self.project)

    def test_create_does_not_overwrite_existing_library(self):
        library.create(self.engine, self.project, 1)
        before = (self.project / library.MANIFEST).read_bytes()
        with self.assertRaises(ValueError):
            library.create(self.engine, self.project, 2)
        self.assertEqual((self.project / library.MANIFEST).read_bytes(), before)

    def test_complete_checks_all_scenes_cache_and_final_movie(self):
        library.create(self.engine, self.project, 1)
        library.add_scene(self.engine, self.project, self.red, 30, size=(96,64))
        with self.assertRaises(ValueError):
            library.complete(self.engine, self.project)
        soundtrack_cache.save_source(self.engine, self.red, self.project, 1)
        with self.assertRaises(ValueError):
            library.complete(self.engine, self.project)
        shutil.copyfile(self.red, self.project / 'video.mp4')
        self.assertEqual(library.complete(self.engine, self.project).status, 'complete')

    def test_completion_commit_wins_late_cancellation(self):
        library.create(self.engine,self.project,1)
        library.add_scene(self.engine,self.project,self.red,30,size=(96,64))
        soundtrack_cache.save_source(self.engine,self.red,self.project,1)
        shutil.copyfile(self.red,self.project/'video.mp4')
        write=library._write
        def cancel_after_write(root,data):
            write(root,data)
            self.engine.cancel.set()
        with patch.object(library,'_write',cancel_after_write):
            result=library.complete(self.engine,self.project)
        self.assertEqual(result.status,'complete')
        self.assertEqual(library.load(self.project).status,'complete')

    def test_replace_one_scene_preserves_entire_final_audio_and_original_project(self):
        self.build()
        original = self.tree()
        def forbidden(*args, **kwargs):
            raise AssertionError('A local picture edit must never generate AI media.')
        with patch.object(Engine, 'generate_clip', forbidden), patch('urllib.request.urlopen', forbidden):
            result = library.export_variant(self.engine, self.project, {0: str(self.green)})
        self.assertEqual(original, self.tree())
        self.assertEqual(self.packets(result), self.packets(self.project / 'video.mp4'))
        edited = library.load(result.parent)
        self.assertEqual(edited.status, 'complete')
        self.assertEqual(self.sha(edited.scenes[1].video), self.sha(self.blue))
        self.assertEqual(self.packets(edited.scenes[0].video), self.packets(self.red))
        # The replacement's 2000Hz audio cannot enter the retained scene.
        self.assertNotEqual(self.packets(edited.scenes[0].video), self.packets(self.green))
        pixels = self.pixels(result)
        self.assertEqual(len(pixels), 60 * 96 * 64 * 3)
        first = pixels[:3]
        self.assertGreater(first[1], first[0])
        self.assertGreater(first[1], first[2])
        self.assertTrue((result.parent / 'boritokep.png').is_file())

    def test_replacing_both_scenes_gives_exact_count_and_order(self):
        self.build()
        result = library.export_variant(self.engine, self.project, {0: str(self.green), 1: str(self.red)})
        pixels = self.pixels(result)
        self.assertEqual(len(pixels), 60 * 96 * 64 * 3)
        second = pixels[30 * 96 * 64 * 3:][:3]
        self.assertGreater(second[0], second[1])
        self.assertGreater(second[0], second[2])

    def test_new_cache_has_edited_picture_and_original_music_free_sound(self):
        self.build()
        old = soundtrack_cache.load_source(self.project)
        result = library.export_variant(self.engine, self.project, {0: str(self.green)})
        new = soundtrack_cache.load_source(result.parent)
        self.assertEqual(new.duration, 2)
        self.assertEqual(self.packets(old.video), self.packets(new.video))
        self.assertEqual(self.packets(result, 'v'), self.packets(new.video, 'v'))
        remixed = soundtrack_cache.remix(self.engine, result.parent, music='', music_volume=12)
        self.assertEqual(self.packets(result, 'v'), self.packets(remixed, 'v'))
        self.assertNotEqual(self.packets(result), self.packets(remixed))

    def test_selected_music_remix_keeps_its_own_entire_soundtrack(self):
        self.build()
        canonical=self.sha(self.project/'video.mp4')
        chosen=soundtrack_cache.remix(self.engine,self.project,music=self.voice,music_volume=75)
        result=library.export_variant(self.engine,self.project,{0:str(self.green)},soundtrack=chosen)
        self.assertEqual(self.packets(result),self.packets(chosen))
        self.assertNotEqual(self.packets(result),self.packets(self.project/'video.mp4'))
        self.assertEqual(self.sha(self.project/'video.mp4'),canonical)
        old=soundtrack_cache.load_source(self.project)
        new=soundtrack_cache.load_source(result.parent)
        self.assertEqual(self.packets(old.video),self.packets(new.video))

    def test_soundtrack_selection_must_be_a_matching_local_project_movie(self):
        self.build()
        other=self.project/'short_soundtrack.mp4'
        shutil.copyfile(self.short,other)
        for chosen in (self.green,other,self.project/'missing.mp4'):
            with self.subTest(chosen=chosen),self.assertRaises(ValueError):
                library.export_variant(self.engine,self.project,{0:str(self.green)},soundtrack=chosen)

    def test_own_narration_and_music_survive_deleted_external_files(self):
        # Keep the class's common fixtures intact; only the original project
        # points to disposable external copies.
        self.build(own=True)
        voice_copy = self.project / 'temporary_voice.wav'
        music_copy = self.project / 'temporary_music.wav'
        shutil.copyfile(self.voice, voice_copy)
        shutil.copyfile(self.music, music_copy)
        project_data = json.loads((self.project / 'projekt.json').read_text())
        project_data.update(audio=str(voice_copy), music=str(music_copy))
        (self.project / 'projekt.json').write_text(json.dumps(project_data), encoding='utf-8')
        voice_copy.unlink()
        music_copy.unlink()
        old = soundtrack_cache.load_source(self.project)
        result = library.export_variant(self.engine, self.project, {0: str(self.green)})
        new = soundtrack_cache.load_source(result.parent)
        self.assertTrue(new.mix_narration)
        self.assertTrue(Path(new.narration).is_file())
        self.assertEqual(self.sha(new.narration), self.sha(old.narration))
        self.assertEqual(self.packets(result), self.packets(self.project / 'video.mp4'))
        project = json.loads((result.parent / 'projekt.json').read_text(encoding='utf-8'))
        self.assertEqual(project['audio'], '')
        self.assertEqual(project['narration'], 'Nincs narráció')
        self.assertFalse(project['music'])
        self.assertNotIn('MUST_NOT_COPY_CREDENTIAL', json.dumps(project))
        self.assertEqual(project['text'], '')
        self.assertFalse(project['subtitles'])
        self.assertEqual(project['videos'],[str(result)])
        soundtrack_cache.remix(self.engine, result.parent, music='')

    def test_normal_project_reload_retains_embedded_music_without_external_files(self):
        self.build(own=True)
        result=library.export_variant(self.engine,self.project,{0:str(self.green)})
        data=json.loads((result.parent/'projekt.json').read_text(encoding='utf-8'))
        project=Project(**data)
        project.output=str(self.project/'render_again')
        project.encoder='CPU • összes mag'
        def no_ai(*args,**kwargs):
            raise AssertionError('Reloaded edit must only import its finished movie.')
        with patch.object(Engine,'generate_clip',no_ai):
            rendered=self.engine.build(project)/'video.mp4'
        old_music=self.amplitude(result,220)
        new_music=self.amplitude(rendered,220)
        self.assertGreater(old_music,500)
        self.assertGreater(new_music,old_music*.8)
        self.assertGreater(self.amplitude(rendered,880),1000)

    def test_saved_caption_is_applied_to_replacement_and_srt_is_retained(self):
        self.build(captions=True)
        result = library.export_variant(self.engine, self.project, {0: str(self.green)})
        edited = library.load(result.parent)
        self.assertTrue(edited.scenes[0].captions_enabled)
        self.assertEqual(self.sha(edited.scenes[0].caption), self.sha(self.ass))
        srt = (result.parent / 'felirat.srt').read_text(encoding='utf-8')
        self.assertIn('00:00:01,000 --> 00:00:02,000', srt)
        self.assertEqual(srt.count('Hello'), 2)
        pixels = self.pixels(result)
        white = sum(min(pixels[i:i+3]) > 180 for i in range(0,96*64*3,3))
        self.assertGreater(white, 5)

    def test_short_broken_missing_and_invalid_selection_are_rejected_before_stage(self):
        self.build()
        before = self.tree()
        for replacements in ({0:str(self.short)}, {0:str(self.broken)}, {0:'missing.mp4'},
                             {}, {2:str(self.green)}, {True:str(self.green)}, {'0':str(self.green)}):
            with self.subTest(replacements=replacements), self.assertRaises(ValueError):
                library.export_variant(self.engine, self.project, replacements)
        self.assertEqual(before, self.tree())
        self.assertFalse(list(self.project.glob('.jelenetcsere.pending_*')))
        self.assertFalse(list(self.project.glob('Jelenetcsere_*')))

    def test_only_one_frame_rounding_tolerance_is_allowed(self):
        self.build()
        near = self.project / 'one_frame_short.mp4'
        too_short = self.project / 'two_frames_short.mp4'
        for filename, count in ((near,29),(too_short,28)):
            self.ff(['-i',self.green,'-an','-c:v','libx264','-threads','2',
                     '-frames:v',str(count),filename])
        with self.assertRaises(ValueError):
            library.export_variant(self.engine,self.project,{0:str(too_short)})
        result=library.export_variant(self.engine,self.project,{0:str(near)})
        self.assertEqual(len(self.pixels(result)),60*96*64*3)

    def test_positive_timestamp_offset_does_not_make_short_video_appear_long(self):
        self.build()
        shifted=self.project/'shifted_short.mp4'
        self.ff(['-i',self.short,'-c','copy','-output_ts_offset','5',shifted])
        self.assertAlmostEqual(library._decode(self.engine,shifted),.5,places=2)
        with self.assertRaises(ValueError):
            library.export_variant(self.engine,self.project,{0:str(shifted)})

    def test_shifted_valid_replacement_starts_at_its_first_picture(self):
        self.build()
        shifted=self.project/'shifted_green.mp4'
        self.ff(['-i',self.green,'-c','copy','-output_ts_offset','5',shifted])
        result=library.export_variant(self.engine,self.project,{0:str(shifted)})
        pixels=self.pixels(result)
        self.assertEqual(len(pixels),60*96*64*3)
        self.assertGreater(pixels[1],pixels[0])
        self.assertGreater(pixels[1],pixels[2])

    def test_append_does_not_rehash_earlier_large_movies(self):
        library.create(self.engine,self.project,2)
        first=library.add_scene(self.engine,self.project,self.red,30,size=(96,64))
        digest=soundtrack_cache._digest
        def prohibit_prior_read(path,engine=None):
            if Path(path)==first.video:
                raise AssertionError('Append re-read a previous complete media file.')
            return digest(path,engine)
        with patch.object(soundtrack_cache,'_digest',prohibit_prior_read):
            second=library.add_scene(self.engine,self.project,self.blue,30,size=(96,64))
        self.assertEqual(second.index,1)
        self.assertEqual(len(library.load(self.project).scenes),2)

    def test_local_playlist_cannot_fetch_a_network_segment(self):
        # This local HLS file is recognized by FFmpeg, but its only remote
        # segment must be blocked by FFmpeg's protocol allowlist.
        playlist=self.project/'remote.m3u8'
        playlist.write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n'
            '#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\n'
            'http://127.0.0.1:9/forbidden.ts\n#EXT-X-ENDLIST\n',encoding='utf-8')
        calls=[]
        run=self.engine.run
        def capture(args,**kwargs):
            calls.append(args)
            return run(args,**kwargs)
        with patch.object(self.engine,'run',capture),self.assertRaises(ValueError) as raised:
            library._decode(self.engine,playlist)
        self.assertEqual(calls[0][calls[0].index('-protocol_whitelist')+1],'file,pipe')
        self.assertIn('not on whitelist',str(raised.exception.__cause__))

    def test_redirected_library_file_is_rejected(self):
        self.build()
        target=self.project/library.FOLDER/'0001.mp4'
        original=target.read_bytes()
        target.unlink()
        try:
            target.symlink_to(self.red)
        except OSError:
            target.write_bytes(original)
            self.skipTest('The Windows session does not permit creating symlinks.')
        with self.assertRaises(ValueError):
            library.load(self.project)
        target.unlink()
        target.write_bytes(original)

    def test_corrupt_final_or_cache_rejected_before_picture_work(self):
        self.build()
        for path in (self.project / 'video.mp4', soundtrack_cache.load_source(self.project).video):
            data = path.read_bytes()
            path.write_bytes(data + b'changed')
            with self.assertRaises(ValueError):
                library.export_variant(self.engine, self.project, {0:str(self.green)})
            path.write_bytes(data)

    def test_malformed_metadata_and_paths_rejected(self):
        self.build()
        manifest = self.project / library.MANIFEST
        original = manifest.read_text(encoding='utf-8')
        mutations = [lambda d:d['scenes'][0]['video'].update(name='../alap.mp4'),
                     lambda d:d['scenes'][0].update(frames=True),
                     lambda d:d['scenes'][0].update(size=[95,64]),
                     lambda d:d['scenes'][0].update(captions_enabled='yes'),
                     lambda d:d['scenes'][0].update(cues=[[0,5,'too long']]),
                     lambda d:d.update(expected_scenes=1),
                     lambda d:d.update(final_video={'name':'elsewhere.mp4'}),
                     lambda d:d.update(api_key='must not accept')]
        for mutation in mutations:
            data = json.loads(original)
            mutation(data)
            manifest.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                library.load(self.project)
        manifest.write_text(original, encoding='utf-8')

    def test_cancel_during_render_removes_only_unpublished_stage(self):
        self.build()
        before = self.tree()
        run = self.engine.run
        def cancel_on_render(args, **kwargs):
            if '-crf' in args:
                raise Cancelled('cancelled')
            return run(args, **kwargs)
        with patch.object(self.engine, 'run', cancel_on_render), self.assertRaises(Cancelled):
            library.export_variant(self.engine, self.project, {0:str(self.green)})
        self.assertEqual(before, self.tree())
        self.assertFalse(list(self.project.glob('.jelenetcsere.pending_*')))

    def test_existing_scene_is_never_removed_on_append_collision(self):
        library.create(self.engine, self.project, 1)
        target = self.project / library.FOLDER / '0001.mp4'
        target.write_bytes(b'prior surviving file')
        with self.assertRaises(FileExistsError):
            library.add_scene(self.engine, self.project, self.red, 30, size=(96,64))
        self.assertEqual(target.read_bytes(), b'prior surviving file')


if __name__ == '__main__':
    unittest.main(verbosity=2)
