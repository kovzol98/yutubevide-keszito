"""Dance presets must describe distinct, coherent moves without external input."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_source"))
from dance_moves import (DANCE_INTENSITIES, DANCE_MOVES, DEFAULT_DANCE,
                         dance_prompt, has_dance_preservation, is_dance_prompt)


class DanceMoveTests(unittest.TestCase):
    def test_catalog_has_eight_stable_distinct_actions_and_ids(self):
        self.assertEqual(len(DANCE_MOVES), 8)
        self.assertEqual(DEFAULT_DANCE, "Oldalazó ringás")
        self.assertEqual([move["id"] for move in DANCE_MOVES.values()], [
            "side_sway", "step_touch", "hip_sway", "disco_arm", "gentle_twist",
            "clap_dance", "simple_shuffle", "cheerful_bounce",
        ])
        actions = [move["action"] for move in DANCE_MOVES.values()]
        self.assertEqual(len(set(actions)), 8)
        for move in DANCE_MOVES.values():
            self.assertTrue(move["description"])
            self.assertTrue(move["action"].endswith("."))

    def test_distinct_moves_keep_their_specific_physical_action(self):
        evidence = {
            "side_sway": "both feet planted",
            "step_touch": "bring the other foot beside it",
            "hip_sway": "hips in a small smooth arc",
            "disco_arm": "Raise one arm diagonally upward",
            "gentle_twist": "alternating heel pivots",
            "clap_dance": "Clap both hands",
            "simple_shuffle": "forward-and-back foot shuffles",
            "cheerful_bounce": "rhythmic knee bends",
        }
        for label, move in DANCE_MOVES.items():
            self.assertIn(evidence[move["id"]], dance_prompt(label))

    def test_all_prompts_are_english_and_keep_connected_body_identity_and_scene(self):
        for label in DANCE_MOVES:
            for group in (False, True):
                for intensity in DANCE_INTENSITIES:
                    prompt = dance_prompt(label, group, intensity)
                    with self.subTest(label=label, group=group, intensity=intensity):
                        self.assertTrue(prompt.isascii())
                        self.assertIn("danc", prompt)
                        self.assertIn("head, neck, shoulders and torso", prompt)
                        self.assertIn("one connected body", prompt)
                        self.assertIn("original identity", prompt)
                        self.assertIn("glasses and other accessories", prompt)
                        self.assertIn("background, colors and lighting constant", prompt)
                        self.assertIn("Fixed camera, one continuous shot", prompt)
                        self.assertIn("no jumps or fast spins", prompt)
                        self.assertNotIn("overlay", prompt)
                        self.assertNotIn("paste", prompt)
                        self.assertLessEqual(len(prompt.split()), 150)
                        self.assertNotIn("\n", prompt)

    def test_cropped_photo_never_requests_invented_body_or_camera_reframing(self):
        for label in DANCE_MOVES:
            prompt = dance_prompt(label)
            self.assertIn("When the full body and feet are visible", prompt)
            self.assertIn("If the photograph crops the body or feet, preserve that crop", prompt)
            self.assertIn("animate only visible joints", prompt)
            self.assertIn("without inventing unseen body parts", prompt)
            self.assertNotIn("zoom out", prompt)

    def test_group_keeps_every_character_separate_and_on_the_same_timing(self):
        prompt = dance_prompt(DEFAULT_DANCE, group=True)
        self.assertIn("All visible characters dance together with the same timing", prompt)
        self.assertIn("in their original positions", prompt)
        self.assertIn("original number of characters", prompt)
        self.assertIn("do not merge or duplicate them", prompt)
        self.assertNotIn("The main visible character", prompt)

    def test_single_character_mode_does_not_add_remove_or_reposition_others(self):
        prompt = dance_prompt(DEFAULT_DANCE)
        self.assertIn("The main visible character dances in place", prompt)
        self.assertIn("every other visible character in their original position", prompt)
        self.assertIn("without adding or removing anyone", prompt)
        self.assertNotIn("All visible characters dance together", prompt)

    def test_intensity_changes_amplitude_without_replacing_action(self):
        for label, move in DANCE_MOVES.items():
            gentle = dance_prompt(label, intensity="Finom")
            lively = dance_prompt(label, intensity="Élénk")
            self.assertNotEqual(gentle, lively)
            self.assertIn(move["action"], gentle)
            self.assertIn(move["action"], lively)
            self.assertIn("small, slow movements", gentle)
            self.assertIn("medium-sized movements", lively)

    def test_catalog_and_nested_records_are_immutable(self):
        with self.assertRaises(TypeError):
            DANCE_MOVES["New move"] = {}
        with self.assertRaises(TypeError):
            DANCE_MOVES[DEFAULT_DANCE]["action"] = "Change the face."
        self.assertIn("Shift weight slowly", dance_prompt(DEFAULT_DANCE))

    def test_rejects_unknown_names_and_ambiguous_parameter_types(self):
        for label in ("unknown", "", None, [], 1, True):
            with self.subTest(label=label), self.assertRaises(ValueError):
                dance_prompt(label)
        for group in (0, 1, "False", "True", None, []):
            with self.subTest(group=group), self.assertRaises(ValueError):
                dance_prompt(DEFAULT_DANCE, group=group)
        for intensity in ("unknown", "", "finom", None, 1, [], True):
            with self.subTest(intensity=intensity), self.assertRaises(ValueError):
                dance_prompt(DEFAULT_DANCE, intensity=intensity)

    def test_recognizes_all_generated_presets_after_whitespace_normalization(self):
        generated = set()
        for label in DANCE_MOVES:
            for group in (False, True):
                for intensity in DANCE_INTENSITIES:
                    prompt = dance_prompt(label, group, intensity)
                    generated.add(prompt)
                    self.assertTrue(is_dance_prompt(prompt))
                    self.assertTrue(is_dance_prompt("\n " + prompt.replace(" ", "  ") + "\t"))
        self.assertEqual(len(generated), 32)

    def test_custom_descriptions_are_not_silently_classified_as_catalog_prompts(self):
        prompt = dance_prompt(DEFAULT_DANCE)
        for custom in ("dance", "Dance pattern: Shift weight slowly.", prompt + " Then walk away.",
                       prompt.replace("glasses", "hat"), "", None, [], 17):
            with self.subTest(custom=custom):
                self.assertFalse(is_dance_prompt(custom))

    def test_preservation_remains_recognizable_after_an_action_edit_or_a_note(self):
        for label, move in DANCE_MOVES.items():
            for group in (False, True):
                for intensity in DANCE_INTENSITIES:
                    selected = dance_prompt(label, group, intensity)
                    edited = selected.replace(move['action'],
                        'Keep both feet planted and trace a slow circle with one hand.')
                    appended = selected + ' Repeat this movement twice at the same speed.'
                    for text in (selected, edited, appended, 'Begin the scene. ' + appended):
                        with self.subTest(label=label, group=group, intensity=intensity, text=text):
                            self.assertTrue(has_dance_preservation(text))
                            self.assertTrue(has_dance_preservation('\n' + text.replace(' ', '\t  ') + '\n'))
                    self.assertFalse(is_dance_prompt(edited))
                    self.assertFalse(is_dance_prompt(appended))

    def test_preservation_detection_requires_the_entire_unchanged_paragraph(self):
        selected = dance_prompt(DEFAULT_DANCE)
        for text in (selected.replace('original identity', 'new identity'),
                     selected.replace('preserve that crop;', ''),
                     selected.removesuffix('no cuts or zoom.'),
                     'Dance in place. Fixed camera, one continuous shot; no cuts or zoom.',
                     '', None, [], 17):
            with self.subTest(text=text):
                self.assertFalse(has_dance_preservation(text))


if __name__ == "__main__":
    unittest.main()
