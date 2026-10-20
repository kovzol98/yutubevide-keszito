"""Standalone, local portrait animation using the official LivePortrait core.

The crop detector is MediaPipe Tasks on CPU.  Only bounded head pose is driven;
source appearance, canonical keypoints and expression stay fixed.  This worker
does not import the upstream Cropper or load driving pickle templates.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def status(event: str, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def read_source(path: Path, max_dimension: int = 1920):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        original_size = image.size
        if max(image.size) > max_dimension:
            ratio = max_dimension / max(image.size)
            image = image.resize(
                (max(2, round(image.width * ratio)), max(2, round(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        rgb = np.array(image)
    # yuv420p requires even dimensions; crop at most one far-edge pixel.
    rgb = np.ascontiguousarray(rgb[: rgb.shape[0] // 2 * 2, : rgb.shape[1] // 2 * 2])
    if min(rgb.shape[:2]) < 64:
        raise ValueError("A kép túl kicsi a portré animálásához.")
    return rgb, original_size


def detect_face(rgb, detector_model: Path):
    import cv2
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python as tasks
    from mediapipe.tasks.python import vision

    if not detector_model.is_file():
        raise FileNotFoundError(f"Hiányzik az arcfelismerő modell: {detector_model}")
    h, w = rgb.shape[:2]
    # Detection needs only a small preview. Coordinates are mapped back exactly.
    ratio = min(1.0, 1024.0 / max(h, w))
    preview = cv2.resize(rgb, (round(w * ratio), round(h * ratio))) if ratio < 1 else rgb
    options = vision.FaceDetectorOptions(
        base_options=tasks.BaseOptions(
            model_asset_path=str(detector_model), delegate=tasks.BaseOptions.Delegate.CPU
        ),
        running_mode=vision.RunningMode.IMAGE,
        min_detection_confidence=0.55,
    )
    with vision.FaceDetector.create_from_options(options) as detector:
        result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=preview))
    if not result.detections:
        raise ValueError("Nem találtam jól látható arcot. Válassz egy szemből készült portrét.")
    # Select the largest face, not a small person accidentally visible behind it.
    face = max(result.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
    b = face.bounding_box
    sx, sy = w / preview.shape[1], h / preview.shape[0]
    box = np.array([b.origin_x * sx, b.origin_y * sy, b.width * sx, b.height * sy], dtype=np.float32)
    if min(box[2:]) < 72:
        raise ValueError("Az arc túl kicsi ezen a képen. Használj közelebbi portrét.")
    landmarks = np.array([[p.x * w, p.y * h] for p in face.keypoints], dtype=np.float32)
    if len(landmarks) < 2:
        raise ValueError("Az arc szemvonalát nem sikerült felismerni.")
    # BlazeFace's first two landmarks are the eye centres (also behind glasses).
    eyes = landmarks[:2][np.argsort(landmarks[:2, 0])]
    eye_axis = eyes[1] - eyes[0]
    angle = math.atan2(float(eye_axis[1]), float(eye_axis[0]))
    confidence = float(face.categories[0].score) if face.categories else None
    return box, landmarks, angle, confidence


def make_crop(rgb, box, angle: float, crop_scale: float = 1.7):
    import cv2
    import numpy as np
    from src.utils.crop import crop_image_by_bbox

    x, y, width, height = [float(v) for v in box]
    side = max(width, height) * crop_scale
    cx, cy = x + width / 2, y + height / 2 - height * 0.08
    bounds = [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2]
    crop = crop_image_by_bbox(
        rgb, bounds, dsize=512, angle=angle, flag_rot=True, borderMode=cv2.BORDER_REFLECT_101
    )
    # Restrict pasteback to the head; the original shirt and background are not
    # regenerated. A compact, smooth feather makes the head join the source.
    mask = np.zeros(rgb.shape[:2], dtype=np.float32)
    cv2.ellipse(
        mask,
        (round(cx), round(y + height * 0.42)),
        (round(width * 0.60), round(height * 0.76)),
        math.degrees(angle), 0, 360, 1.0, -1, cv2.LINE_AA,
    )
    feather = max(2.0, min(width, height) * 0.025)
    mask = cv2.GaussianBlur(mask, (0, 0), feather)
    mask[mask < 1 / 255] = 0
    crop["mask_original"] = mask[..., None]
    crop["crop_bounds"] = bounds
    return crop


@contextlib.contextmanager
def safe_checkpoint_loading(torch):
    """Force upstream checkpoint reads to the restricted state-dict loader."""
    load = torch.load

    def weights_only_load(*args, **kwargs):
        kwargs["weights_only"] = True
        return load(*args, **kwargs)

    torch.load = weights_only_load
    try:
        yield
    finally:
        torch.load = load


def load_wrapper(vendor: Path, models: Path, force_cpu: bool, half_precision: bool = False):
    import numpy as np
    import torch
    from src.config.inference_config import InferenceConfig
    from src.live_portrait_wrapper import LivePortraitWrapper

    if (models / "liveportrait" / "base_models").is_dir():
        models = models / "liveportrait"
    base = models / "base_models"
    paths = {
        "checkpoint_F": base / "appearance_feature_extractor.pth",
        "checkpoint_M": base / "motion_extractor.pth",
        "checkpoint_W": base / "warping_module.pth",
        "checkpoint_G": base / "spade_generator.pth",
        "checkpoint_S": models / "retargeting_models" / "stitching_retargeting_module.pth",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"Hiányzik a portrémodell ({name}): {path}")
    use_cpu = force_cpu or not torch.cuda.is_available()
    # Autocast is explicitly selected; CPU inference stays in float32.
    cfg = InferenceConfig(
        models_config=str(vendor / "src" / "config" / "models.yaml"),
        **{name: str(path) for name, path in paths.items()},
        flag_force_cpu=use_cpu,
        flag_use_half_precision=half_precision and not use_cpu,
        flag_do_torch_compile=False,
        flag_normalize_lip=False,
        flag_eye_retargeting=False,
        flag_lip_retargeting=False,
        flag_stitching=True,
        flag_relative_motion=True,
        animation_region="pose",
        lip_array=np.zeros((1, 21, 3), dtype=np.float32),
        mask_crop=np.zeros((512, 512, 3), dtype=np.uint8),
    )
    with safe_checkpoint_loading(torch):
        wrapper = LivePortraitWrapper(cfg)
    return wrapper


def pose_offsets(t: float, strength: float = 1.0):
    """Non-looped, slow, bounded pose curve, always beginning at the source pose."""
    ease = min(1.0, max(0.0, t / 1.2))
    ease = ease * ease * (3 - 2 * ease)
    yaw = strength * ease * (1.5 * math.sin(2 * math.pi * t / 8.5) + 0.45 * math.sin(2 * math.pi * t / 13.7))
    pitch = strength * ease * (0.7 * math.sin(2 * math.pi * t / 6.8) + 0.25 * math.sin(2 * math.pi * t / 11.2))
    roll = strength * ease * 0.35 * math.sin(2 * math.pi * t / 9.6)
    breath = strength * ease * 0.0008 * math.sin(2 * math.pi * t / 4.8)
    return pitch, yaw, roll, breath


def encode_frames(ffmpeg: Path, output: Path, width: int, height: int, fps: int, log_path: Path, crf: int = 17):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
             "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
             "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
            creationflags=flags,
        )
    except Exception:
        log.close()
        raise
    return process, log


def animate(config: dict):
    started = time.monotonic()
    vendor = Path(config["vendor"]).resolve()
    models = Path(config["models"]).resolve()
    image = Path(config["image"]).resolve()
    output = Path(config["output"]).resolve()
    ffmpeg = Path(config["ffmpeg"]).resolve()
    duration = float(config.get("duration", 5.0))
    fps = int(config.get("fps", 24))
    strength = float(config.get("strength", 1.0))
    composite = config.get("composite_mode", "model")
    if not math.isfinite(duration) or not 0.5 <= duration <= 600:
        raise ValueError("A portré jelenethossza 0,5 és 600 másodperc között lehet.")
    if not 1 <= fps <= 60 or not 0 <= strength <= 1:
        raise ValueError("Érvénytelen képkockaszám vagy mozgáserősség.")
    if composite not in ("model", "residual", "source_detail"):
        raise ValueError("Ismeretlen portréillesztési mód.")
    for path in (vendor / "src" / "live_portrait_wrapper.py", ffmpeg, image):
        if not path.is_file():
            raise FileNotFoundError(str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep AMD's writable caches beside this engine, including on a managed PC.
    cache = Path(config.get('cache', str(models.parent / 'cache'))).resolve()
    for name, relative in (('MIOPEN_USER_DB_PATH', 'miopen-db'),
                           ('MIOPEN_CUSTOM_CACHE_DIR', 'miopen-kernels'),
                           ('TORCHINDUCTOR_CACHE_DIR', 'torch'), ('TEMP', 'temp'), ('TMP', 'temp')):
        location = cache / relative; location.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(location)
    sys.path.insert(0, str(vendor))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # Torch import follows the runtime configuration selected by the installer.
    import cv2
    import numpy as np
    import torch
    from src.utils.camera import get_rotation_matrix
    from src.utils.crop import paste_back

    cv2.setNumThreads(max(1, min(8, os.cpu_count() or 1)))
    rgb, original_size = read_source(image, int(config.get("max_dimension", 1920)))
    if config.get('clean_source',False):
        from portrait_detail import clean_source
        status('cleaning'); rgb=clean_source(rgb)
    detector_model = Path(config.get("face_detector", str(models / "blazeface_short_range.tflite"))).resolve()
    status("detecting")
    box, landmarks, angle, confidence = detect_face(rgb, detector_model)
    crop = make_crop(rgb, box, angle, float(config.get("crop_scale", 1.7)))
    status("loading", face_box=box.tolist())
    wrapper = load_wrapper(vendor, models, bool(config.get("force_cpu", False)), bool(config.get('half_precision', False)))
    prepared = wrapper.prepare_source(crop["img_crop"])
    with torch.inference_mode():
        info = wrapper.get_kp_info(prepared)
        feature = wrapper.extract_feature_3d(prepared)
        source_kp = wrapper.transform_keypoint(info)
        baseline_driving = wrapper.stitching(source_kp, source_kp.clone())
        neutral = wrapper.parse_output(wrapper.warp_decode(feature, source_kp, baseline_driving)["out"])[0]
    detail=None
    if composite=='source_detail':
        from portrait_detail import DetailPreserver
        detail=DetailPreserver(crop['img_crop'],neutral)

    height, width = rgb.shape[:2]
    nframes = max(1, round(duration * fps))
    manifest_path = Path(config.get("manifest", str(output.with_suffix(".json"))))
    mask = crop["mask_original"]
    if config.get("save_debug", False):
        cv2.imwrite(str(output.with_name(output.stem + "_source_crop.png")), cv2.cvtColor(crop["img_crop"], cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(output.with_name(output.stem + "_neutral.png")), cv2.cvtColor(neutral, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(output.with_name(output.stem + "_mask.png")), (mask[..., 0] * 255).astype(np.uint8))
    process, encoder_log = encode_frames(ffmpeg, output, width, height, fps, output.with_suffix(".ffmpeg.log"),int(config.get('intermediate_crf',17)))
    movement_max = 0.0
    first_frame = None
    final_frame = None
    try:
        with torch.inference_mode():
            for frame_idx in range(nframes):
                t = frame_idx / fps
                pitch, yaw, roll, breath = pose_offsets(t, strength)
                rotation = get_rotation_matrix(info["pitch"] + pitch, info["yaw"] + yaw, info["roll"] + roll)
                translation = info["t"].clone()
                translation[..., 2] = 0
                translation[..., 1] += breath
                driving = info["scale"][..., None] * (info["kp"] @ rotation + info["exp"]) + translation[:, None, :]
                driving = wrapper.stitching(source_kp, driving)
                result = wrapper.warp_decode(feature, source_kp, driving)
                generated = wrapper.parse_output(result["out"])[0]
                if composite == "residual":
                    # Preserve source reconstruction at rest, while the learned
                    # model supplies the motion. Use only for very subtle poses.
                    generated = np.clip(crop["img_crop"].astype(np.float32) + generated.astype(np.float32) - neutral.astype(np.float32), 0, 255).astype(np.uint8)
                if detail is not None:
                    frame=detail.render_original(rgb,generated,crop['M_c2o'],mask)
                else:
                    frame = paste_back(generated, crop["M_c2o"], rgb, mask)
                # Protect unchanged pixels from float rounding in upstream blend.
                frame[mask[..., 0] == 0] = rgb[mask[..., 0] == 0]
                process.stdin.write(np.ascontiguousarray(frame).tobytes())
                if frame_idx == 0:
                    first_frame = frame.copy()
                final_frame = frame
                movement_max = max(movement_max, abs(yaw), abs(pitch), abs(roll))
                if frame_idx % max(1, fps) == 0 or frame_idx == nframes - 1:
                    status("progress", frame=frame_idx + 1, total=nframes, seconds=round(time.monotonic() - started, 2))
        process.stdin.close()
        if process.wait(timeout=120) != 0:
            raise RuntimeError(f"A videó mentése sikertelen. Részletek: {output.with_suffix('.ffmpeg.log')}")
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        raise
    finally:
        encoder_log.close()
    if config.get("save_debug", False):
        for suffix, frame in (("first", first_frame), ("last", final_frame)):
            cv2.imwrite(str(output.with_name(output.stem + "_" + suffix + ".png")), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    manifest = {
        "engine": "LivePortrait", "motion": "bounded_pose_only", "composite_mode": composite,
        "image": str(image), "output": str(output), "face_box": box.tolist(),
        "landmarks": landmarks.tolist(), "face_confidence": confidence,
        "source_size": list(original_size), "output_size": [width, height],
        "crop_bounds": crop["crop_bounds"], "nframes": nframes, "fps": fps,
        "duration": nframes / fps, "device": wrapper.device,
        "torch_version": torch.__version__, "hip_version": torch.version.hip,
        "half_precision": wrapper.inference_cfg.flag_use_half_precision,
        "source_cleaned": bool(config.get('clean_source',False)),
        "maximum_detail_displacement_pixels": detail.maximum_displacement if detail else None,
        "maximum_pose_offset_degrees": movement_max,
        "preserves_outside_mask": True, "elapsed_seconds": time.monotonic() - started,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    status("complete", **manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", nargs="?")
    parser.add_argument("--config")
    args = parser.parse_args()
    path = args.config or args.config_path
    if not path:
        parser.error("add meg a JSON beállításfájlt")
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    try:
        animate(config)
    except Exception as exc:
        status("error", message=str(exc))
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
