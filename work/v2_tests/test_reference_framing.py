"""Geometry and pre-model validation for optional source-photo framing."""
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'work/v2_source'))
from reference_framing import FULL_FRAME,SUBJECT_FRAME,FRAMING_STYLES,framing_box,validate_framing
import quality_face_worker as worker


class ReferenceFramingTests(unittest.TestCase):
    def test_full_photo_remains_the_default_even_with_a_face(self):
        self.assertEqual(framing_box(1920,1440,(1650,180,80,100)),(0,0,1920,1440))
        self.assertEqual(validate_framing(FULL_FRAME),FULL_FRAME)
        self.assertIn(SUBJECT_FRAME,FRAMING_STYLES)

    def test_landscape_person_on_right_keeps_entire_height(self):
        self.assertEqual(framing_box(1920,1440,(1650,180,80,100),SUBJECT_FRAME),
                         (840,0,1920,1440))

    def test_landscape_person_on_left_clamps_to_left_edge(self):
        self.assertEqual(framing_box(1920,1440,(40,180,80,100),SUBJECT_FRAME),
                         (0,0,1080,1440))

    def test_face_in_centre_is_centred_in_three_to_four_frame(self):
        box=framing_box(1920,1080,(900,200,120,140),SUBJECT_FRAME)
        self.assertEqual(box,(555,0,1365,1080))
        self.assertEqual((box[0]+box[2])/2,960)

    def test_no_face_preserves_full_landscape(self):
        self.assertEqual(framing_box(1920,1080,None,SUBJECT_FRAME),(0,0,1920,1080))

    def test_portrait_and_square_keep_all_visible_height(self):
        self.assertEqual(framing_box(900,1600,(100,100,100,100),SUBJECT_FRAME),(0,0,900,1600))
        self.assertEqual(framing_box(900,1200,(100,100,100,100),SUBJECT_FRAME),(0,0,900,1200))
        self.assertEqual(framing_box(1200,1200,(550,100,100,100),SUBJECT_FRAME),(150,0,1050,1200))

    def test_invalid_face_detections_never_create_an_arbitrary_crop(self):
        for face in ((),[3],['bad',10,20,20],(math.nan,10,20,20),(math.inf,10,20,20),
                     (10,10,0,20),(10,10,-5,20),(2000,10,20,20),(-100,10,20,20),
                     (10,1100,20,20),(10,-100,20,20)):
            with self.subTest(face=face):
                self.assertEqual(framing_box(1920,1080,face,SUBJECT_FRAME),(0,0,1920,1080))

    def test_partly_visible_face_still_anchors_nearest_image_edge(self):
        self.assertEqual(framing_box(1920,1080,(-10,10,30,30),SUBJECT_FRAME),(0,0,810,1080))

    def test_dimensions_are_positive_integers(self):
        for width,height in ((0,10),(10,0),(-1,10),(10.2,20),(True,20),(10,False)):
            with self.subTest(size=(width,height)):
                with self.assertRaises(ValueError):framing_box(width,height,None)

    def test_unknown_framing_is_rejected_before_loading_models(self):
        with patch.object(sys,'argv',['worker','settings.json']),patch.object(Path,'read_text',
                return_value='{"action":"prepare","framing":"unknown"}'),\
                patch.object(worker,'initialize') as initialize:
            with self.assertRaisesRegex(ValueError,'Ismeretlen fotókivágás'):worker.main()
            initialize.assert_not_called()


if __name__=='__main__':unittest.main()
