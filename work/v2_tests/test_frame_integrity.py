"""CPU-only regression checks for corrupt or collapsed AI decoder output."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
from PIL import Image
import frame_integrity
from ytmuhely import Cancelled


class Engine:
    def __init__(self, cancel_at=None):
        self.checks = 0
        self.cancel_at = cancel_at

    def check(self):
        self.checks += 1
        if self.checks == self.cancel_at:
            raise Cancelled('Stopped during frame validation')


class FrameIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.test_root = ROOT / 'work/frame_integrity_tests'
        self.base = self.test_root / uuid.uuid4().hex
        self.folder = self.base / 'frames.pending_test'
        self.folder.mkdir(parents=True)
        self.reference = self.base / 'reference.png'
        self.report = self.base / 'frame_integrity.json'
        self.detail().save(self.reference)
        self.engine = Engine()
        self.size = (32, 48)

    def tearDown(self):
        resolved = self.base.resolve()
        assert resolved.is_relative_to(self.test_root.resolve()) and resolved != self.test_root.resolve()
        shutil.rmtree(resolved)

    def detail(self):
        image = Image.new('RGB', (32, 48), (20, 40, 60))
        image.paste((220, 180, 140), (0, 0, 16, 48))
        return image

    def sequence(self, count=5, *, color=None):
        for index in range(count):
            image = self.detail() if color is None else Image.new('RGB', self.size, color)
            image.save(self.folder / ('frame_%05d.png' % index))

    def validate(self, *, reference=True, expected_count=5, engine=None):
        return frame_integrity.validate_sequence(engine or self.engine, self.folder,
            expected_count, self.size, reference=self.reference if reference else None,
            report_path=self.report)

    def saved_report(self):
        return json.loads(self.report.read_text(encoding='utf-8'))

    def test_reported_constant_gray_failure_is_rejected_and_pending_frames_survive(self):
        self.sequence(color=(127, 127, 127))
        with self.assertRaisesRegex(frame_integrity.FrameIntegrityError, 'egyszínű képkockákat') as caught:
            self.validate()
        self.assertTrue(caught.exception.halt_queue)
        self.assertEqual(len(list(self.folder.glob('*.png'))), 5)
        report = self.saved_report()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['reason'], 'uniform_output_from_detailed_reference')
        self.assertEqual(report['flat_frames'], 5)
        self.assertEqual(report['checked_frames'], 5)
        self.assertTrue(report['reference_detailed'])
        self.assertNotIn(str(self.reference), self.report.read_text(encoding='utf-8'))

    def test_all_black_and_all_white_output_are_rejected_from_detailed_reference(self):
        for color in ('black', 'white'):
            with self.subTest(color=color):
                self.sequence(color=color)
                with self.assertRaisesRegex(RuntimeError, 'egyszínű'):
                    self.validate()

    def test_near_uniform_decoder_output_is_rejected(self):
        image = Image.new('RGB', self.size, (127, 127, 127))
        image.putpixel((0, 0), (129, 129, 129))
        for index in range(5): image.save(self.folder / ('frame_%05d.png' % index))
        with self.assertRaisesRegex(RuntimeError, 'egyszínű'):
            self.validate()

    def test_detailed_static_sequence_is_valid_without_measuring_motion(self):
        self.sequence()
        report = self.validate()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['flat_frames'], 0)
        self.assertEqual(report['checked_frames'], 5)

    def test_uniform_reference_may_have_uniform_output(self):
        Image.new('RGB', self.size, (127, 127, 127)).save(self.reference)
        self.sequence(color=(127, 127, 127))
        report = self.validate()
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['reference_detailed'])

    def test_text_only_uniform_scene_is_not_assumed_to_be_corrupt(self):
        self.sequence(color='black')
        self.assertEqual(self.validate(reference=False)['status'], 'passed')

    def test_black_fade_frames_among_detailed_frames_are_allowed(self):
        self.sequence()
        Image.new('RGB', self.size, 'black').save(self.folder / 'frame_00000.png')
        report = self.validate()
        self.assertEqual(report['flat_frames'], 1)
        self.assertEqual(report['status'], 'passed')

    def test_wrong_dimensions_in_last_frame_are_rejected(self):
        self.sequence()
        Image.new('RGB', (32, 47), 'red').save(self.folder / 'frame_00004.png')
        with self.assertRaisesRegex(RuntimeError, 'eltérő méretű'):
            self.validate()
        report = self.saved_report()
        self.assertEqual(report['reason'], 'frame_dimensions')
        self.assertEqual(report['invalid_frame'], 4)
        self.assertEqual(report['checked_frames'], 4)
        self.assertEqual(report['actual_size'], [32, 47])

    def test_malformed_last_frame_is_decoded_and_rejected(self):
        self.sequence()
        (self.folder / 'frame_00004.png').write_bytes(b'not a PNG')
        with self.assertRaisesRegex(RuntimeError, 'sérült vagy olvashatatlan'):
            self.validate()
        self.assertEqual(self.saved_report()['reason'], 'frame_decode')
        self.assertEqual(self.saved_report()['checked_frames'], 4)

    def test_truncated_png_is_rejected(self):
        self.sequence()
        final = self.folder / 'frame_00004.png'
        final.write_bytes(final.read_bytes()[:45])
        with self.assertRaisesRegex(RuntimeError, 'sérült vagy olvashatatlan'):
            self.validate()

    def test_other_image_format_renamed_png_is_rejected(self):
        self.sequence()
        self.detail().save(self.folder / 'frame_00004.png', format='JPEG')
        with self.assertRaisesRegex(RuntimeError, 'hibás formátumú'):
            self.validate()
        self.assertEqual(self.saved_report()['reason'], 'frame_format')

    def test_missing_and_excess_frames_are_rejected(self):
        self.sequence()
        for count in (4, 6):
            with self.subTest(expected_count=count):
                with self.assertRaisesRegex(RuntimeError, 'hiányosak'):
                    self.validate(expected_count=count)
                self.assertEqual(self.saved_report()['reason'], 'frame_count')

    def test_discontinuous_numbering_is_rejected_even_with_correct_count(self):
        self.sequence()
        (self.folder / 'frame_00004.png').rename(self.folder / 'frame_00005.png')
        with self.assertRaisesRegex(RuntimeError, 'számozása hibás'):
            self.validate()

    def test_unreadable_reference_is_rejected(self):
        self.sequence()
        self.reference.write_bytes(b'damaged reference')
        with self.assertRaisesRegex(RuntimeError, 'referenciafotója nem olvasható'):
            self.validate()
        self.assertEqual(self.saved_report()['reason'], 'reference_decode')

    def test_transparent_frames_are_checked_as_their_visible_output(self):
        for index in range(5):
            image = self.detail().convert('RGBA'); image.putalpha(0)
            image.save(self.folder / ('frame_%05d.png' % index))
        with self.assertRaisesRegex(RuntimeError, 'egyszínű'):
            self.validate()

    def test_cancellation_before_validation_preserves_pending_sequence(self):
        self.sequence()
        with self.assertRaises(Cancelled):
            self.validate(engine=Engine(cancel_at=1))
        self.assertEqual(self.saved_report()['status'], 'cancelled')
        self.assertEqual(self.saved_report()['checked_frames'], 0)
        self.assertEqual(len(list(self.folder.glob('*.png'))), 5)

    def test_cancellation_mid_sequence_preserves_partial_statistics(self):
        self.sequence()
        with self.assertRaises(Cancelled):
            self.validate(engine=Engine(cancel_at=5))
        self.assertEqual(self.saved_report()['status'], 'cancelled')
        self.assertEqual(self.saved_report()['checked_frames'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
