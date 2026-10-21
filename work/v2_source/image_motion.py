"""Restrained image-to-video motion and reference preparation without cropping."""
from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageOps


DEFAULT_IMAGE_MOTION = "Portré • finom fejmozgás"
SCENE_MOTION = "Jelenetleírás szerinti mozgás"
DANCE_MOTION = "Teljes alak • tánc"
MOTION_EXAMPLES = {
    "Kar- és testmozgás": (
        "The person stays in the same spot, turns their shoulders slightly, "
        "and slowly lowers then lifts one arm. Their feet stay planted, and "
        "their distance from the camera and apparent size stay unchanged. "
        "Keep the same clothing and accessories, constant framing and exposure. "
        "The head follows the shoulders in one continuous movement."
    ),
    "Séta": (
        "The person takes two slow, natural steps forward, with a gentle arm "
        "swing on each step, in one continuous movement."
    ),
    "Tánc": (
        "The person performs a gentle whole-body dance in place: the knees "
        "bend, the weight shifts side to side, and both arms swing naturally "
        "with the movement."
    ),
}
IMAGE_MOTION_LABELS = {
    DANCE_MOTION: "Teljes alak • tánc (AI-jelenet)",
    "Portré • finom fejmozgás": "Portré • finom fejmozgás (gyorsabb)",
    "Finom természetes mozgás": "Általános jelenet (AI-jelenet)",
    "Jelenetleírás szerinti mozgás": "Leírás szerinti mozgás (AI-jelenet)",
}
IMAGE_MOTION_STYLES = {
    DANCE_MOTION: {
        "description": "Az egész alak táncol: kar-, csípő-, térd- és lábmozgással.",
        "fallback": (
            "The person dances rhythmically in place. Knees bend, hips sway, "
            "feet step side to side and both arms swing with the beat."
        ),
        "guidance": (
            "Keep the same person, outfit, background and light. Fixed camera; "
            "the entire body and both feet stay visible throughout the dance."
        ),
        "negative": (
            "static body, frozen legs, head-only movement, still pose, slideshow, "
            "extra limbs, fused legs, malformed hands, missing feet, sliding feet, "
            "identity change, face morphing, camera shake, zoom, cuts, oversaturation, overexposure"
        ),
    },
    "Finom természetes mozgás": {
        "description": "Apró, természetes mozdulatok, az eredeti kép megjelenésének megtartásával.",
        "fallback": "The original subject makes subtle natural movements.",
        "guidance": (
            "Keep the original subject's appearance and identity, existing accessories "
            "and clothing, background, framing, colors and lighting. Fixed camera; "
            "one continuous shot with restrained motion."
        ),
        "negative": (
            "morphing, identity change, altered face, removed accessories, warped anatomy, "
            "excessive motion, camera shake, zoom, cuts, changing background, relighting, "
            "oversaturation, overexposure, flicker"
        ),
    },
    "Portré • finom fejmozgás": {
        "description": "Finom fejbillentés és légzés; az arc, a szemüveg és a képkivágás megmarad.",
        "fallback": "A natural, relaxed portrait.",
        "guidance": (
            "Preserve the same face, hairstyle, sunglasses, clothing, background, "
            "framing, colors and light. Fixed camera; gentle breathing and a tiny "
            "head tilt only. Lips stay closed; no speaking or walking."
        ),
        "negative": (
            "morphing, identity change, altered facial features, warped face, missing glasses, "
            "sunglasses changing shape, removed accessories, talking, lip sync, open mouth, "
            "walking, exaggerated gestures, warped anatomy, camera shake, zoom, cuts, "
            "changing background, relighting, oversaturation, overexposure, flicker"
        ),
    },
    "Jelenetleírás szerinti mozgás": {
        "description": "A megadott angol mozgásleírást követi, megtartva a kép szereplőit és környezetét.",
        "fallback": "Animate the original scene with subtle natural motion.",
        "guidance": (
            "Keep the original subject's appearance and identity, existing accessories "
            "and clothing, background, framing, colors and lighting. Fixed camera; "
            "one continuous shot with natural motion."
        ),
        "negative": (
            "morphing, identity change, altered face, removed accessories, warped anatomy, "
            "camera shake, cuts, changing background, relighting, oversaturation, "
            "overexposure, flicker"
        ),
    },
}


def validate_image_motion(style: str | None) -> str:
    if style is None or style == "":
        return DEFAULT_IMAGE_MOTION
    if style not in IMAGE_MOTION_STYLES:
        raise ValueError("Válassz érvényes képmozgatási módot.")
    return style


def validate_motion_request(style: str | None, prompt: str | None, has_images: bool = True) -> None:
    """Require an explicit action before an expensive description-led photo job.

    Other motion modes have deliberate built-in actions. Text-to-video can
    still obtain its scene from the title or narration in the calling engine.
    """
    selected = validate_image_motion(style)
    if has_images and selected == SCENE_MOTION and not (prompt or "").strip():
        raise ValueError(
            "Írd le angolul, mit mozogjon a képen szereplő ember vagy tárgy, "
            "vagy válassz egy mozgásmintát: Kar- és testmozgás, Séta vagy Tánc. "
            "A leírás szerinti mozgatáshoz konkrét mozdulat szükséges."
        )


