"""Optional framing of a source photo, before any video generation.

Face boxes use (x, y, width, height); returned crop boxes use Pillow's
(left, top, right, bottom) convention. No image or model dependencies.
"""
from __future__ import annotations

import math

FULL_FRAME = 'Teljes fotó'
SUBJECT_FRAME = 'Alanyra közelítés'
FRAMING_STYLES = (FULL_FRAME, SUBJECT_FRAME)


def validate_framing(style):
    if style not in FRAMING_STYLES:
        raise ValueError('Ismeretlen fotókivágás. Válassz a Teljes fotó vagy Alanyra közelítés közül.')
    return style


def framing_box(width, height, face_box, style=FULL_FRAME):
    """Keep all visible body height; optionally centre a 3:4 crop on a face.

    Missing, invalid or off-image face detections preserve the full photo.
    Images already narrower than 3:4 also retain their complete frame.
    """
    validate_framing(style)
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, int) or not isinstance(height, int)
            or min(width, height) <= 0):
        raise ValueError('A fotó mérete pozitív egész szám legyen.')
    whole = (0, 0, width, height)
    if style == FULL_FRAME or width * 4 <= height * 3 or face_box is None:
        return whole
    try:
        x, y, face_width, face_height = map(float, face_box)
    except (TypeError, ValueError, OverflowError):
        return whole
    if (not all(math.isfinite(value) for value in (x, y, face_width, face_height))
            or min(face_width, face_height) <= 0
            or x >= width or y >= height or x + face_width <= 0 or y + face_height <= 0):
        return whole
    crop_width = min(width, max(1, round(height * 3 / 4)))
    centre = x + face_width / 2
    left = min(width - crop_width, max(0, round(centre - crop_width / 2)))
    return left, 0, left + crop_width, height
