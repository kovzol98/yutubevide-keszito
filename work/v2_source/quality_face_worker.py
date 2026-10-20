"""Prepare a source photo once; never paste a separate head onto video."""
from __future__ import annotations
import json,math,os,sys,time,traceback
from pathlib import Path
# The embedded Python runtime deliberately omits the script directory from
# sys.path. Resolve this small local helper before loading any model packages.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from reference_framing import FULL_FRAME,framing_box,validate_framing

def status(event,**values):
    print(json.dumps(dict(event=event,**values),ensure_ascii=False),flush=True)

def initialize(config):
    runtime=Path(config['runtime']);bundle=Path(config['bundle'])
    # ROCm and plotting caches must be configured before importing Torch.
    for key,sub in [('MIOPEN_USER_DB_PATH','miopen-db'),('MIOPEN_CUSTOM_CACHE_DIR','miopen-kernels'),
                    ('MPLCONFIGDIR','matplotlib'),('TEMP','temp'),('TMP','temp')]:
        target=runtime/'cache'/sub;target.mkdir(parents=True,exist_ok=True);os.environ[key]=str(target)
    sys.dont_write_bytecode=True
    sys.path[:0]=[str(bundle),str(runtime/'code')]
    import cv2,torch
    torch.set_num_threads(4);cv2.setNumThreads(2)
    return runtime

def detector_for(runtime):
    from mediapipe.tasks import python as tasks
    from mediapipe.tasks.python import vision
    options=vision.FaceDetectorOptions(base_options=tasks.BaseOptions(
        model_asset_path=str(runtime/'models/blaze_face_short_range.tflite'),
        delegate=tasks.BaseOptions.Delegate.CPU),running_mode=vision.RunningMode.IMAGE,
        min_detection_confidence=.4)
    return vision.FaceDetector.create_from_options(options)

def find_face(detector,rgb,expected=None):
    import cv2,mediapipe as mp,numpy as np
    scale=min(2.,1280/max(rgb.shape[:2]))
    preview=cv2.resize(rgb,None,fx=scale,fy=scale)
    faces=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=preview)).detections
    candidates=[]
    for face in faces:
        b=face.bounding_box
        box=np.array([b.origin_x/scale,b.origin_y/scale,b.width/scale,b.height/scale],np.float32)
        confidence=float(face.categories[0].score)
        if min(box[2:])<12:continue
        if expected is not None:
            distance=float(np.linalg.norm((box[:2]+box[2:]/2)-(expected[:2]+expected[2:]/2)))
            size=float(max(expected[2:]))
            if distance>max(size*2.,rgb.shape[0]*.08):continue
            if not .4<box[2]/expected[2]<2.5:continue
            score=confidence-distance/max(size,1)*.15
        else:score=confidence
        points=np.array([[p.x*rgb.shape[1],p.y*rgb.shape[0]] for p in face.keypoints],np.float32)
        if len(points)<2:continue
        eyes=points[:2][np.argsort(points[:2,0])];axis=eyes[1]-eyes[0]
        if np.linalg.norm(axis)<3:continue
        candidates.append((score,box,math.atan2(float(axis[1]),float(axis[0])),eyes))
    if not candidates:return None
    _,box,angle,eyes=max(candidates,key=lambda value:value[0])
    return box,angle,eyes

