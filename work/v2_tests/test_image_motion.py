"""Image references must retain all edges and honor the source aspect ratio."""
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_source"))
from image_motion import (DEFAULT_IMAGE_MOTION, DANCE_MOTION, SCENE_MOTION, IMAGE_MOTION_STYLES, resolved_motion,
                          image_motion_prompt, prepare_reference,
                          source_dimensions, validate_image_motion)


class ReferencePreparationTests(unittest.TestCase):
    def setUp(self):
        # Keep fixture artifacts in the workspace. TemporaryDirectory creates
        # mode-0700 Windows folders which this managed runtime cannot reopen.
        self.root = Path(__file__).resolve().parent / "image_motion_fixtures" / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=True)

    def fixture(self, size=(208, 460)):
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        width, height = size
        draw.rectangle((0, 0, width - 1, 15), fill="red")
        draw.rectangle((0, height - 16, width - 1, height - 1), fill="blue")
        draw.rectangle((0, 16, 15, height - 17), fill="green")
        draw.rectangle((width - 16, 16, width - 1, height - 17), fill="yellow")
        path = self.root / "fixture.png"
        image.save(path)
        return path

    def test_portrait_dimensions_retain_source_aspect_within_budget(self):
        path = self.fixture((2080, 4608))
        dimensions = source_dimensions(path, 832, 480)
        self.assertEqual(dimensions, (416, 928))
        self.assertLessEqual(dimensions[0] * dimensions[1], 832 * 480)
        self.assertTrue(all(value % 32 == 0 for value in dimensions))

    def test_all_edges_survive_without_black_padding_or_crop(self):
        path = self.fixture()
        width, height = source_dimensions(path, 832, 480)
        destination = self.root / "prepared.png"
        metadata = prepare_reference(path, destination, width, height)
        self.assertEqual(metadata["crop"], 0)
        self.assertLessEqual(metadata["padding"]["top"] + metadata["padding"]["bottom"], 32)
        with Image.open(destination) as image:
            self.assertEqual(image.size, (width, height))
            self.assertEqual(image.getpixel((width // 2, 0)), (255, 0, 0))
            self.assertEqual(image.getpixel((width // 2, height - 1)), (0, 0, 255))
            self.assertEqual(image.getpixel((0, height // 2)), (0, 128, 0))
            self.assertEqual(image.getpixel((width - 1, height // 2)), (255, 255, 0))

    def test_exif_orientation_is_applied_before_aspect_choice(self):
        image = Image.new("RGB", (120, 80), "white")
        exif = image.getexif()
        exif[274] = 6
        path = self.root / "rotated.jpg"
        image.save(path, exif=exif)
        width, height = source_dimensions(path, 832, 480)
        metadata = prepare_reference(path, self.root / "rotated_reference.png", width, height)
        self.assertGreater(height, width)
        self.assertEqual(metadata["original_size"], [120, 80])
        self.assertEqual(metadata["oriented_size"], [80, 120])
        self.assertTrue(metadata["exif_transposed"])

    def test_large_forced_padding_is_rejected_instead_of_cropping(self):
        with self.assertRaises(ValueError):
            prepare_reference(self.fixture(), self.root / "bad.png", 832, 480)

    def test_motion_directions_are_short_and_preserve_identity(self):
        for style in IMAGE_MOTION_STYLES:
            prompt = image_motion_prompt(style)
            self.assertLess(len(prompt.split()), 65)
            self.assertIn("background", prompt)
            self.assertIn("Fixed camera", prompt)
        portrait = image_motion_prompt("Portré • finom fejmozgás")
        self.assertIn("sunglasses", portrait)
        self.assertIn("no speaking or walking", portrait)
        self.assertEqual(validate_image_motion(None), DEFAULT_IMAGE_MOTION)
        with self.assertRaises(ValueError):
            validate_image_motion("unknown")

    def test_explicit_motion_text_is_retained_in_full(self):
        action = "natural " * 100 + "The final instruction must remain intact."
        for style in IMAGE_MOTION_STYLES:
            prompt = image_motion_prompt(style, action)
            self.assertTrue(prompt.startswith(action))
            self.assertIn("The final instruction must remain intact.", prompt)
            self.assertIn("Fixed camera", prompt)

    def test_dance_requests_cannot_silently_use_head_only_engine(self):
        for style in (DEFAULT_IMAGE_MOTION, SCENE_MOTION, 'Finom természetes mozgás'):
            for prompt in ('dance', 'The man is dancing.', 'He dances in place.', 'Az egész alak táncoljon.'):
                self.assertEqual(resolved_motion(style, prompt), DANCE_MOTION)
        for prompt in ('no dancing', 'The person must not dance.', 'ne táncoljon', 'tiny head tilt'):
            self.assertEqual(resolved_motion(DEFAULT_IMAGE_MOTION, prompt), DEFAULT_IMAGE_MOTION)
        prompt=image_motion_prompt(DANCE_MOTION,'dance')
        for part in ('Knees bend','feet step','both arms swing','both feet stay visible'):
            self.assertIn(part,prompt)


if __name__ == "__main__":
    unittest.main()
