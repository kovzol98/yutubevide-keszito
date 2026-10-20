"""Imported clips are checked without rendering, including cover-only media."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

from PIL import Image
from media_probe import probe_video
from ytmuhely import Cancelled, Engine, ffmpeg


class ProbeMetadataTests(unittest.TestCase):
    def fake(self, output=None, error=None):
        class Fake:
            def run(self, args, timeout):
                self.args, self.timeout = args, timeout
                if error is not None:
                    raise error
                return output
        return Fake()

    def test_precise_header_duration_and_nondefault_audio_stream(self):
        engine = self.fake(
            'Input #0, matroska, from file:\n'
            '  Duration: 01:02:03.45, start: 0.000000\n'
            '  Stream #0:0: Video: h264\n'
            '  Stream #0:2(eng): Audio: aac, 48000 Hz\n'
            'Output #0, null, to pipe:\nframe=1\nprogress=end\n')
        self.assertEqual(probe_video(engine, __file__),
                         {'duration': 3723.45, 'has_audio': True})
        self.assertEqual(engine.timeout, 30)
        self.assertEqual(engine.args[engine.args.index('-map') + 1], '0:V:0')
        self.assertIn('copy', engine.args)
        self.assertNotIn('-y', engine.args)

    def test_output_metadata_does_not_invent_an_input_audio_stream(self):
        engine = self.fake(
            'Duration: 00:00:01.23\nStream #0:0: Video: h264\n'
            'Output #0, null, to pipe:\nStream #0:1: Audio: aac\nframe=1\n')
        self.assertEqual(probe_video(engine, __file__),
                         {'duration': 1.23, 'has_audio': False})

    def test_unknown_zero_and_empty_video_are_rejected(self):
        for duration, frames in (('N/A', 1), ('00:00:00.00', 1),
                                 ('00:00:01.00', 0)):
            with self.subTest(duration=duration, frames=frames), self.assertRaises(ValueError):
                probe_video(self.fake(f'Duration: {duration}\nframe={frames}\n'), __file__)

    def test_cancellation_is_not_reported_as_bad_video(self):
        with self.assertRaises(Cancelled):
            probe_video(self.fake(error=Cancelled()), __file__)

    def test_missing_and_invalid_paths_do_not_start_ffmpeg(self):
        class MustNotRun:
            def run(self, *args, **kwargs):
                raise AssertionError('Missing files must fail before probing.')
        for path in (None, ROOT / 'does-not-exist.mp4', ROOT):
            with self.subTest(path=path), self.assertRaises(ValueError):
                probe_video(MustNotRun(), path)


class RealVideoProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'work/v2_tests/media_probe_fixtures'
        cls.root = cls.parent / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.engine = Engine()
        cls.silent = cls.root / 'Néma jelenet szóközzel.mp4'
        cls.sound = cls.root / 'Saját hangos felvétel.mp4'
        cls.audio = cls.root / 'Csak hang.wav'
        cls.cover = cls.root / 'Zene borítóképpel.mp3'
        cls.ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30',
                    '-frames:v', '37', '-an', '-c:v', 'libx264', '-threads', '2',
                    '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', cls.silent])
        cls.ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.234',
                    '-c:a', 'pcm_s16le', cls.audio])
        cls.ffmpeg(['-i', cls.silent, '-i', cls.audio, '-map', '0:v:0',
                    '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', cls.sound])
        cover_image = cls.root / 'Borító.jpg'
        Image.new('RGB', (64, 64), '#bd9c58').save(cover_image)
        cls.ffmpeg(['-i', cls.audio, '-i', cover_image, '-map', '0:a:0',
                    '-map', '1:v:0', '-c:a', 'libmp3lame', '-c:v', 'copy',
                    '-disposition:v', 'attached_pic', cls.cover])

    @classmethod
    def tearDownClass(cls):
        resolved = cls.root.resolve()
        if resolved.is_relative_to(cls.parent.resolve()):
            shutil.rmtree(resolved)

    @staticmethod
    def ffmpeg(args):
        return subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', '-y',
                               *map(str, args)], check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def test_fractional_duration_and_silent_video(self):
        result = probe_video(self.engine, self.silent)
        self.assertAlmostEqual(result['duration'], 37 / 30, delta=.011)
        self.assertFalse(result['has_audio'])

    def test_clip_audio_is_reported(self):
        result = probe_video(self.engine, self.sound)
        self.assertAlmostEqual(result['duration'], 1.234, delta=.011)
        self.assertTrue(result['has_audio'])

    def test_audio_only_and_audio_with_attached_cover_are_not_videos(self):
        for path in (self.audio, self.cover):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'nem olvasható videó'):
                probe_video(self.engine, path)

    def test_corrupt_file_has_a_friendly_error(self):
        corrupt = self.root / 'Hibás videó.mp4'
        corrupt.write_bytes(b'not a movie')
        with self.assertRaisesRegex(ValueError, 'nem olvasható videó'):
            probe_video(self.engine, corrupt)


if __name__ == '__main__':
    unittest.main()
