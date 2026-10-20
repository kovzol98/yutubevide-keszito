"""Local continuation contracts: real decoding, source integrity, no AI/network."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

from PIL import Image
import local_resume as resume
import scene_library
import soundtrack_cache
from ytmuhely import Engine, Project, Cancelled, ffmpeg


class LocalResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/local_resume_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.clip = cls.root / 'eredeti.mp4'
        subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error',
                        '-f', 'lavfi', '-i', 'color=red:size=96x64:rate=30',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                        '-t', '1', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', str(cls.clip)], check=True)
        cls.photo = cls.root / 'eredeti.jpg'
        Image.new('RGB', (96,64), 'red').save(cls.photo)
        cls.voice, cls.music = cls.root / 'hang.wav', cls.root / 'zene.wav'
        for filename in (cls.voice, cls.music):
            subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1',
                '-c:a', 'pcm_s16le', str(filename)], check=True)

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().parent == cls.parent.resolve():
            shutil.rmtree(cls.root)

    def setUp(self):
        self.folder = self.root / uuid.uuid4().hex
        self.folder.mkdir()
        self.engine = Engine()
        self.p = Project(title='Mentett próba', text='First scene Second scene',
                         visual_mode='Saját videók', videos=[str(self.clip)],
                         narration='Nincs narráció', output=str(self.root / 'kimenet'))
        self.chunks = ('First scene', 'Second scene')

    def saved(self, *, scenes=1, frames=30, size=(96,64), label=None):
        (self.folder / 'projekt.json').write_text(json.dumps(asdict(self.p), ensure_ascii=False), encoding='utf-8')
        resume.create(self.engine, self.folder, self.p, self.chunks, (96,64))
        scene_library.create(self.engine, self.folder, len(self.chunks))
        for index in range(scenes):
            scene_library.add_scene(self.engine, self.folder, self.clip, frames,
                                    size=size, label=self.chunks[index] if label is None else label)
        return self.folder

    def edit(self, name, change):
        path = self.folder / name
        data = json.loads(path.read_text(encoding='utf-8'))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def hashes(self):
        return {str(p.relative_to(self.folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.folder.rglob('*') if p.is_file()}

    def test_supported_modes_and_offline_voice_only(self):
        for mode in resume.LOCAL_MODES:
            for voice in resume.LOCAL_VOICES:
                self.assertTrue(resume.supported(Project(visual_mode=mode, narration=voice)))
        for changes in ({'visual_mode':'Grok videó • internet'},
                        {'narration':'Angol női • internet'}, {'narration':'Angol férfi • internet'},
                        {'narration':'Windows-hang • offline'}, {'api_key':'secret'},
                        {'visual_mode':'ismeretlen'}):
            self.assertFalse(resume.supported({**asdict(self.p), **changes}))

    def test_valid_prefix_is_read_only_and_fully_decoded(self):
        self.saved()
        before = self.hashes()
        forbidden = lambda *a, **k: self.fail('A folytatás ellenőrzése nem indíthat AI-t vagy hálózati kérést.')
        with patch.object(Engine, 'generate_clip', forbidden), patch.object(Engine, 'narrate', forbidden), \
             patch('urllib.request.urlopen', forbidden), patch.object(scene_library, '_decode', wraps=scene_library._decode) as decoder:
            state = resume.prepare(self.engine, self.folder)
        self.assertEqual(state.root, self.folder.resolve())
        self.assertEqual(state.chunks, self.chunks)
        self.assertEqual(state.size, (96,64))
        self.assertEqual(len(state.library.scenes), 1)
        self.assertEqual(decoder.call_count, 1)
        self.assertEqual(decoder.call_args.args[2], 30)
        self.assertEqual(self.hashes(), before)

    def test_empty_prefix_can_resume_without_ai(self):
        self.saved(scenes=0)
        state = resume.prepare(self.engine, self.folder)
        self.assertEqual(state.library.scenes, ())

    def test_original_journal_never_overwritten(self):
        self.saved()
        before = self.hashes()
        with self.assertRaises(ValueError):
            resume.create(self.engine, self.folder, self.p, ('Other',), (96,64))
        self.assertEqual(self.hashes(), before)
        self.assertFalse(list(self.folder.glob('.folytatas.pending_*')))

    def test_old_v11_project_has_clear_unsupported_message(self):
        with self.assertRaisesRegex(ValueError, 'régebbi projekthez nincs folytatási napló'):
            resume.prepare(self.engine, self.folder)

    def test_saved_project_must_match_before_journal_creation(self):
        data = asdict(self.p)
        data['title'] = 'Other'
        (self.folder/'projekt.json').write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            resume.create(self.engine, self.folder, self.p, self.chunks, (96,64))
        self.assertFalse((self.folder / resume.JOURNAL).exists())

    def test_modified_project_rejected_before_scene_decode(self):
        self.saved()
        self.edit('projekt.json', lambda d: d.update(seconds=6))
        with patch.object(scene_library, '_decode', side_effect=AssertionError('must reject sooner')):
            with self.assertRaisesRegex(ValueError, 'beállításai megváltoztak'):
                resume.prepare(self.engine, self.folder)

    def test_unknown_credentials_are_not_persisted(self):
        data = asdict(self.p)
        data['api_key'] = 'PRIVATE_KEY_MUST_NEVER_PERSIST'
        (self.folder/'projekt.json').write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            resume.create(self.engine, self.folder, data, self.chunks, (96,64))
        self.assertFalse((self.folder / resume.JOURNAL).exists())

    def test_cloud_and_internet_voice_rejected_before_input_read(self):
        for field, value in (('visual_mode','Grok videó • internet'), ('narration','Angol női • internet')):
            data = asdict(self.p)
            data[field] = value
            with patch.object(resume, '_input', side_effect=AssertionError('must reject sooner')):
                with self.assertRaisesRegex(ValueError, 'internetes vagy Grok'):
                    resume.create(self.engine, self.folder, data, self.chunks, (96,64))

    def test_changed_original_movie_rejected(self):
        copied = self.folder / 'input.mp4'
        shutil.copyfile(self.clip, copied)
        self.p.videos = [str(copied)]
        self.saved()
        copied.write_bytes(copied.read_bytes() + b'CHANGED')
        with self.assertRaisesRegex(ValueError, 'megvaltozott|megváltozott'):
            resume.prepare(self.engine, self.folder)

    def test_changed_photo_rejected(self):
        copied = self.folder / 'input.jpg'
        shutil.copyfile(self.photo, copied)
        self.p.visual_mode = 'AI-videó • helyi'
        self.p.images = [str(copied)]
        self.saved()
        Image.new('RGB',(96,64),'blue').save(copied)
        with self.assertRaisesRegex(ValueError, 'megváltozott'):
            resume.prepare(self.engine, self.folder)

    def test_changed_music_rejected_even_at_zero_volume(self):
        copied = self.folder/'music.wav'
        shutil.copyfile(self.music, copied)
        self.p.music, self.p.music_volume = str(copied), 0
        self.saved()
        copied.write_bytes(copied.read_bytes() + b'CHANGED')
        with self.assertRaisesRegex(ValueError, 'megváltozott'):
            resume.prepare(self.engine,self.folder)

    def test_changed_own_narration_rejected(self):
        copied = self.folder/'voice.wav'
        shutil.copyfile(self.voice, copied)
        self.p.audio, self.p.narration = str(copied), 'Saját hangfájl'
        self.saved()
        copied.write_bytes(copied.read_bytes() + b'CHANGED')
        with self.assertRaisesRegex(ValueError, 'megváltozott'):
            resume.prepare(self.engine,self.folder)

    def test_input_manifest_cannot_drop_or_reorder_input(self):
        self.saved()
        self.edit(resume.JOURNAL, lambda d: d.update(inputs=[]))
        with self.assertRaisesRegex(ValueError, 'bemeneti fájllistája'):
            resume.prepare(self.engine,self.folder)

    def test_scene_count_must_match_journal(self):
        self.saved()
        self.edit(scene_library.MANIFEST, lambda d: d.update(expected_scenes=3))
        with self.assertRaisesRegex(ValueError, 'jelenetszáma'):
            resume.prepare(self.engine,self.folder)

    def test_scene_label_must_match_original_prefix(self):
        self.saved(label='Wrong scene')
        with self.assertRaisesRegex(ValueError, 'szövege vagy képmérete'):
            resume.prepare(self.engine,self.folder)

    def test_scene_dimensions_must_match_initial_size(self):
        self.saved(size=(128,96))
        with self.assertRaisesRegex(ValueError, 'szövege vagy képmérete'):
            resume.prepare(self.engine,self.folder)

    def test_actual_decoded_scene_frames_must_match(self):
        self.saved(frames=60)
        with self.assertRaisesRegex(ValueError, 'képkockaszáma'):
            resume.prepare(self.engine,self.folder)

    def test_truncated_scene_rehashed_in_library_still_fails_decode(self):
        self.saved()
        movie = self.folder / scene_library.FOLDER / '0001.mp4'
        movie.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'broken' * 100)
        self.edit(scene_library.MANIFEST,
                  lambda d: d['scenes'][0].update(video=soundtrack_cache._description(movie)))
        with self.assertRaisesRegex(ValueError, 'nem olvasható végig|nem tartalmaz'):
            resume.prepare(self.engine,self.folder)

    def test_complete_library_checks_final_movie_too(self):
        self.chunks = ('First scene',)
        self.saved()
        soundtrack_cache.save_source(self.engine, self.clip, self.folder, 1)
        shutil.copyfile(self.clip,self.folder/'video.mp4')
        scene_library.complete(self.engine,self.folder)
        with patch.object(scene_library,'_decode',wraps=scene_library._decode) as decoder:
            state = resume.prepare(self.engine,self.folder)
        self.assertEqual(state.library.status,'complete')
        self.assertEqual(decoder.call_count,2)
        self.assertEqual(decoder.call_args.args[1],self.folder.resolve()/'video.mp4')

    def test_journal_rejects_unknown_schema_and_extra_fields(self):
        self.saved()
        original = (self.folder/resume.JOURNAL).read_text(encoding='utf-8')
        for edit in (lambda d:d.update(schema=True), lambda d:d.update(schema=2),
                     lambda d:d.update(api_key='secret'), lambda d:d.update(size=[True,64]),
                     lambda d:d.update(chunks=[]), lambda d:d.update(path_base='relative')):
            (self.folder/resume.JOURNAL).write_text(original,encoding='utf-8')
            self.edit(resume.JOURNAL,edit)
            with self.assertRaises(ValueError):
                resume.prepare(self.engine,self.folder)

    def test_nonfinite_and_unbounded_settings_rejected(self):
        for value in (float('nan'),float('inf'),False,-1,121,10**1000):
            data = asdict(self.p)
            data['seconds'] = value
            with self.assertRaises(ValueError):
                resume.normalize_project(data)

    def test_duplicate_json_keys_rejected(self):
        self.saved()
        path = self.folder / resume.JOURNAL
        original = path.read_text(encoding='utf-8')
        path.write_text(original.replace('"schema": 1','"schema": 1, "schema": 1'),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'ismétlődő'):
            resume.prepare(self.engine,self.folder)

    def test_internet_input_path_rejected(self):
        data = asdict(self.p)
        data['videos'] = ['https://example.org/movie.mp4']
        with self.assertRaisesRegex(ValueError,'helyi fájlútvonal'):
            resume.normalize_project(data)

    def test_relative_paths_have_stable_recorded_base(self):
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.p.videos = ['eredeti.mp4']
            self.saved()
            os.chdir(self.folder)
            state = resume.prepare(self.engine,self.folder)
        finally:
            os.chdir(original)
        self.assertEqual(state.project['videos'],[str(self.clip.absolute())])

    def test_linked_fixed_filename_or_root_rejected(self):
        self.saved()
        with patch.object(soundtrack_cache,'_linked',side_effect=lambda p:Path(p).name==resume.JOURNAL):
            with self.assertRaisesRegex(ValueError,'átirányított'):
                resume.prepare(self.engine,self.folder)
        with patch.object(soundtrack_cache,'_linked',side_effect=lambda p:Path(p)==self.folder):
            with self.assertRaises(ValueError):
                resume.prepare(self.engine,self.folder)

    def test_linked_active_input_rejected(self):
        self.saved()
        with patch.object(soundtrack_cache,'_linked',side_effect=lambda p:Path(p)==self.clip):
            with self.assertRaisesRegex(ValueError,'átirányított'):
                resume.prepare(self.engine,self.folder)

    def test_cancellation_prevents_any_read_or_write(self):
        self.engine.cancel.set()
        with self.assertRaises(Cancelled):
            resume.create(self.engine,self.folder,self.p,self.chunks,(96,64))
        with self.assertRaises(Cancelled):
            resume.prepare(self.engine,self.folder)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_wrapper_uses_same_engine_build_and_normalized_settings(self):
        self.saved()
        with patch.object(self.engine,'build',return_value='finished') as build:
            self.assertEqual(resume.resume(self.engine,self.folder),'finished')
        self.assertEqual(build.call_args.kwargs,{'resume_folder':self.folder.resolve()})
        self.assertEqual(asdict(build.call_args.args[0]),resume.prepare(self.engine,self.folder).project)


if __name__ == '__main__':
    unittest.main(verbosity=2)
