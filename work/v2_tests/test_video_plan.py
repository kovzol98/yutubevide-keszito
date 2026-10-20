"""User-facing plans must match the selected real input and rendering route."""
import sys
import unittest
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
from video_plan import describe_video_plan
from reference_framing import FULL_FRAME,SUBJECT_FRAME


class VideoPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / 'work/v2_tests/video_plan_fixtures' / ('run_' + uuid.uuid4().hex)
        cls.folder.mkdir(parents=True)
        cls.portrait = cls.folder / 'álló fotó.jpg'
        cls.landscape = cls.folder / 'landscape.png'
        cls.oriented = cls.folder / 'orientation.jpg'
        cls.tall = cls.folder / 'very_tall.png'
        cls.large = cls.folder / 'large_portrait.jpg'
        Image.new('RGB', (1200, 1600), '#8c765a').save(cls.portrait)
        Image.new('RGB', (1600, 900), '#b4a085').save(cls.landscape)
        orientation = Image.Exif()
        orientation[274] = 6
        Image.new('RGB', (1600, 1200), '#8c765a').save(cls.oriented, exif=orientation)
        Image.new('RGB', (100, 1600), '#8c765a').save(cls.tall)
        Image.new('RGB', (3001, 4001), '#8c765a').save(cls.large)

    def plan(self, **changes):
        parameters = dict(visual_mode='AI-videó • helyi',
            image_motion='Jelenetleírás szerinti mozgás',
            image_engine='Élethű • arc és fény', ai_quality='Normál • 832×480',
            images=[str(self.portrait)], video_format='1080p • 16:9')
        parameters.update(changes)
        return describe_video_plan(**parameters)

    def test_native_normal_and_1080p_export_are_distinct(self):
        plan = self.plan()
        self.assertEqual(plan.pipeline, 'quality-image')
        self.assertEqual(plan.native_size, (480, 640))
        self.assertEqual(plan.native_fps, 16)
        self.assertEqual(plan.export_size, (1920, 1080))
        self.assertIn('480×640', plan.generation_hint)
        self.assertIn('30 képkocka/mp', plan.generation_hint)
        self.assertIn('1920×1080', plan.export_hint)
        self.assertIn('nem ad új AI-részletet', plan.export_hint)
        self.assertIn('több tíz perc', plan.time_hint)

    def test_detailed_uses_real_photo_aspect_and_warns_about_resources(self):
        plan = self.plan(ai_quality='Részletes • 1280×704')
        self.assertEqual(plan.native_size, (800, 1088))
        self.assertIn('Részletes', plan.time_hint)
        self.assertIn('memóriát', plan.time_hint)

    def test_first_photo_and_reordering_change_the_native_canvas(self):
        first = self.plan(images=[str(self.portrait), str(self.landscape)])
        reordered = self.plan(images=[str(self.landscape), str(self.portrait)])
        self.assertEqual(first.native_size, (480, 640))
        self.assertGreater(reordered.native_size[0], reordered.native_size[1])
        self.assertIn('első fotóra', reordered.generation_hint)

    def test_exif_orientation_is_respected(self):
        self.assertEqual(self.plan(images=[str(self.oriented)]).native_size, (480, 640))

    def test_extreme_photo_aspect_retains_the_tall_canvas_within_pixel_budget(self):
        plan = self.plan(images=[str(self.tall)])
        width, height = plan.native_size
        self.assertGreater(height, width * 8)
        self.assertLessEqual(width * height, 640 * 480)
        self.assertEqual((width % 32, height % 32), (0, 0))

    def test_no_photo_uses_legacy_even_if_quality_or_portrait_was_selected(self):
        plan = self.plan(images=[], image_motion='Portré • finom fejmozgás')
        self.assertEqual(plan.pipeline, 'legacy-text')
        self.assertEqual((plan.native_size, plan.native_fps), ((832, 480), 24))
        self.assertNotIn('Mozgássimítás', plan.generation_hint)
        self.assertIn('korábbi motor', plan.generation_hint)

    def test_shorts_swaps_text_generation_canvas_but_preserves_photo_aspect(self):
        normal = self.plan(images=[], video_format='Shorts • 9:16')
        detailed = self.plan(images=[], video_format='Shorts • 9:16',
                             ai_quality='Részletes • 1280×704')
        self.assertEqual(normal.native_size, (480, 832))
        self.assertEqual(detailed.native_size, (704, 1280))
        self.assertEqual(self.plan(video_format='Shorts • 9:16').native_size, (480, 640))
        photo = self.plan(images=[str(self.landscape)], video_format='Shorts • 9:16')
        self.assertGreater(photo.native_size[0], photo.native_size[1])

    def test_legacy_photo_uses_legacy_budget_and_fps(self):
        plan = self.plan(image_engine='Alap • korábbi motor')
        self.assertEqual(plan.pipeline, 'legacy-image')
        self.assertEqual(plan.native_fps, 24)
        self.assertNotEqual(plan.native_size, (480, 640))

    def test_portrait_mode_overrides_quality_engine_and_quality_preset(self):
        plan = self.plan(image_motion='Portré • finom fejmozgás',
                         ai_quality='Részletes • 1280×704')
        self.assertEqual(plan.pipeline, 'portrait')
        self.assertEqual((plan.native_size, plan.native_fps), ((1200, 1600), 30))
        self.assertIn('nem hat', plan.time_hint)
        self.assertNotIn('több tíz perc', plan.time_hint)

    def test_source_aspect_export_uses_full_photo_canvas(self):
        plan = self.plan(video_format='Fotó eredeti aránya')
        self.assertEqual(plan.export_size, (1200, 1600))
        self.assertEqual(plan.native_size, (480, 640))

    def test_portrait_canvas_matches_worker_scaling_limit(self):
        plan = self.plan(images=[str(self.large)], image_motion='Portré • finom fejmozgás')
        self.assertEqual(plan.native_size, (1440, 1920))

    def test_own_video_does_not_claim_ai_generation(self):
        plan = self.plan(visual_mode='Saját videók')
        self.assertEqual(plan.pipeline, 'own-video')
        self.assertIsNone(plan.native_size)
        self.assertIsNone(plan.native_fps)
        self.assertIn('nem érvényes', plan.generation_hint)
        unsupported = self.plan(visual_mode='Saját videók', video_format='Fotó eredeti aránya')
        self.assertIsNone(unsupported.export_size)
        self.assertIn('fix exportméretet', unsupported.export_hint)

    def test_slideshow_uses_export_canvas_without_promising_ai_body_motion(self):
        plan = self.plan(visual_mode='Képes diavideó', video_format='Shorts • 9:16')
        self.assertEqual(plan.pipeline, 'slideshow')
        self.assertEqual((plan.native_size, plan.native_fps), ((1080, 1920), 30))
        self.assertIn('nem generálja', plan.time_hint)

    def test_missing_photo_does_not_show_a_fabricated_native_size(self):
        plan = self.plan(images=[str(self.folder / 'nem létezik.jpg')])
        self.assertIsNone(plan.native_size)
        self.assertIn('nem olvasható', plan.generation_hint)

    def test_subject_framing_waits_for_face_detection_without_promising_a_canvas(self):
        plan=self.plan(images=[str(self.landscape)],framing=SUBJECT_FRAME,
                       ai_quality='Részletes • 1280×704')
        self.assertEqual(plan.pipeline,'quality-image')
        self.assertIsNone(plan.native_size)
        self.assertEqual(plan.native_fps,16)
        self.assertEqual(plan.export_size,(1920,1080))
        self.assertIn('3:4',plan.generation_hint)
        self.assertIn('oldalsó',plan.generation_hint)
        self.assertIn('teljes fotómagasság',plan.generation_hint)
        self.assertIn('Arc nélkül a teljes fotót',plan.generation_hint)
        self.assertIn('arcfelismerés után',plan.generation_hint)
        self.assertNotIn('800×1088',plan.generation_hint)

    def test_subject_framing_source_aspect_export_waits_for_the_real_crop(self):
        plan=self.plan(images=[str(self.landscape)],framing=SUBJECT_FRAME,
                       video_format='Fotó eredeti aránya')
        self.assertIsNone(plan.native_size)
        self.assertIsNone(plan.export_size)
        self.assertIn('arcfelismerés utáni kivágás',plan.export_hint)
        self.assertNotIn('olvasható fotó kell',plan.export_hint)

    def test_subject_framing_keeps_known_dimensions_for_an_already_portrait_photo(self):
        plan=self.plan(framing=SUBJECT_FRAME,video_format='Fotó eredeti aránya')
        self.assertEqual(plan.native_size,(480,640))
        self.assertEqual(plan.export_size,(1200,1600))
        self.assertIn('nem vág az oldalából',plan.generation_hint)
        self.assertEqual(self.plan(images=[str(self.oriented)],framing=SUBJECT_FRAME).native_size,(480,640))

    def test_full_framing_default_preserves_existing_landscape_dimensions(self):
        implicit=self.plan(images=[str(self.landscape)],video_format='Fotó eredeti aránya')
        explicit=self.plan(images=[str(self.landscape)],video_format='Fotó eredeti aránya',framing=FULL_FRAME)
        self.assertEqual(implicit,explicit)
        self.assertEqual(explicit.export_size,(1600,900))
        self.assertGreater(explicit.native_size[0],explicit.native_size[1])

    def test_subject_crop_is_only_planned_for_quality_image_generation(self):
        for changes in (dict(visual_mode='Képes diavideó'),dict(visual_mode='Saját videók'),
                        dict(image_motion='Portré • finom fejmozgás'),dict(image_engine='Alap • korábbi motor'),
                        dict(images=[])):
            parameters=dict(images=[str(self.landscape)])
            parameters.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(self.plan(**parameters,framing=SUBJECT_FRAME),self.plan(**parameters))

    def test_detailed_time_hint_sets_realistic_expectations_without_suggesting_normal_first(self):
        plan=self.plan(ai_quality='Részletes • 1280×704')
        self.assertIn('több tíz perc',plan.time_hint)
        self.assertIn('2 mp',plan.time_hint)
        self.assertNotIn('először Normál',plan.time_hint)

    def test_unknown_framing_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Ismeretlen fotókivágás'):
            self.plan(framing='unknown')


if __name__ == '__main__':
    unittest.main()
