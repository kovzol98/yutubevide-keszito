"""Saved soundtrack edits must not resample or touch the original movie."""
from __future__ import annotations

from array import array
from dataclasses import fields
import hashlib
import json
import math
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

import soundtrack_cache
from ytmuhely import Cancelled, Engine, Project, ffmpeg


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/soundtrack_cache_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.binary = ffmpeg()
        cls.movie = cls.root / 'Eredeti kép angol hanggal.mp4'
        cls.silent = cls.root / 'Néma kép.mp4'
        cls.voice = cls.root / 'Saját hang.wav'
        cls.music = cls.root / 'Rövid háttérzene.wav'
        cls.ff(['-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30',
                '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
                '-t', '1.2', '-c:v', 'libx264', '-threads', '2', '-preset', 'fast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', cls.movie])
        cls.ff(['-i', cls.movie, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                '-t', '1.2', '-c:a', 'aac', cls.silent])
        for path, frequency, seconds in ((cls.music, 440, .22), (cls.voice, 660, .9)):
            cls.ff(['-f', 'lavfi', '-i',
                    f'sine=frequency={frequency}:sample_rate=48000:duration={seconds}',
                    '-c:a', 'pcm_s16le', path])

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().is_relative_to(cls.parent.resolve()):
            shutil.rmtree(cls.root)

    @classmethod
    def ff(cls, args):
        return subprocess.run([cls.binary, '-hide_banner', '-nostdin', '-y',
                               *map(str, args)], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def setUp(self):
        self.project = self.root / uuid.uuid4().hex
        self.project.mkdir()
        self.original = self.project / 'video.mp4'
        shutil.copyfile(self.movie, self.original)
        self.original_sha = self.sha(self.original)
        self.engine = Engine()

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(self, **kwargs):
        return soundtrack_cache.save_source(self.engine, self.movie, self.project, 1.2, **kwargs)

    def assert_original(self):
        self.assertEqual(self.sha(self.original), self.original_sha)
        self.assertFalse(list(self.project.glob('.partial_*')))
        self.assertFalse(list(self.project.glob('.hang_alap.pending_*')))

    def assert_packets(self, output):
        def packets(path):
            return self.ff(['-v', 'error', '-i', path, '-map', '0:v:0',
                            '-c:v', 'copy', '-f', 'framehash', '-']).stdout
        self.assertEqual(packets(output), packets(self.movie))

    def amplitude(self, path, frequency):
        result = self.ff(['-v', 'error', '-i', path, '-map', '0:a:0', '-c:a', 'pcm_s16le',
                          '-ar', '48000', '-ac', '1', '-f', 's16le', '-'])
        data = array('h', result.stdout)[round(.4 * 48000):round(.7 * 48000)]
        real = sum(value * math.cos(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(data))
        imag = sum(value * math.sin(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(data))
        return 2 * math.hypot(real, imag) / len(data)

    def test_cache_is_an_exact_copy_and_repeat_save_does_not_replace_it(self):
        source = self.save()
        self.assertEqual(self.sha(source.video), self.sha(self.movie))
        stat = source.video.stat()
        self.assertEqual(self.save(), source)
        self.assertEqual(source.video.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(source.duration, 1.2)
        self.assertEqual(source.narration, '')
        self.assertFalse(source.mix_narration)
        self.assert_original()

    def test_multiple_music_edits_retain_source_voice_and_remove_previous_music(self):
        source = self.save()
        source_sha = self.sha(source.video)
        (self.project / 'projekt.json').write_text(json.dumps({
            'title': 'Próba', 'music': 'old.wav', 'music_volume': 80,
            'unsupported_future_key': 'ignored'}), encoding='utf-8')
        def forbidden(*args, **kwargs):
            raise AssertionError('A soundtrack edit must never generate a scene.')
        with patch.object(Engine, 'generate_clip', forbidden):
            mixed = soundtrack_cache.remix(self.engine, self.project, music=self.music, music_volume=40)
            plain = soundtrack_cache.remix(self.engine, self.project, music='', music_volume=12)
            muted = soundtrack_cache.remix(self.engine, self.project, music=self.music, music_volume=0)
        self.assertEqual(len({mixed, plain, muted}), 3)
        for movie in (mixed, plain, muted):
            self.assert_packets(movie)
            self.assertGreater(self.amplitude(movie, 880), 2000)
            data = json.loads(movie.with_suffix('.projekt.json').read_text(encoding='utf-8'))
            self.assertLessEqual(set(data), {field.name for field in fields(Project)})
            Project(**data)
        self.assertGreater(self.amplitude(mixed, 440), 700)
        self.assertLess(self.amplitude(plain, 440), 10)
        self.assertLess(self.amplitude(muted, 440), 10)
        self.assertEqual(self.sha(source.video), source_sha)
        self.assert_original()

    def test_external_voice_is_self_contained_when_original_file_is_deleted(self):
        external = self.project / 'Törölt angol narráció.wav'
        shutil.copyfile(self.voice, external)
        source = soundtrack_cache.save_source(self.engine, self.silent, self.project, 1.2,
                                              narration=external)
        self.assertEqual(Path(source.narration).name, 'narracio.flac')
        external.unlink()
        movie = soundtrack_cache.remix(self.engine, self.project, music=self.music, music_volume=40)
        self.assertGreater(self.amplitude(movie, 660), 2000)
        self.assertGreater(self.amplitude(movie, 440), 700)
        self.assertLess(self.amplitude(movie, 880), 10)
        self.assert_packets(movie)
        self.assert_original()

    def test_original_clip_and_external_voice_are_both_retained_when_mix_enabled(self):
        external = self.project / 'Külön narráció.wav'
        shutil.copyfile(self.voice, external)
        (self.project / 'project.json').write_text(json.dumps({
            'title': 'Saját klip', 'narration': 'Saját hangfájl', 'audio': str(external)}),
            encoding='utf-8')
        source = self.save(narration=external, mix_narration=True)
        external.unlink()
        self.assertTrue(soundtrack_cache.load_source(self.project).mix_narration)
        movie = soundtrack_cache.remix(self.engine, self.project, music=self.music, music_volume=40)
        for frequency in (880, 660):
            self.assertGreater(self.amplitude(movie, frequency), 2000)
        self.assertGreater(self.amplitude(movie, 440), 700)
        data = json.loads(movie.with_suffix('.projekt.json').read_text(encoding='utf-8'))
        self.assertEqual(data['audio'], source.narration)
        self.assertEqual(data['narration'], 'Saját hangfájl')
        self.assert_packets(movie)
        self.assert_original()

    def test_valid_cache_rejects_a_different_source_without_overwrite(self):
        source = self.save()
        before = source.manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, 'már van mentett'):
            soundtrack_cache.save_source(self.engine, self.silent, self.project, 1.2)
        self.assertEqual(before, source.manifest.read_bytes())
        self.assert_original()

    def test_missing_cache_never_attempts_an_export(self):
        with patch.object(self.engine, 'run', side_effect=AssertionError('No decoder')):
            with self.assertRaisesRegex(ValueError, 'még nincs mentett hangalap'):
                soundtrack_cache.remix(self.engine, self.project, music=self.music)
        self.assert_original()

    def test_metadata_cannot_select_arbitrary_paths_or_nonfinite_duration(self):
        source = self.save()
        original = source.manifest.read_text(encoding='utf-8')
        replacements = [
            ('video', {'name': '../video.mp4', 'bytes': self.movie.stat().st_size,
                       'sha256': self.sha(self.movie)}),
            ('duration', float('inf')), ('duration', 0), ('duration', True),
            ('schema', True), ('schema', 2), ('mix_narration', 'yes'),
            ('narration', {'name': str(self.voice), 'bytes': self.voice.stat().st_size,
                           'sha256': self.sha(self.voice)}),
        ]
        for key, value in replacements:
            with self.subTest(key=key, value=value):
                data = json.loads(original)
                data[key] = value
                source.manifest.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'nem olvasható vagy megsérült'):
                    soundtrack_cache.load_source(self.project)
        source.manifest.write_text(original, encoding='utf-8')
        self.assert_original()

    def test_changed_or_missing_cached_file_is_detected(self):
        source = self.save()
        original = source.video.read_bytes()
        source.video.write_bytes(b'x' + original[1:])
        with self.assertRaises(ValueError):
            soundtrack_cache.remix(self.engine, self.project)
        source.video.unlink()
        with self.assertRaises(ValueError):
            soundtrack_cache.load_source(self.project)
        self.assert_original()

    def test_corrupt_source_leaves_no_published_cache(self):
        corrupt = self.project / 'Hibás bemenet.mp4'
        corrupt.write_bytes(b'broken container')
        with self.assertRaises(RuntimeError):
            soundtrack_cache.save_source(self.engine, corrupt, self.project, 1.2)
        self.assertFalse((self.project / 'hang_alap').exists())
        self.assert_original()

    def test_cancel_before_save_or_remix_does_not_publish_anything(self):
        self.engine.cancel.set()
        with self.assertRaises(Cancelled):
            self.save()
        self.engine.cancel.clear()
        self.save()
        self.engine.cancel.set()
        with self.assertRaises(Cancelled):
            soundtrack_cache.remix(self.engine, self.project)
        self.assertFalse(list(self.project.glob('video_zene_*')))
        self.assert_original()

    def test_cancel_during_source_validation_removes_the_staging_folder(self):
        with patch.object(self.engine, 'run', side_effect=Cancelled('Megszakítva.')):
            with self.assertRaises(Cancelled):
                self.save()
        self.assertFalse((self.project / 'hang_alap').exists())
        self.assert_original()

    def test_export_failure_or_cancellation_removes_partial_and_keeps_cache(self):
        source = self.save()
        cache_sha = self.sha(source.video)
        for error in (Cancelled('Megszakítva.'), RuntimeError('Export hiba')):
            def fail_export(engine, merged, destination, duration, **kwargs):
                Path(destination).write_bytes(b'partial export')
                raise error
            with self.subTest(error=type(error).__name__):
                with patch.object(soundtrack_cache.background_music, 'export_video', fail_export):
                    with self.assertRaises(type(error)):
                        soundtrack_cache.remix(self.engine, self.project)
                self.assertFalse(list(self.project.glob('video_zene_*')))
                self.assertEqual(self.sha(source.video), cache_sha)
                self.assert_original()

    def test_invalid_music_and_project_description_fail_before_export(self):
        self.save()
        bad_music = self.project / 'Hibás zene.mp3'
        bad_music.write_bytes(b'No audio')
        with patch.object(soundtrack_cache.background_music, 'export_video',
                          side_effect=AssertionError('No export')):
            with self.assertRaises(ValueError):
                soundtrack_cache.remix(self.engine, self.project, music=bad_music, music_volume=0)
            (self.project / 'projekt.json').write_text('[]', encoding='utf-8')
            with self.assertRaises(ValueError):
                soundtrack_cache.remix(self.engine, self.project)
        self.assert_original()

    def test_variant_project_publish_failure_rolls_back_new_movie_only(self):
        self.save()
        (self.project / 'projekt.json').write_text('{"title":"Próba"}', encoding='utf-8')
        real_publish = soundtrack_cache._publish_new_file
        def publish(staging, destination):
            if destination.suffix == '.json':
                raise OSError('A projekt nem írható')
            real_publish(staging, destination)
        with patch.object(soundtrack_cache, '_publish_new_file', publish):
            with self.assertRaises(OSError):
                soundtrack_cache.remix(self.engine, self.project)
        self.assertFalse(list(self.project.glob('video_zene_*')))
        self.assert_original()

    def test_publication_cannot_overwrite_an_existing_file(self):
        stage = self.project / '.temporary.mp4'
        stage.write_bytes(b'new')
        destination = self.project / 'finished.mp4'
        destination.write_bytes(b'previous')
        with self.assertRaises(FileExistsError):
            soundtrack_cache._publish_new_file(stage, destination)
        self.assertEqual(destination.read_bytes(), b'previous')
        self.assertEqual(stage.read_bytes(), b'new')
        self.assert_original()

    def test_partial_unlink_failure_does_not_leak_a_published_movie(self):
        stage = self.project / '.temporary.mp4'
        stage.write_bytes(b'new')
        destination = self.project / 'finished.mp4'
        original_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == stage:
                raise OSError('Cannot remove staging link')
            return original_unlink(path, *args, **kwargs)
        # Exercise the POSIX exclusive-link branch even on Windows.
        with patch.object(soundtrack_cache.os, 'name', 'posix'), patch.object(Path, 'unlink', unlink):
            with self.assertRaises(OSError):
                soundtrack_cache._publish_new_file(stage, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(stage.read_bytes(), b'new')
        self.assert_original()

    @unittest.skipUnless(os.name == 'nt', 'Uses the actual Windows no-overwrite rename semantics.')
    def test_windows_publish_works_without_hard_links(self):
        stage = self.project / '.temporary.mp4'
        stage.write_bytes(b'new movie')
        destination = self.project / 'finished.mp4'
        with patch.object(soundtrack_cache.os, 'link', side_effect=AssertionError('No hard-link support')):
            soundtrack_cache._publish_new_file(stage, destination)
        self.assertEqual(destination.read_bytes(), b'new movie')
        self.assertFalse(stage.exists())
        # A second real Windows rename must refuse the existing destination.
        stage.write_bytes(b'replacement must be refused')
        with self.assertRaises(FileExistsError):
            soundtrack_cache._publish_new_file(stage, destination)
        self.assertEqual(destination.read_bytes(), b'new movie')
        self.assertEqual(stage.read_bytes(), b'replacement must be refused')
        self.assert_original()

    def test_large_cache_hash_is_interruptible_between_chunks(self):
        source = self.save()
        with source.video.open('ab') as video:
            video.write(b'\x00' * (12 * 1024 * 1024))
        data = json.loads(source.manifest.read_text(encoding='utf-8'))
        data['video']['bytes'] = source.video.stat().st_size
        data['video']['sha256'] = self.sha(source.video)
        source.manifest.write_text(json.dumps(data), encoding='utf-8')
        actual_digest = soundtrack_cache._digest
        calls = []
        def cancel_during_digest(path, engine=None):
            self.assertIs(engine, self.engine)
            def check():
                calls.append('chunk')
                if len(calls) == 2:
                    raise Cancelled('Megszakítva a nagy hangalap ellenőrzése közben.')
            with patch.object(engine, 'check', check):
                return actual_digest(path, engine)
        with patch.object(soundtrack_cache, '_digest', cancel_during_digest):
            with self.assertRaises(Cancelled):
                soundtrack_cache.load_source(self.project, self.engine)
        self.assertEqual(len(calls), 2)
        self.assertEqual(source.video.stat().st_size, data['video']['bytes'])
        self.assert_original()

    def test_corrupt_existing_cache_is_never_replaced(self):
        source = self.save()
        source.manifest.write_text('broken JSON', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(source.manifest.read_text(encoding='utf-8'), 'broken JSON')
        self.assert_original()

    def test_invalid_duration_never_creates_a_cache(self):
        for duration in (0, -1, None, 'bad', float('nan'), float('inf'), True):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                soundtrack_cache.save_source(self.engine, self.movie, self.project, duration)
        self.assertFalse((self.project / 'hang_alap').exists())
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
