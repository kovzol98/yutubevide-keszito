import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v2_source'))
import cv2,numpy as np
from portrait_detail import DetailPreserver,clean_source

class DetailTests(unittest.TestCase):
    def fixture(self):
        rng=np.random.default_rng(17)
        source=rng.integers(20,230,(128,128,3),dtype=np.uint8)
        neutral=cv2.GaussianBlur(source,(0,0),1.2)
        return source,neutral
    def test_neutral_pose_keeps_original_pixels_exactly(self):
        source,neutral=self.fixture(); keeper=DetailPreserver(source,neutral)
        result=keeper.render_original(source,neutral,np.eye(3,dtype=np.float32),np.ones((128,128,1),np.float32))
        self.assertTrue(np.array_equal(source,result))
    def test_backward_flow_moves_detail_in_correct_direction(self):
        source,neutral=self.fixture()
        transform=np.array([[1,0,4],[0,1,0]],np.float32)
        animated=cv2.warpAffine(neutral,transform,(128,128),borderMode=cv2.BORDER_REFLECT_101)
        expected=cv2.warpAffine(source,transform,(128,128),borderMode=cv2.BORDER_REFLECT_101)
        keeper=DetailPreserver(source,neutral)
        result=keeper.render_original(source,animated,np.eye(3,dtype=np.float32),np.ones((128,128,1),np.float32))
        area=(slice(16,-16),slice(16,-16))
        error=np.abs(result[area].astype(float)-expected[area]).mean()
        self.assertLess(error,8)
        self.assertLess(error,np.abs(animated[area].astype(float)-expected[area]).mean()/2)
    def test_pixels_outside_head_mask_are_protected(self):
        source,neutral=self.fixture()
        animated=np.roll(neutral,2,axis=1)
        mask=np.zeros((128,128,1),np.float32);mask[30:95,30:95]=1
        result=DetailPreserver(source,neutral).render_original(source,animated,np.eye(3,dtype=np.float32),mask)
        self.assertTrue(np.array_equal(source[mask[...,0]==0],result[mask[...,0]==0]))
    def test_cleanup_keeps_constant_skin_color(self):
        source=np.full((64,64,3),(160,110,80),np.uint8)
        error=np.abs(clean_source(source).astype(float)-source).mean()
        self.assertLess(error,2)

if __name__=='__main__':unittest.main()
