"""Real local sound mixing, preflight failures and unchanged video packets."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

import background_music
from ytmuhely import Cancelled, Engine, Project, ffmpeg


class SettingsTests(unittest.TestCase):
    def test_default_level_remains_twelve_percent(self):
        self.assertEqual(background_music.validate_settings(''), 12.0)
        self.assertEqual(background_music.validate_settings('', '37.5'), 37.5)
        self.assertEqual(background_music.validate_settings('', '12,5'), 12.5)
        self.assertEqual(background_music.validate_settings('', 0), 0)
        self.assertEqual(background_music.validate_settings('', 100), 100)

    def test_invalid_volume_never_reaches_a_renderer(self):
        for value in ('not a number', float('nan'), float('inf'), -1, 101, None, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '0 és 100'):
                background_music.validate_settings('', value)

    def test_missing_selection_is_rejected_even_when_muted(self):
        with self.assertRaisesRegex(ValueError, 'Nem található'):
            background_music.validate_settings(ROOT / 'does-not-exist-music.wav', 0)

    def test_unselected_music_does_not_start_ffmpeg(self):
        class MustNotRun:
            def run(self, *args, **kwargs):
                raise AssertionError('No selected music must require no decoder.')
        self.assertEqual(background_music.validate_audio(MustNotRun(), '', 12), 12)

    def test_cancellation_during_preflight_propagates(self):
        class CancelEngine:
            def run(self, *args, **kwargs):
                raise Cancelled('Megszakítva.')
        with self.assertRaises(Cancelled):
            background_music.validate_audio(CancelEngine(), __file__, 12)

    def test_empty_audio_decode_is_not_accepted(self):
        class EmptyEngine:
            def run(self, *args, **kwargs):
                return 'Output file is empty.'
        with self.assertRaisesRegex(ValueError, 'nem tartalmaz'):
            background_music.validate_audio(EmptyEngine(), __file__, 12)

    def test_invalid_export_duration_is_rejected(self):
        for duration in (0, -1, float('nan'), float('inf'), 'abc'):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                background_music.export_command('in.mp4', 'out.mp4', duration,
                                                binary='ffmpeg')

    def test_old_project_load_keeps_default_music_level(self):
        # Old JSON has a music path but no configurable volume field.
        project = Project(**{'title': 'Régi projekt', 'music': ''})
        self.assertEqual(project.music_volume, 12)


class RealSoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/background_music_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.engine = Engine()
        cls.binary = ffmpeg()
        cls.merged = cls.root / 'Néma kép – rövid videó.mp4'
        cls.short = cls.root / 'Rövid zene szóközzel.wav'
        cls.long = cls.root / 'Hosszú zene.wav'
        cls.voice = cls.root / 'Saját angol narráció.wav'
        cls.run_ffmpeg([
            '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30',
            '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
            '-t', '1.2', '-c:v', 'libx264', '-threads', '2', '-preset', 'fast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', cls.merged,
        ])
        for path, frequency, duration in ((cls.short, 440, .22), (cls.long, 440, 2),
                                          (cls.voice, 880, .9)):
            cls.run_ffmpeg(['-f', 'lavfi', '-i',
                            f'sine=frequency={frequency}:sample_rate=48000:duration={duration}',
                            '-c:a', 'pcm_s16le', path])

    @classmethod
    def tearDownClass(cls):
        resolved = cls.root.resolve()
        if resolved.is_relative_to(cls.parent.resolve()):
            shutil.rmtree(resolved)

    @classmethod
    def run_ffmpeg(cls, args):
        return subprocess.run([cls.binary, '-hide_banner', '-nostdin', '-y',
                               *[str(x) for x in args]], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def export(self, name, **options):
        path = self.root / f'{name}.mp4'
        background_music.export_video(self.engine, self.merged, path, 1.2, **options)
        return path

    def samples(self, path):
        wav = self.root / f'{path.stem}_decoded.wav'
        self.run_ffmpeg(['-i', path, '-map', '0:a:0', '-c:a', 'pcm_s16le',
                         '-ar', '48000', '-ac', '2', wav])
        with wave.open(str(wav), 'rb') as source:
            self.assertEqual(source.getnchannels(), 2)
            self.assertEqual(source.getframerate(), 48000)
            data = array('h', source.readframes(source.getnframes()))
        self.assertAlmostEqual(len(data) / 2 / 48000, 1.2, delta=.04)
        return data[::2]

    @staticmethod
    def rms(samples, start=.4, end=.7):
        chunk = samples[round(start * 48000):round(end * 48000)]
        return math.sqrt(sum(value * value for value in chunk) / len(chunk))

    @staticmethod
    def amplitude(samples, frequency, start=.4, end=.7):
        chunk = samples[round(start * 48000):round(end * 48000)]
        real = sum(value * math.cos(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(chunk))
        imag = sum(value * math.sin(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(chunk))
        return 2 * math.hypot(real, imag) / len(chunk)

    def assert_video_unchanged(self, path, *, stereo=True):
        def digest(source):
            return self.run_ffmpeg(['-v', 'error', '-i', source, '-map', '0:v:0',
                                    '-c:v', 'copy', '-f', 'framehash', '-']).stdout
        # Includes every video packet's timestamp, duration, size and SHA-256.
        self.assertEqual(digest(path), digest(self.merged))
        metadata = subprocess.run([self.binary, '-hide_banner', '-i', str(path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        detail = metadata.stderr.decode('utf-8', 'replace')
        self.assertIn('48000 Hz, stereo' if stereo else '48000 Hz, mono', detail)
        self.assertRegex(detail, r'Duration: 00:00:01\.2\d')

    def test_valid_audio_is_preflighted_even_at_zero_volume(self):
        for volume in (0, 12, 100):
            self.assertEqual(background_music.validate_audio(self.engine, self.short, volume), volume)

    def test_no_audio_video_and_corrupt_file_fail_preflight(self):
        no_audio = self.root / 'Csak kép.mp4'
        self.run_ffmpeg(['-i', self.merged, '-map', '0:v:0', '-an', '-c:v', 'copy', no_audio])
        broken = self.root / 'Hibás zene.mp3'
        broken.write_bytes(b'This is not a playable audio file.')
        for path in (no_audio, broken):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'nem olvasható hang'):
                background_music.validate_audio(self.engine, path, 12)

    def test_short_music_loops_and_fades_without_changing_video(self):
        output = self.export('rövid zene ismételve', music=self.short, music_volume=50)
        audio = self.samples(output)
        self.assertGreater(self.rms(audio, .65, .75), 800)
        self.assertLess(self.rms(audio, .015, .045), self.rms(audio) * .25)
        self.assertLess(self.rms(audio, 1.15, 1.18), self.rms(audio) * .25)
        self.assert_video_unchanged(output)

    def test_long_music_trims_and_volume_selection_changes_energy(self):
        quiet = self.export('halk zene', music=self.long, music_volume=10)
        louder = self.export('hangos zene', music=self.long, music_volume=40)
        ratio = self.rms(self.samples(louder)) / self.rms(self.samples(quiet))
        self.assertAlmostEqual(ratio, 4, delta=.2)
        self.assert_video_unchanged(louder)

    def test_own_narration_and_music_are_both_audible(self):
        output = self.export('narráció zenével', music=self.long,
                             music_volume=40, narration=self.voice)
        audio = self.samples(output)
        self.assertGreater(self.amplitude(audio, 440), 1000)
        self.assertGreater(self.amplitude(audio, 880), 2000)
        # The narration ends at 0.9 s; music continues through the last scene.
        self.assertLess(self.amplitude(audio, 880, 1, 1.1), 50)
        self.assertGreater(self.amplitude(audio, 440, 1, 1.1), 300)
        self.assert_video_unchanged(output)

    def test_existing_generated_narration_is_retained_with_music(self):
        narrated = self.root / 'Már narrált videó.mp4'
        self.run_ffmpeg(['-i', self.merged, '-i', self.voice, '-map', '0:v:0',
                         '-map', '1:a:0', '-c:v', 'copy', '-af', 'apad',
                         '-t', '1.2', '-c:a', 'aac', '-b:a', '192k', narrated])
        output = self.root / 'Eredeti narráció és új zene.mp4'
        background_music.export_video(self.engine, narrated, output, 1.2,
                                       music=self.long, music_volume=40)
        audio = self.samples(output)
        self.assertGreater(self.amplitude(audio, 440), 1000)
        self.assertGreater(self.amplitude(audio, 880), 2000)
        self.assert_video_unchanged(output)

    def test_muted_music_matches_no_music_and_own_voice_stays_audible(self):
        plain = self.export('csak narráció', narration=self.voice)
        muted = self.export('néma háttérzene', music=self.short,
                            music_volume=0, narration=self.voice)
        self.assertEqual(self.samples(plain), self.samples(muted))
        self.assertGreater(self.amplitude(self.samples(plain), 880), 2000)
        no_audio = self.export('zene és narráció nélkül')
        self.assertLess(self.rms(self.samples(no_audio)), 1)
        self.assert_video_unchanged(muted, stereo=False)

    def test_loud_music_and_voice_do_not_hard_clip(self):
        hot_music = self.root / 'Erős zene.wav'
        hot_voice = self.root / 'Erős hang.wav'
        for path, frequency in ((hot_music, 440), (hot_voice, 880)):
            self.run_ffmpeg(['-f', 'lavfi', '-i',
                             f'sine=frequency={frequency}:sample_rate=48000:duration=1.2',
                             '-af', 'volume=7', '-c:a', 'pcm_s16le', path])
        output = self.export('erős keverés', music=hot_music, music_volume=100,
                             narration=hot_voice)
        audio = self.samples(output)
        self.assertGreater(self.rms(audio), 8000)
        self.assertLess(max(abs(value) for value in audio), 32767)


if __name__ == '__main__':
    unittest.main()