def image_motion_prompt(style: str | None, user_prompt: str = "") -> str:
    """Use only the supplied motion description; never derive an action from speech.

    Fallback directions stay short. Explicit action descriptions are preserved
    in full, with the preservation instructions appended afterwards.
    """
    settings = IMAGE_MOTION_STYLES[validate_image_motion(style)]
    if style==DANCE_MOTION and user_prompt.strip():
        from dance_moves import has_dance_preservation
        if has_dance_preservation(user_prompt):
            # A complete chosen choreography already includes identity, group,
            # lighting and framing. Generic solo dance would conflict with it.
            return ' '.join(user_prompt.split())
    guidance = settings["guidance"]
    action = " ".join((user_prompt or "").split()) or settings["fallback"]
    if style==DANCE_MOTION and user_prompt.strip():
        action += '. '+settings['fallback']
    if not action.endswith((".", "!", "?")):
        action += "."
    return action + " " + guidance

def resolved_motion(style: str, prompt: str) -> str:
    """An explicit dance request must never silently become head-only motion."""
    text=(prompt or '').casefold()
    excluded=re.search(r'\b(?:no|not|without|never)\s+(?:\w+\s+){0,2}danc(?:e|es|ing)\b|\b(?:ne|nem)\s+tánc\w*',text)
    wants_dance=re.search(r'\bdanc(?:e|es|ing)\b|\btánc\w*',text)
    if style in IMAGE_MOTION_STYLES and wants_dance and not excluded:
        return DANCE_MOTION
    return style


def image_motion_negative(style: str | None, base_negative: str = "") -> str:
    addition = IMAGE_MOTION_STYLES[validate_image_motion(style)]["negative"]
    return ", ".join(part for part in ((base_negative or "").strip(" ,"), addition) if part)


def _contained_size(source_width: int, source_height: int, width: int, height: int) -> tuple[int, int]:
    scale = min(width / source_width, height / source_height)
    return min(width, max(1, round(source_width * scale))), min(height, max(1, round(source_height * scale)))


def source_dimensions(image_path, budget_width: int, budget_height: int) -> tuple[int, int]:
    """Choose a source-shaped, 32-aligned canvas within the preset's pixel budget.

    The preset bounds represent an area budget rather than forced landscape
    dimensions. Only candidates requiring at most 32 pixels of edge padding on
    either axis are accepted; the photograph itself is never cropped.
    """
    area = int(budget_width) * int(budget_height)
    if int(budget_width) < 32 or int(budget_height) < 32:
        raise ValueError("Az AI-jelenet mérete legalább 32 × 32 képpont legyen.")
    with Image.open(image_path) as original:
        oriented = ImageOps.exif_transpose(original)
        source_width, source_height = oriented.size
    ratio = source_width / source_height
    candidates = []
    # For each aligned width, examine the nearest heights to the source ratio.
    # The budget-limited height covers very tall photographs as well.
    for width in range(32, area // 32 + 1, 32):
        maximum_height = area // width // 32 * 32
        ideal_height = width / ratio
        heights = {int(math.floor(ideal_height / 32)) * 32,
                   int(math.ceil(ideal_height / 32)) * 32, maximum_height}
        for height in heights:
            if height < 32 or height > maximum_height:
                continue
            resized_width, resized_height = _contained_size(source_width, source_height, width, height)
            if width - resized_width > 32 or height - resized_height > 32:
                continue
            aspect_error = abs(math.log((width / height) / ratio))
            candidates.append((width * height, -aspect_error, width, height))
    if not candidates:
        raise ValueError("A kép aránya túl szélsőséges ehhez a részletességhez. Válassz nagyobb AI-részletességet.")
    _, _, width, height = max(candidates)
    return width, height


def prepare_reference(image_path, destination, width: int, height: int) -> dict:
    """Save the complete oriented photograph with small repeated-edge padding.

    No exposure, color or identity adjustments are applied. The returned data
    can be included in the caller's scene metadata.
    """
    width, height = int(width), int(height)
    if width < 32 or height < 32 or width % 32 or height % 32:
        raise ValueError("A referencia mérete 32 képpont többszöröse legyen.")
    with Image.open(image_path) as original:
        raw_size = original.size
        orientation = original.getexif().get(274, 1)
        source = ImageOps.exif_transpose(original).convert("RGB")
    resized_width, resized_height = _contained_size(*source.size, width, height)
    pad_x, pad_y = width - resized_width, height - resized_height
    if pad_x > 32 or pad_y > 32:
        raise ValueError("A referencia képaránya nem megfelelő: túl nagy szegély szükséges. A teljes kép megtartásához más méret kell.")
    resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    left, top = pad_x // 2, pad_y // 2
    right, bottom = pad_x - left, pad_y - top
    canvas = Image.new("RGB", (width, height))
    canvas.paste(resized, (left, top))
    # Repeat edge pixels instead of introducing black bands or invented scenery.
    if left:
        canvas.paste(resized.crop((0, 0, 1, resized_height)).resize((left, resized_height), Image.Resampling.NEAREST), (0, top))
    if right:
        canvas.paste(resized.crop((resized_width - 1, 0, resized_width, resized_height)).resize((right, resized_height), Image.Resampling.NEAREST), (left + resized_width, top))
    if top:
        canvas.paste(canvas.crop((0, top, width, top + 1)).resize((width, top), Image.Resampling.NEAREST), (0, 0))
    if bottom:
        canvas.paste(canvas.crop((0, top + resized_height - 1, width, top + resized_height)).resize((width, bottom), Image.Resampling.NEAREST), (0, top + resized_height))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return {
        "original_size": list(raw_size),
        "oriented_size": list(source.size),
        "reference_size": [width, height],
        "resized_size": [resized_width, resized_height],
        "padding": {"left": left, "top": top, "right": right, "bottom": bottom},
        "crop": 0,
        "exif_transposed": orientation not in (None, 1),
    }
