"""Imported sound and voice mixing, measured in real FFmpeg output."""
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
from ytmuhely import Engine, ffmpeg


class OriginalVolumeTests(unittest.TestCase):
    def test_decimal_comma_and_full_original_volume_default(self):
        self.assertEqual(background_music.validate_video_volume(), 100)
        for value, expected in (('37,5', 37.5), (' 12.5 ', 12.5), (0, 0), (100, 100)):
            self.assertEqual(background_music.validate_video_volume(value), expected)

    def test_invalid_values_have_the_original_sound_label(self):
        for value in (None, True, '', 'abc', -1, 101, float('nan'), float('inf'), 10 ** 1000):
            with self.subTest(value=str(value)[:30]), self.assertRaisesRegex(
                    ValueError, 'eredeti hang.*0 és 100'):
                background_music.validate_video_volume(value)

    def test_no_original_or_muted_original_never_references_missing_stream(self):
        for present, volume in ((False, 100), (True, 0), (False, 0)):
            for voice in (False, True):
                self.assertEqual(background_music.scene_audio(present, volume, voice),
                                 ([], '1:a:0'))

    def test_retained_audio_filter_has_no_video_filter(self):
        for voice in (False, True):
            args, mapping = background_music.scene_audio(True, 50, voice)
            self.assertEqual(args[0], '-filter_complex')
            self.assertEqual(mapping, '[scene_audio]')
            self.assertNotIn(':v', args[1])
            self.assertIn('volume=0.50000000', args[1])


class RealImportedSoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/clip_audio_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.binary = ffmpeg()
        cls.engine = Engine()
        cls.original = cls.root / 'Eredeti hangos klip.mp4'
        cls.no_audio = cls.root / 'Hang nélküli klip.mp4'
        cls.voice = cls.root / 'Angol felolvasás.wav'
        cls.music = cls.root / 'Háttérzene.wav'
        cls.silence = cls.root / 'Csend.wav'
        cls.delayed = cls.root / 'Fél másodperccel később induló hang.mp4'
        cls.shifted = cls.root / 'Öt másodperces konténerkezdet.mkv'
        cls.run_ffmpeg([
            '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=1.2',
            '-t', '1.2', '-c:v', 'libx264', '-threads', '2', '-preset', 'fast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', cls.original,
        ])
        cls.run_ffmpeg(['-i', cls.original, '-map', '0:v:0', '-c:v', 'copy', '-an', cls.no_audio])
        cls.run_ffmpeg(['-i', cls.no_audio, '-itsoffset', '0.5', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:sample_rate=48000:duration=0.7',
                        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                        '-c:a', 'aac', '-b:a', '192k', '-t', '1.2', cls.delayed])
        # The common container start is normalized by FFmpeg. Only the audio's
        # extra 0.5 s belongs in the finished scene, rather than 5.5 s of silence.
        cls.run_ffmpeg(['-i', cls.delayed, '-map', '0:v:0', '-map', '0:a:0',
                        '-c', 'copy', '-output_ts_offset', '5', cls.shifted])
        # Voice ends before the picture, so padding must keep the full scene.
        for path, frequency, duration in ((cls.voice, 880, .9), (cls.music, 660, 2)):
            cls.run_ffmpeg(['-f', 'lavfi', '-i',
                            f'sine=frequency={frequency}:sample_rate=32000:duration={duration}',
                            '-c:a', 'pcm_s16le', path])
        cls.run_ffmpeg(['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                        '-t', '1.2', '-c:a', 'pcm_s16le', cls.silence])

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().is_relative_to(cls.parent.resolve()):
            shutil.rmtree(cls.root)

    @classmethod
    def run_ffmpeg(cls, args):
        return subprocess.run([cls.binary, '-hide_banner', '-nostdin', '-y',
                               *map(str, args)], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def scene(self, name, *, original=True, volume=100, voice=False, source=None):
        source = source or (self.original if original else self.no_audio)
        path = self.root / f'{name}.mp4'
        filters, audio_map = background_music.scene_audio(original, volume, voice)
        if not filters:
            # The caller's existing direct-voice branch pads its input too.
            filters = ['-af', 'apad']
        self.run_ffmpeg(['-i', source, '-i', self.voice if voice else self.silence,
                         *filters, '-map', '0:v:0', '-map', audio_map, '-c:v', 'copy',
                         '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                         '-t', '1.2', path])
        return path

    def samples(self, path):
        wav = self.root / f'{path.stem}_decoded.wav'
        self.run_ffmpeg(['-i', path, '-map', '0:a:0', '-c:a', 'pcm_s16le',
                         '-ar', '48000', '-ac', '2', wav])
        with wave.open(str(wav), 'rb') as source:
            self.assertEqual(source.getnchannels(), 2)
            self.assertEqual(source.getframerate(), 48000)
            data = array('h', source.readframes(source.getnframes()))
        self.assertAlmostEqual(len(data) / 96000, 1.2, delta=.04)
        return data[::2]

    @staticmethod
    def amplitude(samples, frequency, start=.4, end=.7):
        values = samples[round(start * 48000):round(end * 48000)]
        real = sum(value * math.cos(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(values))
        imag = sum(value * math.sin(2 * math.pi * frequency * n / 48000)
                   for n, value in enumerate(values))
        return 2 * math.hypot(real, imag) / len(values)

    def assert_video_unchanged(self, path, *, stereo=True):
        def digest(source):
            return self.run_ffmpeg(['-v', 'error', '-i', source, '-map', '0:v:0',
                                    '-c:v', 'copy', '-f', 'framehash', '-']).stdout
        # Packet timestamps, durations and compressed pixel data all match.
        self.assertEqual(digest(path), digest(self.original))
        details = subprocess.run([self.binary, '-hide_banner', '-i', str(path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE).stderr.decode(
                                     'utf-8', 'replace')
        self.assertRegex(details, r'Duration: 00:00:01\.2\d')
        self.assertIn('48000 Hz, stereo' if stereo else '48000 Hz, mono', details)

    def test_original_only_retains_tone_and_selected_level(self):
        loud = self.scene('Eredeti teljes hangerő')
        quiet = self.scene('Eredeti fél hangerő', volume=50)
        loud_samples, quiet_samples = self.samples(loud), self.samples(quiet)
        self.assertGreater(self.amplitude(loud_samples, 440), 2000)
        self.assertLess(self.amplitude(loud_samples, 880), 30)
        self.assertAlmostEqual(self.amplitude(loud_samples, 440) /
                               self.amplitude(quiet_samples, 440), 2, delta=.08)
        self.assert_video_unchanged(loud)

    def test_original_and_generated_voice_are_both_audible_and_padded(self):
        output = self.scene('Eredeti és új angol hang', voice=True)
        audio = self.samples(output)
        for frequency in (440, 880):
            self.assertGreater(self.amplitude(audio, frequency), 2000)
        self.assertGreater(self.amplitude(audio, 440, 1, 1.1), 2000)
        self.assertLess(self.amplitude(audio, 880, 1, 1.1), 30)
        self.assert_video_unchanged(output)

    def test_delayed_original_audio_keeps_its_initial_silence_with_or_without_voice(self):
        for voice in (False, True):
            with self.subTest(voice=voice):
                output = self.scene(f'Késleltetett eredeti hang {voice}',
                                    source=self.delayed, voice=voice)
                audio = self.samples(output)
                self.assertLess(self.amplitude(audio, 440, .1, .3), 20)
                self.assertGreater(self.amplitude(audio, 440, .65, .85), 2000)
                if voice:
                    self.assertGreater(self.amplitude(audio, 880, .1, .3), 2000)
                self.assert_video_unchanged(output)

    def test_container_start_is_not_added_as_extra_audio_silence(self):
        output = self.scene('Közös konténerkezdet levonva', source=self.shifted)
        audio = self.samples(output)
        self.assertLess(self.amplitude(audio, 440, .1, .3), 20)
        self.assertGreater(self.amplitude(audio, 440, .65, .85), 2000)

    def test_muting_original_retains_voice(self):
        output = self.scene('Eredeti némítva angol hanggal', voice=True, volume=0)
        audio = self.samples(output)
        self.assertLess(self.amplitude(audio, 440), 30)
        self.assertGreater(self.amplitude(audio, 880), 2000)
        self.assert_video_unchanged(output)

    def test_muting_original_without_voice_produces_silence(self):
        output = self.scene('Néma jelenet', volume=0)
        self.assertLess(max(map(abs, self.samples(output))), 2)
        self.assert_video_unchanged(output)

    def test_video_without_audio_accepts_voice_or_silence(self):
        for voice in (False, True):
            with self.subTest(voice=voice):
                output = self.scene(f'Néma forrás új hang {voice}', original=False, voice=voice)
                audio = self.samples(output)
                if voice:
                    self.assertGreater(self.amplitude(audio, 880), 2000)
                else:
                    self.assertLess(max(map(abs, audio)), 2)
                self.assert_video_unchanged(output)

    def test_own_external_narration_mixes_original_without_music(self):
        output = self.root / 'Eredeti és saját külső hang.mp4'
        background_music.export_video(self.engine, self.original, output, 1.2,
                                       narration=self.voice, mix_narration=True)
        audio = self.samples(output)
        for frequency in (440, 880):
            self.assertGreater(self.amplitude(audio, frequency), 2000)
        self.assertGreater(self.amplitude(audio, 440, 1, 1.1), 2000)
        self.assertLess(self.amplitude(audio, 880, 1, 1.1), 30)
        self.assert_video_unchanged(output)

    def test_own_external_narration_original_and_music_all_remain_audible(self):
        output = self.root / 'Eredeti saját hang és zene.mp4'
        background_music.export_video(self.engine, self.original, output, 1.2,
                                       narration=self.voice, mix_narration=True,
                                       music=self.music, music_volume=40)
        audio = self.samples(output)
        for frequency in (440, 880):
            self.assertGreater(self.amplitude(audio, frequency), 2000)
        self.assertGreater(self.amplitude(audio, 660), 1000)
        self.assert_video_unchanged(output)

    def test_direct_export_retains_delayed_base_audio_when_mixing_voice_or_music(self):
        options = ({'music': self.music, 'music_volume': 40},
                   {'narration': self.voice, 'mix_narration': True},
                   {'narration': self.voice, 'mix_narration': True,
                    'music': self.music, 'music_volume': 40})
        for index, kwargs in enumerate(options):
            with self.subTest(options=kwargs):
                output = self.root / f'Késleltetett alap közvetlen export {index}.mp4'
                background_music.export_video(self.engine, self.delayed, output,
                                               1.2, **kwargs)
                audio = self.samples(output)
                self.assertLess(self.amplitude(audio, 440, .1, .3), 20)
                self.assertGreater(self.amplitude(audio, 440, .65, .85), 2000)
                self.assert_video_unchanged(output)

    def test_delayed_external_narration_keeps_its_timing_when_music_is_added(self):
        for music in ('', self.music):
            with self.subTest(music=bool(music)):
                output = self.root / f'Késleltetett külső narráció {bool(music)}.mp4'
                background_music.export_video(self.engine, self.no_audio, output,
                                               1.2, narration=self.delayed,
                                               music=music, music_volume=40)
                audio = self.samples(output)
                self.assertLess(self.amplitude(audio, 440, .1, .3), 20)
                self.assertGreater(self.amplitude(audio, 440, .65, .85), 2000)
                self.assert_video_unchanged(output, stereo=bool(music))

    def test_hot_original_and_external_voice_are_limited_with_or_without_music(self):
        hot_original = self.root / 'Hangos eredeti klip.mp4'
        hot_voice = self.root / 'Hangos saját hang.wav'
        self.run_ffmpeg(['-i', self.original, '-map', '0:v:0', '-map', '0:a:0',
                         '-c:v', 'copy', '-af', 'volume=7', '-c:a', 'aac',
                         '-b:a', '192k', hot_original])
        self.run_ffmpeg(['-i', self.voice, '-af', 'volume=7', '-c:a', 'pcm_s16le', hot_voice])
        for music in ('', self.music):
            with self.subTest(music=bool(music)):
                output = self.root / f'Erős eredeti és saját hang zene {bool(music)}.mp4'
                background_music.export_video(self.engine, hot_original, output, 1.2,
                                               narration=hot_voice, mix_narration=True,
                                               music=music, music_volume=100)
                audio = self.samples(output)
                self.assertGreater(self.amplitude(audio, 440), 8000)
                self.assertGreater(self.amplitude(audio, 880), 8000)
                self.assertLess(max(map(abs, audio)), 32767)
                self.assert_video_unchanged(output)

    def test_external_voice_default_still_replaces_original(self):
        output = self.root / 'Saját hang lecserélve.mp4'
        background_music.export_video(self.engine, self.original, output, 1.2,
                                       narration=self.voice)
        audio = self.samples(output)
        self.assertLess(self.amplitude(audio, 440), 30)
        self.assertGreater(self.amplitude(audio, 880), 2000)
        # The default keeps the previous mono-narration export behavior.
        self.assertEqual(background_music.export_command('a.mp4', 'b.mp4', 1.2,
                         narration=self.voice, binary='ffmpeg')[-1], 'b.mp4')


if __name__ == '__main__':
    unittest.main()
