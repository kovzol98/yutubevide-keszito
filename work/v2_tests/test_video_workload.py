"""Bound sampling cost while preserving one contiguous generated timeline."""
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
from video_workload import MAX_SEGMENTS, plan_workload, validate_segment


class VideoWorkloadTests(unittest.TestCase):
    def test_reported_ten_second_job_is_five_fresh_bounded_parts(self):
        plan = plan_workload(800, 1120, 16, 10, 'Részletes • 1280×704')
        self.assertEqual(plan.output_frames, 160)
        self.assertEqual(plan.native_duration, 10)
        self.assertEqual(len(plan.segments), 5)
        self.assertEqual([part.output_frames for part in plan.segments], [33, 32, 32, 32, 31])
        self.assertEqual([part.generated_frames for part in plan.segments], [33] * 5)
        self.assertEqual([part.reference_frame for part in plan.segments], [None, 32, 64, 96, 128])
        self.assertLess(plan.peak_pixel_frames, 800 * 1120 * 161 / 4)
        self.assertIn('800×1120', plan.user_hint)
        self.assertIn('16 képkocka/mp', plan.user_hint)
        self.assertIn('5 egymást követő rész', plan.user_hint)
        self.assertIn('Kért hossz: 10 mp', plan.user_hint)

    def test_normal_cap_is_49_and_references_previous_accepted_frame(self):
        plan = plan_workload(480, 640, 16, 10, 'Normál • 832×480')
        self.assertEqual(len(plan.segments), 4)
        self.assertTrue(all(part.generated_frames <= 49 for part in plan.segments))
        self.assertFalse(plan.segments[0].uses_previous_frame)
        for previous, part in zip(plan.segments, plan.segments[1:]):
            self.assertEqual(part.reference_frame, previous.output_stop - 1)
            self.assertEqual(part.output_start, previous.output_stop)
            self.assertEqual(part.trim_start, 1)
            self.assertTrue(part.uses_previous_frame)

    def test_every_timeline_frame_is_written_once_without_gaps_or_duplication(self):
        for quality, size in [('Normál', (640, 480)), ('Részletes', (800, 1120))]:
            for frame_count in range(1, 500):
                with self.subTest(quality=quality, frames=frame_count):
                    plan = plan_workload(*size, 16, frame_count / 16, quality)
                    accepted = [frame for part in plan.segments
                                for frame in range(part.output_start, part.output_stop)]
                    self.assertEqual(accepted, list(range(frame_count)))
                    for part in plan.segments:
                        self.assertEqual((part.generated_frames - 1) % 4, 0)
                        self.assertLessEqual(part.trim_stop, part.generated_frames)
                        self.assertLessEqual(part.generated_frames, plan.max_generated_frames)

    def test_fractional_duration_rounds_up_by_less_than_one_native_frame(self):
        for duration in (.001, .07, 1.001, 2.01, 5.333333, 10.01, 119.99):
            with self.subTest(duration=duration):
                plan = plan_workload(640, 480, 16, duration, 'Normál')
                self.assertGreaterEqual(plan.native_duration, duration)
                self.assertLess(plan.native_duration - duration, 1 / 16)

    def test_short_and_boundary_clips_respect_four_n_plus_one(self):
        for frames in (1, 2, 4, 5, 31, 32, 33, 34, 64, 65, 66):
            with self.subTest(frames=frames):
                plan = plan_workload(800, 1120, 16, frames / 16, 'Részletes')
                self.assertEqual(plan.output_frames, frames)
                self.assertTrue(all(part.generated_frames >= 5 for part in plan.segments))

    def test_user_selected_maximum_120_seconds_is_preserved(self):
        for quality in ('Normál', 'Részletes'):
            plan = plan_workload(640, 480, 16, 120, quality)
            self.assertEqual(plan.output_frames, 1920)
            self.assertEqual(plan.native_duration, 120)
            self.assertLess(len(plan.segments), MAX_SEGMENTS)

    def test_exact_global_cap_is_accepted_and_more_is_rejected(self):
        for quality, maximum in [('Normál', 49), ('Részletes', 33)]:
            duration = (maximum + (MAX_SEGMENTS - 1) * (maximum - 1)) / 16
            plan = plan_workload(640, 480, 16, duration, quality)
            self.assertEqual(len(plan.segments), MAX_SEGMENTS)
            with self.assertRaisesRegex(ValueError, 'túl hosszú'):
                plan_workload(640, 480, 16, duration + 1 / 16, quality)

    def test_invalid_numbers_and_unexpected_types_fail_before_planning(self):
        for duration in (0, -1, math.nan, math.inf, -math.inf, True, None, '10', 1e300):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                plan_workload(640, 480, 16, duration, 'Normál')
        for fps in (0, 24, 16.0, True, '16', math.inf):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                plan_workload(640, 480, fps, 10, 'Normál')

    def test_resolution_is_never_silently_lowered_or_cropped(self):
        for width, height in ((640, 480), (480, 640), (800, 1120), (1280, 704)):
            quality = 'Normál' if width * height <= 640 * 480 else 'Részletes'
            plan = plan_workload(width, height, 16, 10, quality)
            self.assertEqual((plan.width, plan.height), (width, height))
        for size in ((0, 480), (640, -480), (641, 480), (640.0, 480), (True, 480), (1280, 704)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                plan_workload(*size, 16, 10, 'Normál')
        with self.assertRaisesRegex(ValueError, 'felbontása'):
            plan_workload(1920, 1088, 16, 10, 'Részletes')

    def test_graph_guard_rejects_original_161_frame_job(self):
        with self.assertRaisesRegex(ValueError, 'terhelési keret'):
            validate_segment(800, 1120, 161, 'Részletes')
        for frames in (0, 1, 4, 32, 34, 49, True, 33.0):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                validate_segment(800, 1120, frames, 'Részletes')
        validate_segment(800, 1120, 33, 'Részletes')
        validate_segment(640, 480, 49, 'Normál')

    def test_unknown_quality_is_rejected_and_plans_are_immutable(self):
        for quality in ('', 'Bad', None, 1, 'NormálFake'):
            with self.subTest(quality=quality), self.assertRaises(ValueError):
                plan_workload(640, 480, 16, 10, quality)
        plan = plan_workload(640, 480, 16, 10, 'Normál')
        with self.assertRaises(FrozenInstanceError):
            plan.width = 800
        with self.assertRaises(FrozenInstanceError):
            plan.segments[0].output_frames = 1


if __name__ == '__main__':
    unittest.main()
