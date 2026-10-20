"""Prevent expensive photo jobs whose selected motion has no actual action."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_source"))
from image_motion import (DANCE_MOTION, DEFAULT_IMAGE_MOTION, IMAGE_MOTION_STYLES,
                          MOTION_EXAMPLES, SCENE_MOTION, image_motion_prompt,
                          resolved_motion, validate_motion_request)


class MotionRequestTests(unittest.TestCase):
    def test_description_mode_rejects_all_empty_photo_actions(self):
        for prompt in (None, "", "  \t\r\n", "\u00a0"):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(ValueError, "konkrét mozdulat") as caught:
                    validate_motion_request(SCENE_MOTION, prompt)
                for label in MOTION_EXAMPLES:
                    self.assertIn(label, str(caught.exception))

    def test_written_photo_action_and_editable_examples_are_accepted(self):
        for prompt in ("The cat raises its paw.", *MOTION_EXAMPLES.values()):
            with self.subTest(prompt=prompt):
                self.assertIsNone(validate_motion_request(SCENE_MOTION, prompt))
                self.assertTrue(image_motion_prompt(SCENE_MOTION, prompt).startswith(prompt))

    def test_other_explicit_modes_keep_their_deliberate_fallbacks(self):
        for style in IMAGE_MOTION_STYLES:
            if style == SCENE_MOTION:
                continue
            with self.subTest(style=style):
                self.assertIsNone(validate_motion_request(style, " \n"))
                self.assertIn(IMAGE_MOTION_STYLES[style]["fallback"], image_motion_prompt(style))
        self.assertIsNone(validate_motion_request(None, ""))
        self.assertIsNone(validate_motion_request("", ""))

    def test_no_photo_can_use_existing_title_or_narration_scene(self):
        for prompt in (None, "", "  \n"):
            self.assertIsNone(validate_motion_request(SCENE_MOTION, prompt, has_images=False))

    def test_invalid_motion_mode_still_reports_validation_error(self):
        with self.assertRaisesRegex(ValueError, "érvényes képmozgatási"):
            validate_motion_request("unknown", "The person waves.")

    def test_prompt_builder_remains_backward_compatible_for_empty_scene(self):
        self.assertTrue(image_motion_prompt(SCENE_MOTION).startswith(
            IMAGE_MOTION_STYLES[SCENE_MOTION]["fallback"]))

    def test_dance_example_resolves_to_whole_body_and_negation_stays_portrait(self):
        for style in (DEFAULT_IMAGE_MOTION, SCENE_MOTION):
            self.assertEqual(resolved_motion(style, MOTION_EXAMPLES["Tánc"]), DANCE_MOTION)
        self.assertEqual(resolved_motion(DEFAULT_IMAGE_MOTION, "No dancing; a tiny head tilt."),
                         DEFAULT_IMAGE_MOTION)

    def test_examples_describe_actions_and_arm_example_prevents_unrequested_approach(self):
        self.assertEqual(set(MOTION_EXAMPLES), {"Kar- és testmozgás", "Séta", "Tánc"})
        body = MOTION_EXAMPLES["Kar- és testmozgás"].lower()
        for action in ("shoulders", "lowers", "lifts", "head follows", "feet stay planted", "size stay unchanged"):
            self.assertIn(action, body)
        walk = MOTION_EXAMPLES["Séta"].lower()
        for action in ("two", "steps", "arm", "swing"):
            self.assertIn(action, walk)
        dance = MOTION_EXAMPLES["Tánc"].lower()
        for action in ("whole-body dance", "knees", "arms"):
            self.assertIn(action, dance)
        for label, text in MOTION_EXAMPLES.items():
            with self.subTest(example=label):
                self.assertLess(len(text.split()), 80)
                for unwanted in ("new outfit", "sunset", "beach", "new background"):
                    self.assertNotIn(unwanted, text.lower())


if __name__ == "__main__":
    unittest.main()
