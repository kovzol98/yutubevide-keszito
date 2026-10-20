"""Keep the original background outside a person's complete motion envelope."""
from __future__ import annotations

def motion_envelope(reference,frames,model):
    import cv2,numpy as np,mediapipe as mp
    from mediapipe.tasks import python as tasks
    from mediapipe.tasks.python import vision
    union=np.zeros(reference.shape[:2],np.float32)
    options=vision.ImageSegmenterOptions(base_options=tasks.BaseOptions(
        model_asset_path=str(model),delegate=tasks.BaseOptions.Delegate.CPU),
        output_confidence_masks=True,output_category_mask=False)
    with vision.ImageSegmenter.create_from_options(options) as segmenter:
        for rgb in _with_reference(reference,frames):
            result=segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb))
            mask=result.confidence_masks[0].numpy_view().squeeze()
            if mask.shape!=union.shape:raise ValueError('Eltérő méretű háttérmaszk.')
            np.maximum(union,mask,out=union)
    # One fixed mask over the whole shot avoids breathing/flickering boundaries.
    # It includes both the original person and every generated body position;
    # therefore the original standing person can never reappear in a gap.
    return feather_envelope(union>.12)

def feather_envelope(foreground):
    import cv2,numpy as np
    distance=cv2.distanceTransform((~foreground.astype(bool)).astype(np.uint8),cv2.DIST_L2,3)
    margin=max(3,min(foreground.shape)/60);feather=max(4,min(foreground.shape)/40)
    t=np.clip((distance-margin)/feather,0,1)
    return 1-t*t*(3-2*t)

def _with_reference(reference,frames):
    yield reference
    yield from frames

def composite(frame,reference,envelope):
    import numpy as np
    weight=envelope[...,None]
    return np.clip(weight*frame+(1-weight)*reference,0,255).astype(np.uint8)
