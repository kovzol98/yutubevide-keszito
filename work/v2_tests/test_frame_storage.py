"""Regression checks for isolated RIFE attempts and safe frame publication.

Small real PNG sequences exercise the filesystem boundaries. Model processes
and FFmpeg are replaced by a recording engine; this suite starts no GPU work.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

from PIL import Image
import frame_storage
import motion_smooth
from ytmuhely import Cancelled


OLD = (210, 20, 20)
GPU = (220, 160, 20)
CPU = (20, 180, 50)


def save_png(path, color):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), color).save(path)


def output_sequence(folder, count, color):
    for index in range(1, count + 1):
        save_png(Path(folder) / ('%08d.png' % index), color)


def pixels(path):
    with Image.open(path) as image:
        return image.convert('RGB').getpixel((0, 0))


class RecordingEngine:
    def __init__(self, interpolate):
        self.interpolate = interpolate
        self.calls = []
        self.messages = []
        self.checks = 0
        self.attempts = []

    def check(self):
        self.checks += 1

    def log(self, value):
        self.messages.append(value)

    def run(self, args, **kwargs):
        args = [str(value) for value in args]
        self.calls.append((args, kwargs))
        if args[0].endswith('rife-ncnn-vulkan.exe'):
            folder = Path(args[args.index('-o') + 1])
            attempt = dict(folder=folder, before=sorted(p.name for p in folder.iterdir()),
                           cpu='-g' in args, target=int(args[args.index('-n') + 1]))
            self.attempts.append(attempt)
            return self.interpolate(attempt)
        if args[0] == 'mock-ffmpeg':
            # The actual encoder is outside this unit test; preserve its path
            # contract so publication and the final metadata still execute.
            Path(args[-1]).write_bytes(b'encoded test video')
            return 'mock encoding completed'
        raise AssertionError('Unexpected external program: ' + args[0])


class FrameStorageTests(unittest.TestCase):
    def setUp(self):
        self.test_root = ROOT / 'work/frame_storage_tests'
        self.base = self.test_root / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.scene = self.base / 'scene'
        self.scene.mkdir()
        self.native = self.base / 'native'
        self.native.mkdir()
        for index in range(3):
            save_png(self.native / ('frame_%05d.png' % index), (index * 40, 60, 80))
        self.ai = self.base / 'AI'
        binary = self.ai / 'interpolation/rife-ncnn-vulkan.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'not executed')
        model = binary.parent / 'rife-v4.6'
        model.mkdir()
        for name in ('flownet.bin', 'flownet.param'):
            (model / name).write_bytes(b'not loaded')
        self.ffmpeg = patch('ytmuhely.ffmpeg', return_value='mock-ffmpeg')
        self.ffmpeg.start()
        self.addCleanup(self.ffmpeg.stop)

    def tearDown(self):
        # Explicit workspace paths avoid Windows TemporaryDirectory ACL issues.
        resolved = self.base.resolve()
        assert resolved.is_relative_to(self.test_root.resolve()) and resolved != self.test_root.resolve()
        shutil.rmtree(resolved)

    def generate(self, engine, *, scene=None, fps=30, native=None,force_cpu=False):
        return motion_smooth.generate(engine, self.ai, native or self.native, fps, scene or self.scene,force_cpu=force_cpu)

    def test_previous_long_run_is_preserved_and_does_not_count_in_new_short_run(self):
        previous = self.scene / 'folyamatos_kockak'
        output_sequence(previous, 12, OLD)

        def complete(attempt):
            self.assertEqual(attempt['before'], [])
            self.assertFalse(attempt['cpu'])
            output_sequence(attempt['folder'], attempt['target'], GPU)
            return 'GPU complete'

        engine = RecordingEngine(complete)
        result = self.generate(engine)
        current = sorted(previous.glob('*.png'))
        backups = list(self.scene.glob('folyamatos_kockak.previous_*'))
        self.assertTrue(result.is_file())
        self.assertEqual(len(current), 3)
        self.assertEqual([pixels(path) for path in current], [GPU] * 3)
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(list(backups[0].glob('*.png'))), 12)
        self.assertTrue(all(pixels(path) == OLD for path in backups[0].glob('*.png')))
        self.assertNotEqual(engine.attempts[0]['folder'], previous)
        settings = json.loads((self.scene / 'mozgas_simito.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['native_frames'], 3)
        self.assertEqual(settings['output_frames'], 3)
        self.assertEqual(settings['device'],'gpu')
        self.assertEqual(settings['model'],'RIFE v4.6 Vulkan')
        encode_args = engine.calls[-1][0]
        self.assertEqual(encode_args[encode_args.index('-i') + 1], str(previous / '%08d.png'))

    def test_gpu_failure_or_incomplete_output_retries_cpu_in_a_fresh_empty_folder(self):
        for failure in ('exception', 'missing frame'):
            with self.subTest(failure=failure):
                scene = self.base / ('retry_' + failure.replace(' ', '_'))
                scene.mkdir()
                output_sequence(scene / 'folyamatos_kockak', 7, OLD)

                def partial_then_success(attempt):
                    self.assertEqual(attempt['before'], [])
                    if not attempt['cpu']:
                        output_sequence(attempt['folder'], 1, GPU)
                        if failure == 'exception':
                            raise RuntimeError('Simulated GPU failure after one PNG')
                        return 'GPU exited successfully but produced too few frames'
                    output_sequence(attempt['folder'], attempt['target'], CPU)
                    return 'CPU complete'

                engine = RecordingEngine(partial_then_success)
                self.generate(engine, scene=scene)
                self.assertEqual([attempt['cpu'] for attempt in engine.attempts], [False, True])
                gpu, cpu = engine.attempts
                self.assertNotEqual(gpu['folder'], cpu['folder'])
                self.assertEqual(pixels(gpu['folder'] / '00000001.png'), GPU)
                published = sorted((scene / 'folyamatos_kockak').glob('*.png'))
                self.assertEqual(len(published), 3)
                self.assertTrue(all(pixels(path) == CPU for path in published))
                backups = list(scene.glob('folyamatos_kockak.previous_*'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(len(list(backups[0].glob('*.png'))), 7)
                self.assertIn('CPU complete', (scene / 'mozgas_simito.log').read_text(encoding='utf-8'))
                settings=json.loads((scene/'mozgas_simito.json').read_text(encoding='utf-8'))
                self.assertEqual(settings['device'],'cpu')
                self.assertEqual(settings['model'],'RIFE v4.6 CPU')

    def test_forced_cpu_never_attempts_vulkan_and_reports_actual_device(self):
        def complete(attempt):
            self.assertTrue(attempt['cpu'])
            output_sequence(attempt['folder'],attempt['target'],CPU)
            return 'CPU complete'
        engine=RecordingEngine(complete)
        self.generate(engine,force_cpu=True)
        self.assertEqual([attempt['cpu'] for attempt in engine.attempts],[True])
        args=engine.calls[0][0]
        self.assertEqual(args[args.index('-g')+1],'-1')
        settings=json.loads((self.scene/'mozgas_simito.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['device'],'cpu')
        self.assertEqual(settings['model'],'RIFE v4.6 CPU')

    def test_failed_forced_cpu_is_not_retried(self):
        engine=RecordingEngine(lambda attempt: (_ for _ in ()).throw(RuntimeError('CPU unavailable')))
        with self.assertRaisesRegex(RuntimeError,'CPU unavailable'):
            self.generate(engine,force_cpu=True)
        self.assertEqual([attempt['cpu'] for attempt in engine.attempts],[True])

    def test_cancelled_forced_cpu_preserves_native_and_previous_frames(self):
        previous=self.scene/'folyamatos_kockak'
        output_sequence(previous,5,OLD)
        def cancelled(attempt):
            output_sequence(attempt['folder'],1,CPU)
            raise Cancelled('User stopped CPU interpolation')
        engine=RecordingEngine(cancelled)
        with self.assertRaises(Cancelled):self.generate(engine,force_cpu=True)
        self.assertEqual([attempt['cpu'] for attempt in engine.attempts],[True])
        self.assertEqual(len(list(self.native.glob('frame_*.png'))),3)
        self.assertEqual(len(list(previous.glob('*.png'))),5)
        self.assertTrue(all(pixels(path)==OLD for path in previous.glob('*.png')))

    def test_incomplete_input_does_not_start_a_motor_or_make_staging(self):
        for numbers in ((0,), (0, 2), (1, 2)):
            with self.subTest(numbers=numbers):
                folder = self.base / ('native_' + '_'.join(map(str, numbers)))
                folder.mkdir()
                for index in numbers:
                    save_png(folder / ('frame_%05d.png' % index), GPU)
                engine = RecordingEngine(lambda attempt: self.fail('Motor must not start'))
                with self.assertRaises(RuntimeError):
                    self.generate(engine, native=folder)
                self.assertEqual(engine.calls, [])
                self.assertEqual(list(self.scene.iterdir()), [])

    def test_cancelled_gpu_run_never_retries_cpu_and_preserves_previous_frames(self):
        previous = self.scene / 'folyamatos_kockak'
        output_sequence(previous, 8, OLD)

        def cancelled(attempt):
            output_sequence(attempt['folder'], 1, GPU)
            raise Cancelled('User stopped the running GPU process')

        engine = RecordingEngine(cancelled)
        with self.assertRaises(Cancelled):
            self.generate(engine)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual([attempt['cpu'] for attempt in engine.attempts], [False])
        self.assertEqual(len(list(previous.glob('*.png'))), 8)
        self.assertTrue(all(pixels(path) == OLD for path in previous.glob('*.png')))
        self.assertEqual(list(self.scene.glob('folyamatos_kockak.previous_*')), [])
        self.assertFalse((self.scene / 'folyamatos_mozgas.mp4').exists())

    def test_invalid_fps_does_not_start_a_motor_or_make_staging(self):
        for fps in (0, -1, float('nan'), float('inf'), -float('inf'), 'bad fps'):
            with self.subTest(fps=fps):
                engine = RecordingEngine(lambda attempt: self.fail('Motor must not start'))
                with self.assertRaises(ValueError):
                    self.generate(engine, fps=fps)
                self.assertEqual(engine.calls, [])
                self.assertEqual(list(self.scene.iterdir()), [])

    def test_staging_folders_are_unique_and_publication_keeps_previous_content(self):
        first = frame_storage.staging_folder(self.scene, 'download')
        second = frame_storage.staging_folder(self.scene, 'download')
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())
        output_sequence(first, 2, GPU)
        destination = self.scene / 'frames'
        output_sequence(destination, 4, OLD)
        self.assertEqual(frame_storage.publish_folder(first, destination), destination)
        self.assertFalse(first.exists())
        self.assertEqual(len(list(destination.glob('*.png'))), 2)
        backups = list(self.scene.glob('frames.previous_*'))
        self.assertEqual(len(backups), 1)
        self.assertTrue(all(pixels(path) == OLD for path in backups[0].glob('*.png')))
        self.assertEqual(len(list(backups[0].glob('*.png'))), 4)
        self.assertEqual(list(second.iterdir()), [])

    def test_failed_publication_restores_previous_successful_folder(self):
        staging = frame_storage.staging_folder(self.scene, 'download')
        output_sequence(staging, 2, GPU)
        destination = self.scene / 'frames'
        output_sequence(destination, 4, OLD)
        actual_replace = Path.replace

        def fail_only_new_publish(path, target):
            if path == staging:
                raise OSError('Simulated rename failure publishing the new frames')
            return actual_replace(path, target)

        with patch.object(Path, 'replace', fail_only_new_publish):
            with self.assertRaisesRegex(OSError, 'rename failure'):
                frame_storage.publish_folder(staging, destination)
        self.assertEqual(len(list(destination.glob('*.png'))), 4)
        self.assertTrue(all(pixels(path) == OLD for path in destination.glob('*.png')))
        self.assertEqual(len(list(staging.glob('*.png'))), 2)
        self.assertEqual(list(self.scene.glob('frames.previous_*')), [])

    def test_publication_rejects_cross_scene_before_changing_the_old_folder(self):
        elsewhere = self.base / 'other_scene'
        elsewhere.mkdir()
        staging = frame_storage.staging_folder(elsewhere, 'download')
        output_sequence(staging, 2, GPU)
        destination = self.scene / 'frames'
        output_sequence(destination, 4, OLD)
        with self.assertRaises(ValueError):
            frame_storage.publish_folder(staging, destination)
        self.assertEqual(len(list(destination.glob('*.png'))), 4)
        self.assertEqual(len(list(staging.glob('*.png'))), 2)
        self.assertEqual(list(self.scene.glob('frames.previous_*')), [])


if __name__ == '__main__':
    unittest.main()