def restore(rgb,eyes,runtime):
    import cv2,numpy as np,torch
    from face_restore_vendor.gfpganv1_clean_arch import GFPGANv1Clean
    axis=eyes[1]-eyes[0];length=float(np.linalg.norm(axis));c,s=axis/length;scale=128/length
    matrix=np.array([[scale*c,scale*s,0],[-scale*s,scale*c,0]],np.float32)
    matrix[:,2]=np.array([192,240],np.float32)-matrix[:,:2]@eyes[0]
    aligned=cv2.warpAffine(rgb,matrix,(512,512),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
    model=GFPGANv1Clean(out_size=512,num_style_feat=512,channel_multiplier=2,decoder_load_path=None,
        fix_decoder=False,num_mlp=8,input_is_latent=True,different_w=True,narrow=1,sft_half=True).eval()
    weights=torch.load(runtime/'models/GFPGANv1.4.pth',map_location='cpu',weights_only=True)
    model.load_state_dict(weights['params_ema'],strict=True);del weights
    with torch.inference_mode():
        tensor=torch.from_numpy(aligned.transpose(2,0,1).copy()).float().unsqueeze(0)/127.5-1
        output=model(tensor,return_rgb=False,randomize_noise=False)[0][0].clamp(-1,1)
        repaired=((output.permute(1,2,0).numpy()+1)*127.5).clip(0,255)
    mask=np.zeros((512,512),np.float32);cv2.ellipse(mask,(256,278),(158,211),0,0,360,1,-1)
    mask=cv2.GaussianBlur(mask,(0,0),12);inside=mask>.8
    repaired+=(aligned[inside].mean(0)-repaired[inside].mean(0))
    repaired=.25*aligned+.75*repaired.clip(0,255)
    inverse=cv2.invertAffineTransform(matrix);height,width=rgb.shape[:2]
    layer=cv2.warpAffine(repaired,inverse,(width,height),flags=cv2.INTER_CUBIC)
    alpha=cv2.warpAffine(mask,inverse,(width,height),flags=cv2.INTER_LINEAR)[...,None]
    return np.clip(alpha*layer+(1-alpha)*rgb,0,255).astype(np.uint8)

def save_image(path,rgb):
    from PIL import Image
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.stem+'.partial.png')
    Image.fromarray(rgb).save(temporary);temporary.replace(path)

def prepare(config,runtime):
    from liveportrait_worker import read_source
    framing=validate_framing(config.get('framing',FULL_FRAME))
    rgb,original_size=read_source(Path(config['source']))
    with detector_for(runtime) as detector:face=find_face(detector,rgb)
    height,width=rgb.shape[:2]
    box=framing_box(width,height,None if face is None else face[0],framing)
    left,top,right,bottom=box
    cropped=box!=(0,0,width,height)
    original_face=None if face is None else face[0].copy()
    if cropped:
        rgb=rgb[top:bottom,left:right].copy()
        if face is not None:
            face_box,angle,eyes=face
            face_box=face_box.copy();face_box[:2]-=(left,top)
            eyes=eyes.copy();eyes-=(left,top)
            face=face_box,angle,eyes
    reason=('full_frame' if framing==FULL_FRAME else 'no_face' if face is None
            else 'subject_crop' if cropped else 'already_portrait')
    crop=dict(style=framing,applied=cropped,box=list(box),coordinates_size=[width,height],
              original_box=[round(left*original_size[0]/width),round(top*original_size[1]/height),
                            round(right*original_size[0]/width),round(bottom*original_size[1]/height)],
              reason=reason)
    repaired=bool(face is not None and config.get('enhanced',True))
    if repaired:rgb=restore(rgb,face[2],runtime)
    save_image(config['output'],rgb)
    return dict(face_found=face is not None,source_size=original_size,prepared_size=[rgb.shape[1],rgb.shape[0]],
                source_crop=crop,source_face_box=None if original_face is None else original_face.tolist(),
                prepared_face_box=None if face is None else face[0].tolist(),
                prepared_eyes=None if face is None else face[2].tolist(),
                restored_once=repaired,restoration_blend=.75 if repaired else 0,randomized_noise=False,
                source=str(config['source']),output=str(config['output']))

def main():
    config=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if config['action']!='prepare':raise ValueError('A külön fejráillesztés megszűnt. A teljes jelenet közös mozgását használd.')
    validate_framing(config.get('framing',FULL_FRAME))
    runtime=initialize(config)
    result=prepare(config,runtime)
    manifest=Path(config['manifest']);temporary=manifest.with_suffix('.partial.json')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(manifest)
    status('complete',**result)

if __name__=='__main__':
    try:main()
    except Exception:
        traceback.print_exc();sys.exit(1)
