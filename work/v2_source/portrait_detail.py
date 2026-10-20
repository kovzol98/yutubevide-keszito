"""Transfer subtle neural portrait motion to the original photo's pixels."""
from __future__ import annotations

class DetailPreserver:
    def __init__(self, original_crop, neutral_render):
        import cv2
        import numpy as np
        self.source=original_crop.copy()
        self.neutral=cv2.cvtColor(neutral_render,cv2.COLOR_RGB2GRAY)
        height,width=self.neutral.shape
        self.x,self.y=np.meshgrid(np.arange(width,dtype=np.float32),np.arange(height,dtype=np.float32))
        self.flow=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.flow.setUseSpatialPropagation(True)
        self.maximum_displacement=0.0
        self.original_grid=None

    def displacement(self, animated_render):
        import cv2
        import numpy as np
        gray=cv2.cvtColor(animated_render,cv2.COLOR_RGB2GRAY)
        if np.array_equal(gray,self.neutral): return np.zeros((*gray.shape,2),dtype=np.float32)
        # Backward flow locates each animated pixel in the neutral source image.
        motion=self.flow.calc(gray,self.neutral,None)
        motion=cv2.GaussianBlur(motion,(0,0),.8)
        if not np.isfinite(motion).all(): raise RuntimeError('Érvénytelen portrémozgás.')
        self.maximum_displacement=max(self.maximum_displacement,float(np.max(np.linalg.norm(motion,axis=2))))
        return motion

    def render_original(self, rgb, animated_render, crop_to_original, mask):
        import cv2
        import numpy as np
        motion=self.displacement(animated_render)
        if not np.any(motion): return rgb.copy()
        height,width=rgb.shape[:2]
        if self.original_grid is None:
            self.original_grid=np.stack(np.meshgrid(np.arange(width,dtype=np.float32),np.arange(height,dtype=np.float32)),axis=-1)
        vectors=motion@crop_to_original[:2,:2].T
        canvas=cv2.warpAffine(vectors,crop_to_original[:2],(width,height),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        coordinates=self.original_grid+canvas
        moved=cv2.remap(rgb,coordinates[...,0],coordinates[...,1],cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
        result=np.clip(mask*moved+(1-mask)*rgb,0,255).astype(np.uint8)
        result[mask[...,0]==0]=rgb[mask[...,0]==0]
        return result

def clean_source(rgb):
    """Denoise once, so noise reduction cannot flicker between video frames."""
    import cv2
    bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
    clean=cv2.fastNlMeansDenoisingColored(bgr,None,2.0,3.5,7,21)
    return cv2.cvtColor(clean,cv2.COLOR_BGR2RGB)
