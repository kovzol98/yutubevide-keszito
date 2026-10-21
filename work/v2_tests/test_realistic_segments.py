"""Model-free integration checks for sequential Wan continuation frames."""
from contextlib import ExitStack
import json
import math
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import comfy_video
import realistic_video
import ytmuhely as app
from video_workload import plan_workload


class RealisticSegmentTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'work/v2_tests/realistic_segment_fixtures'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.work = self.folder / 'temporary'
        self.work.mkdir()
        self.original = self.folder / 'source.png'
        self._picture((200, 280), 0).save(self.original)
        self.logs = []
        self.progress = []
        self.engine = app.Engine(self.logs.append)
        self.engine.report_scene_progress = self.progress.append
        self.project = app.Project(ai_root=str(self.folder),
            ai_quality='Részletes • 1280×704',
            image_motion='Jelenetleírás szerinti mozgás',
            video_prompt='The subjects gently turn their shoulders.')
        self.scene = self.folder / 'AI_jelenetek/jelenet_001'
        self.calls = []
        self.generated = []
        self.interpolated = []

    def tearDown(self):
        assert self.folder.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.folder)

    @staticmethod
    def _picture(size, marker):
        picture = Image.new('RGB', size, (30, 60, 90))
        draw = ImageDraw.Draw(picture)
        draw.rectangle((size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4),
                       fill=(180, 130, 70))
        draw.rectangle((0, 0, min(10, size[0] - 1), min(10, size[1] - 1)),
                       fill=(marker % 256, marker // 256, 11))
        return picture

    @staticmethod
    def _marker(path):
        with Image.open(path) as picture:
            red, green, _ = picture.convert('RGB').getpixel((0, 0))
        return red + green * 256

    def _prepare(self, engine, portrait, config, scene, *args):
        shutil.copyfile(self.original, config['output'])
        return {'face_found': True}

    def _native(self, engine, project, text, image, index, work, duration,
                *, backend=None, scene_dir=None, canvas=None):
        folder = Path(scene_dir) if scene_dir is not None else self.scene
        folder.mkdir(parents=True, exist_ok=True)
        size = tuple(canvas) if canvas is not None else (800, 1120)
        count = 1 + 4 * math.ceil(max(0, duration * 16 - 1) / 4)
        number = len(self.calls)
        reference_marker = self._marker(image)
        self.calls.append({'folder': folder, 'canvas': size, 'count': count,
                           'reference_marker': reference_marker, 'duration': duration})
        frames = folder / 'frames'
        frames.mkdir(exist_ok=True)
        markers = []
        for frame in range(count):
            marker = reference_marker if frame == 0 else 1000 + number * 100 + frame
            self._picture(size, marker).save(frames / ('frame_%05d.png' % frame))
            markers.append(marker)
        self.generated.append(markers)
        (folder / 'video_settings.json').write_text(json.dumps({
            'width': size[0], 'height': size[1], 'fps': 16, 'frames': count,
            'quality': project.ai_quality, 'decoder': 'VAEDecodeTiled'}), encoding='utf-8')
        (folder / 'video.mp4').write_bytes(b'native clip placeholder; merger uses PNGs')
        if hasattr(engine, 'report_scene_progress'):
            for progress in (0, 20, 60, 100):
                engine.report_scene_progress(progress)
        return folder / 'video.mp4'

    def _smooth(self, engine, root, frames, fps, scene, *, force_cpu=False):
        self.interpolated.append([self._marker(frame) for frame in sorted(Path(frames).glob('frame_*.png'))])
        self.assertEqual(fps, 16)
        self.assertTrue(force_cpu)
        output = Path(scene) / 'completed.mp4'
        output.write_bytes(b'completed interpolation placeholder')
        return output

    def _patches(self, native=None):
        stack = ExitStack()
        stack.enter_context(patch.object(realistic_video, 'engine_files', return_value=self.folder))
        stack.enter_context(patch.object(realistic_video, '_worker', side_effect=self._prepare))
        stack.enter_context(patch.object(comfy_video, 'generate', side_effect=native or self._native))
        stack.enter_context(patch.object(realistic_video, '_release_for_interpolation', return_value=False))
        stack.enter_context(patch('motion_smooth.generate', side_effect=self._smooth))
        return stack

    def _generate(self, duration):
        return realistic_video.generate(self.engine, self.project, '', self.original, 0, self.work, duration)

    def test_ten_seconds_chains_last_accepted_frame_and_assembles_exact_timeline(self):
        with self._patches():
            result = self._generate(10)
        plan = plan_workload(800, 1120, 16, 10, self.project.ai_quality)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(len(self.interpolated), 1)
        expected = []
        for index, (part, call, markers) in enumerate(zip(plan.segments, self.calls, self.generated)):
            self.assertEqual(call['canvas'], (800, 1120))
            self.assertEqual(call['count'], part.generated_frames)
            if index:
                self.assertEqual(call['reference_marker'], expected[-1])
                self.assertEqual(call['folder'], self.scene / 'reszek' / ('resz_%03d' % (index + 1)))
            expected.extend(markers[part.trim_start:part.trim_stop])
        self.assertEqual(self.interpolated[0], expected)
        self.assertEqual(len(expected), 160)
        self.assertEqual(len(set(expected)), len(expected))
        settings = json.loads((self.scene / 'video_settings.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['frames'], 160)
        self.assertEqual(settings['fps'], 16)
        self.assertEqual(result.read_bytes(), b'completed interpolation placeholder')
        self.assertTrue(any('800×1120' in line and '5' in line for line in self.logs))

    def test_last_reference_is_accepted_endpoint_instead_of_surplus_tail(self):
        # The first split is full and the final part has a surplus frame. Verify
        # its exact trimming instead of allowing a 161-frame, 10.0625s result.
        with self._patches():
            self._generate(10)
        self.assertNotEqual(self.generated[-1][-1], self.interpolated[0][-1])
        self.assertEqual(self.interpolated[0][-1], self.generated[-1][-2])

    def test_normal_preset_uses_49_frame_cap_and_has_no_output_repetition(self):
        self.project.ai_quality = 'Normál • 832×480'
        with self._patches():
            self._generate(10)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call['count'] <= 49 for call in self.calls))
        self.assertEqual(len(self.interpolated[0]), 160)
        self.assertEqual(len(set(self.interpolated[0])), 160)

    def test_later_sampling_error_is_preserved_and_skips_interpolation(self):
        original = RuntimeError('GPU numerical failure in second part')
        def fail(*args, **kwargs):
            if self.calls:
                raise original
            return self._native(*args, **kwargs)
        with self._patches(fail), self.assertRaises(RuntimeError) as caught:
            self._generate(10)
        self.assertIs(caught.exception, original)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.interpolated, [])
        self.assertFalse((self.scene / 'completed.mp4').exists())

    def test_cancellation_does_not_start_more_parts_or_interpolation(self):
        original = app.Cancelled('cancelled between parts')
        def cancel(*args, **kwargs):
            if self.calls:
                raise original
            return self._native(*args, **kwargs)
        with self._patches(cancel), self.assertRaises(app.Cancelled) as caught:
            self._generate(10)
        self.assertIs(caught.exception, original)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.interpolated, [])

    def test_incomplete_part_never_reaches_interpolation_or_finished_export(self):
        def missing(*args, **kwargs):
            result = self._native(*args, **kwargs)
            (result.parent / 'frames/frame_00010.png').unlink()
            return result
        with self._patches(missing), self.assertRaises((ValueError, RuntimeError, OSError)):
            self._generate(10)
        self.assertEqual(self.interpolated, [])
        self.assertFalse((self.scene / 'completed.mp4').exists())

    def test_progress_never_moves_back_when_a_new_part_starts(self):
        with self._patches():
            self._generate(10)
        self.assertGreater(len(self.progress), 3)
        self.assertEqual(self.progress, sorted(self.progress))
        self.assertGreaterEqual(min(self.progress), 0)
        self.assertLessEqual(max(self.progress), 100)


if __name__ == '__main__':
    unittest.main()
