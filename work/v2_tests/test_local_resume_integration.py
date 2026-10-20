"""Interrupted local Engine renders, using real CPU media and offline stand-ins.

The AI and English speech fixtures are intentionally synthetic. These tests
verify preservation, routing, timing, and publication; they make no claim about
the visual quality of an AI model or the quality of synthesized English.
"""
from __future__ import annotations

from array import array
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

from PIL import Image
import imageio_ffmpeg
import background_music
import local_resume
import scene_library
import soundtrack_cache
import ytmuhely as app


class LocalResumeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep real libass/FFmpeg inputs below Windows' path limit even when
        # the repository itself is cloned inside a deeply nested workspace.
        cls.parent = ROOT / 'work/lri_tests'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.binary = app.ffmpeg()
        cls.movie = cls.root / 'synthetic moving picture.mp4'
        cls.spoken = cls.root / 'synthetic English stand-in.wav'
        cls.own_voice = cls.root / 'own narration.wav'
        cls.music = cls.root / 'background music.wav'
        cls.ff(['-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                '-t', '3', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', cls.movie])
        for path, frequency, duration in ((cls.spoken, 880, .4),
                (cls.own_voice, 880, 3), (cls.music, 220, 4)):
            cls.ff(['-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000',
                    '-t', str(duration), '-c:a', 'pcm_s16le', path])

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().parent == cls.parent.resolve():
            shutil.rmtree(cls.root)

    @classmethod
    def ff(cls, args):
        return subprocess.run([cls.binary, '-hide_banner', '-nostdin', '-y', *map(str, args)],
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=app.CREATE_NO_WINDOW)

    def setUp(self):
        self.base = self.root / uuid.uuid4().hex[:12]
        self.base.mkdir()
        self.output = self.base / 'output'
        self.photo = self.base / 'original photo.png'
        Image.new('RGB', (96, 64), '#bd9869').save(self.photo)
        # Every mutable input belongs to this test, so corruption cannot affect
        # a later test or the user's original media.
        self.selected_music = self.base / 'selected music.wav'
        self.selected_voice = self.base / 'selected narration.wav'
        shutil.copyfile(self.music, self.selected_music)
        shutil.copyfile(self.own_voice, self.selected_voice)
        self.sizes = patch.dict(app.SIZES, {'720p • 16:9': (96, 64)})
        self.sizes.start()
        self.addCleanup(self.sizes.stop)

    def project(self, **changes):
        values = dict(title='Resume test',
            images=[str(self.photo)] * 3,
            text='First English scene.\n\nSecond English scene.\n\nThird English scene.',
            video_prompt='A person makes a small natural movement.',
            narration='Angol női LJ • offline', visual_mode='AI-videó • helyi',
            image_motion='Jelenetleírás szerinti mozgás',
            format='720p • 16:9', output=str(self.output), seconds=1,
            enhancement='Eredeti megjelenés', encoder='CPU • összes mag',
            subtitles=True)
        values.update(changes)
        return app.Project(**values)

    @contextmanager
    def offline(self, engine, *, fail_generate_at=None, fail_export=False,
                no_new_work=False, crop=False, interrupt_after_saved=None):
        """Run the actual Engine with no model, network, or GPU work permitted."""
        calls = SimpleNamespace(generated=[], spoken=[], saved=0)
        actual_add = scene_library.add_scene

        def speech(text, filename, mode):
            if no_new_work:
                raise AssertionError('A retained scene was synthesized again.')
            self.assertEqual(mode, 'Angol női LJ • offline')
            calls.spoken.append(text)
            shutil.copyfile(self.spoken, filename)

        def generate(project, text, image, index, work, seconds):
            if no_new_work:
                raise AssertionError('A retained scene started an AI model again.')
            calls.generated.append(index)
            if len(calls.generated) == fail_generate_at:
                raise app.Cancelled('Synthetic interruption during this scene.')
            if crop and index == 0:
                folder = Path(work).parent / 'AI_jelenetek/jelenet_001'
                folder.mkdir(parents=True)
                Image.new('RGB', (48, 64), '#bd9869').save(folder / 'elokeszitett_foto.png')
            return self.movie

        def add(*args, **kwargs):
            result = actual_add(*args, **kwargs)
            calls.saved += 1
            if calls.saved == interrupt_after_saved:
                raise app.Cancelled('Synthetic interruption after a committed scene.')
            return result

        def setup(*args, **kwargs):
            if no_new_work:
                raise AssertionError('All scenes are retained; local model setup must be skipped.')
            return None

        def probe(*args, **kwargs):
            if no_new_work:
                raise AssertionError('All scenes are retained; the GPU must not be probed.')
            return []

        def forbidden_network(*args, **kwargs):
            raise AssertionError('A local continuation attempted a network request.')

        with ExitStack() as stack:
            stack.enter_context(patch.object(engine, 'narrate', side_effect=speech))
            stack.enter_context(patch.object(engine, 'generate_clip', side_effect=generate))
            stack.enter_context(patch.object(engine, 'codec', return_value=[
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-threads', '1']))
            for module in ('realistic_video', 'comfy_video', 'portrait_video'):
                stack.enter_context(patch(f'{module}.engine_files', side_effect=setup))
            stack.enter_context(patch('gpu_resources.probe_gpu_users', side_effect=probe))
            stack.enter_context(patch('comfy_video.stop_engine'))
            stack.enter_context(patch('urllib.request.urlopen', side_effect=forbidden_network))
            stack.enter_context(patch.object(scene_library, 'add_scene', side_effect=add))
            if fail_export:
                stack.enter_context(patch.object(background_music, 'export_video',
                    side_effect=RuntimeError('Synthetic failure during final soundtrack export.')))
            yield calls

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def tree(self, folder):
        return {str(path.relative_to(folder)): self.sha(path)
                for path in folder.rglob('*') if path.is_file()}

    def folders(self):
        return set(self.output.iterdir()) if self.output.exists() else set()

    def interrupted(self, project=None, **kwargs):
        engine = app.Engine()
        with self.offline(engine, fail_generate_at=2, **kwargs):
            with self.assertRaises(app.Cancelled):
                engine.build(project or self.project())
        source = engine.last_project_folder
        self.assertFalse((source / 'video.mp4').exists())
        self.assertFalse((source / 'ideiglenes').exists())
        self.assertTrue((source / local_resume.JOURNAL).is_file())
        self.assertEqual(len(scene_library.load(source).scenes), 1)
        return source

    def picture(self, path):
        return self.ff(['-v', 'error', '-i', path, '-map', '0:V:0', '-an',
                        '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']).stdout

    def audio_packets(self, path):
        data = self.ff(['-v', 'error', '-i', path, '-map', '0:a:0', '-c', 'copy',
                        '-f', 'framehash', '-']).stdout
        return [line for line in data.splitlines() if not line.startswith(b'#')]

    def amplitude(self, path, frequency):
        raw = self.ff(['-v', 'error', '-i', path, '-map', '0:a:0', '-vn',
                       '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 's16le', '-']).stdout
        samples = array('h', raw)[9600:28800]
        self.assertGreater(len(samples), 0)
        real = sum(v * math.cos(2 * math.pi * frequency * n / 48000)
                   for n, v in enumerate(samples))
        imaginary = sum(v * math.sin(2 * math.pi * frequency * n / 48000)
                        for n, v in enumerate(samples))
        return 2 * math.hypot(real, imaginary) / len(samples)

    def test_resume_renders_only_two_remaining_scenes_and_preserves_original(self):
        project = self.project(music=str(self.selected_music), music_volume=40)
        source = self.interrupted(project)
        before = self.tree(source)
        first = scene_library.load(source).scenes[0]
        engine = app.Engine()
        with self.offline(engine) as calls:
            result = local_resume.resume(engine, source)
        self.assertNotEqual(result, source)
        self.assertEqual(calls.generated, [1, 2])
        self.assertEqual(calls.spoken, ['Second English scene.', 'Third English scene.'])
        self.assertEqual(self.tree(source), before)
        library = scene_library.load(result)
        self.assertEqual(library.status, 'complete')
        self.assertEqual(len(library.scenes), 3)
        self.assertEqual(self.sha(library.scenes[0].video), self.sha(first.video))
        self.assertEqual(library.scenes[0].cues, first.cues)
        frames, _ = imageio_ffmpeg.count_frames_and_secs(str(result / 'video.mp4'))
        self.assertEqual(frames, sum(scene.frames for scene in library.scenes))
        self.assertIn('Third English scene.', (result / 'felirat.srt').read_text(encoding='utf-8'))
        # Compare to an uninterrupted actual export, rather than to an
        # implementation-shaped expected command string.
        clean_engine = app.Engine()
        with self.offline(clean_engine):
            clean = clean_engine.build(project)
        self.assertEqual(self.picture(result / 'video.mp4'), self.picture(clean / 'video.mp4'))
        self.assertEqual(self.audio_packets(result / 'video.mp4'), self.audio_packets(clean / 'video.mp4'))
        self.assertEqual((result / 'felirat.srt').read_bytes(), (clean / 'felirat.srt').read_bytes())

    def test_a_second_interruption_can_itself_be_resumed(self):
        source = self.interrupted()
        original = self.tree(source)
        engine = app.Engine()
        with self.offline(engine, fail_generate_at=2) as calls:
            with self.assertRaises(app.Cancelled):
                local_resume.resume(engine, source)
        second = engine.last_project_folder
        self.assertEqual(calls.generated, [1, 2])
        self.assertEqual(self.tree(source), original)
        saved = scene_library.load(second)
        self.assertEqual(len(saved.scenes), 2)
        second_before = self.tree(second)
        last_engine = app.Engine()
        with self.offline(last_engine) as calls:
            result = local_resume.resume(last_engine, second)
        self.assertEqual(calls.generated, [2])
        self.assertEqual(calls.spoken, ['Third English scene.'])
        self.assertEqual(self.tree(second), second_before)
        self.assertEqual(len(scene_library.load(result).scenes), 3)

    def test_final_export_failure_reuses_every_scene_without_model_or_voice_setup(self):
        engine = app.Engine()
        with self.offline(engine, fail_export=True):
            with self.assertRaisesRegex(RuntimeError, 'soundtrack'):
                engine.build(self.project(music=str(self.selected_music)))
        source = engine.last_project_folder
        self.assertEqual(len(scene_library.load(source).scenes), 3)
        original = self.tree(source)
        next_engine = app.Engine()
        with self.offline(next_engine, no_new_work=True) as calls:
            result = local_resume.resume(next_engine, source)
        self.assertEqual(calls.generated, [])
        self.assertEqual(calls.spoken, [])
        self.assertEqual(self.tree(source), original)
        self.assertEqual(scene_library.load(result).status, 'complete')
        # Clicking continuation on a completed result is also a no-op.
        before = self.folders()
        with self.offline(app.Engine(), no_new_work=True):
            self.assertEqual(local_resume.resume(app.Engine(), result), result)
        self.assertEqual(self.folders(), before)

    def test_subject_crop_restores_the_saved_export_size(self):
        project = self.project(format='Fotó eredeti aránya', framing='Alanyra közelítés')
        source = self.interrupted(project, crop=True)
        self.assertEqual(scene_library.load(source).scenes[0].size, (48, 64))
        engine = app.Engine()
        with self.offline(engine) as calls:
            result = local_resume.resume(engine, source)
        self.assertEqual(calls.generated, [1, 2])
        library = scene_library.load(result)
        self.assertEqual({scene.size for scene in library.scenes}, {(48, 64)})
        decoded = self.picture(result / 'video.mp4')
        self.assertEqual(len(decoded), sum(scene.frames for scene in library.scenes) * 48 * 64 * 3)

    def test_own_narration_and_music_survive_without_speech_synthesis(self):
        project = self.project(narration='Saját hangfájl', audio=str(self.selected_voice),
                               music=str(self.selected_music), music_volume=40)
        source = self.interrupted(project)
        before = self.tree(source)
        engine = app.Engine()
        with self.offline(engine) as calls:
            result = local_resume.resume(engine, source)
        self.assertEqual(calls.generated, [1, 2])
        self.assertEqual(calls.spoken, [])
        self.assertEqual(self.tree(source), before)
        library = scene_library.load(result)
        self.assertEqual([scene.frames for scene in library.scenes], [30, 30, 30])
        cache = soundtrack_cache.load_source(result)
        self.assertTrue(Path(cache.narration).is_file())
        self.assertFalse(cache.mix_narration)
        self.assertEqual(cache.duration, 3)
        self.assertGreater(self.amplitude(result / 'video.mp4', 880), 1000)
        self.assertGreater(self.amplitude(result / 'video.mp4', 220), 400)
        self.assertLess(self.amplitude(result / 'video.mp4', 440), 50)

    def test_imported_full_clips_resume_without_duplicate_clips_or_lost_original_sound(self):
        project = self.project(visual_mode='Saját videók', images=[],
            videos=[str(self.movie)] * 3, narration='Nincs narráció',
            text='One. Two. Three. Four. Five. Six.', subtitles=False,
            use_video_length=True, seconds=1)
        engine = app.Engine()
        with self.offline(engine, interrupt_after_saved=1):
            with self.assertRaises(app.Cancelled):
                engine.build(project)
        source = engine.last_project_folder
        next_engine = app.Engine()
        with self.offline(next_engine) as calls:
            result = local_resume.resume(next_engine, source)
        self.assertEqual(calls.generated, [])
        self.assertEqual(calls.spoken, [])
        library = scene_library.load(result)
        self.assertEqual([scene.frames for scene in library.scenes], [90, 90, 90])
        frames, _ = imageio_ffmpeg.count_frames_and_secs(str(result / 'video.mp4'))
        self.assertEqual(frames, 270)
        self.assertGreater(self.amplitude(result / 'video.mp4', 440), 2000)

    def test_changed_input_or_project_is_rejected_before_generation_or_destination(self):
        project = self.project(narration='Saját hangfájl', audio=str(self.selected_voice),
                               music=str(self.selected_music))
        source = self.interrupted(project)
        for target in (self.photo, self.selected_music, self.selected_voice, source / 'projekt.json'):
            with self.subTest(changed=target.name):
                original = target.read_bytes()
                before = self.folders()
                if target.suffix == '.json':
                    data = json.loads(original)
                    data['lighting'] = 'Lágy természetes fény'
                    target.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                else:
                    target.write_bytes(original + b' changed bytes')
                try:
                    engine = app.Engine()
                    with self.offline(engine, no_new_work=True) as calls:
                        with self.assertRaises(ValueError):
                            local_resume.resume(engine, source)
                    self.assertEqual(calls.generated, [])
                    self.assertEqual(calls.spoken, [])
                    self.assertEqual(self.folders(), before)
                finally:
                    target.write_bytes(original)
        with self.offline(app.Engine(), no_new_work=True):
            with self.assertRaises(ValueError):
                app.Engine().build(replace(project, seconds=2), resume_folder=source)

    def test_damaged_retained_clip_is_rejected_before_new_destination(self):
        source = self.interrupted()
        scene = scene_library.load(source).scenes[0]
        scene.video.write_bytes(b'not a playable video')
        before = self.folders()
        engine = app.Engine()
        with self.offline(engine, no_new_work=True) as calls:
            with self.assertRaises(ValueError):
                local_resume.resume(engine, source)
        self.assertEqual(calls.generated, [])
        self.assertEqual(calls.spoken, [])
        self.assertEqual(self.folders(), before)

    def test_cloud_and_internet_voice_journals_cannot_start_generation(self):
        source = self.interrupted()
        journal = source / local_resume.JOURNAL
        project = source / 'projekt.json'
        old_journal, old_project = journal.read_bytes(), project.read_bytes()
        for field, value in (('visual_mode', app.GROK_MODE),
                ('narration', 'Angol női • internet'), ('narration', 'Angol férfi • internet')):
            with self.subTest(field=field, value=value):
                data, settings = json.loads(old_journal), json.loads(old_project)
                data['project'][field] = value
                settings[field] = value
                journal.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                project.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
                before = self.folders()
                try:
                    engine = app.Engine()
                    with self.offline(engine, no_new_work=True) as calls:
                        with self.assertRaisesRegex(ValueError, 'helyi|offline|internetes|Grok'):
                            local_resume.resume(engine, source)
                    self.assertEqual(calls.generated, [])
                    self.assertEqual(calls.spoken, [])
                    self.assertEqual(self.folders(), before)
                finally:
                    journal.write_bytes(old_journal)
                    project.write_bytes(old_project)


if __name__ == '__main__':
    unittest.main(verbosity=2)
