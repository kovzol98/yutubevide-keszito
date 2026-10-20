"""Lighting direction for generated scenes and restrained, fixed video grading.

Prompt additions guide the model; the small FFmpeg adjustments are applied once
when assembling the final video. They do not invent new lights or recover
clipped detail. The original style leaves both the prompt and pixels unchanged.
"""
from __future__ import annotations


DEFAULT_LIGHTING = "Lágy természetes fény"

LIGHTING_STYLES = {
    DEFAULT_LIGHTING: {
        "positive": (
            "Soft scene-motivated key light with gentle shadow fill, balanced exposure "
            "appropriate to the scene's time of day and atmosphere, visible highlight "
            "and shadow detail, natural skin tones and neutral colors, stable lighting "
            "throughout the movement. Preserve the described environment and mood."
        ),
        "negative": (
            "overexposure, blown highlights, crushed shadow detail, harsh glare, "
            "artificial glowing skin, strong color cast, flickering light, exposure pumping"
        ),
        "filter": "eq=gamma=1.045:gamma_weight=0.65:contrast=1.015:saturation=1.025:eval=init",
        "description": "Lágy fény, finoman élénkebb színek és több látható árnyékrészlet.",
    },
    "Meleg filmes fény": {
        "positive": (
            "A soft gently warm key light with subtle fill and restrained cinematic "
            "contrast, balanced exposure with detailed highlights, natural skin tones, "
            "stable lighting throughout the movement. Preserve the scene's time of "
            "day, environment and intended mood; keep the warmth subtle."
        ),
        "negative": (
            "overexposure, blown highlights, crushed shadows, orange skin, heavy orange "
            "color cast, excessive bloom, harsh glare, flickering light, exposure pumping"
        ),
        "filter": (
            "eq=gamma=1.035:gamma_r=1.015:gamma_b=0.99:gamma_weight=0.65:"
            "contrast=1.015:saturation=1.02:eval=init"
        ),
        "description": "Enyhén melegebb tónusok és visszafogott filmes kontraszt.",
    },
    "Világos stúdiófény": {
        "positive": (
            "Clean soft studio-style key and fill lighting, an evenly readable subject "
            "with gentle dimensional shadows, controlled highlights, clear natural "
            "colors and skin tones, stable exposure throughout the movement. Preserve "
            "the described environment and time of day; avoid washing out the scene."
        ),
        "negative": (
            "overexposure, blown highlights, washed-out colors, flat white haze, harsh "
            "glare, strong color cast, glowing skin, flickering light, exposure pumping"
        ),
        "filter": "eq=gamma=1.075:gamma_weight=0.55:contrast=1.005:saturation=1.015:eval=init",
        "description": "Világosabb középtónusok és tiszta, egyenletes megvilágítás.",
    },
    "Eredeti világítás": {
        "positive": "",
        "negative": "",
        "filter": "",
        "description": "A jelenetleírás és az eredeti kép megvilágítását követi, színkorrekció nélkül.",
    },
}


def validate_lighting(style: str | None) -> str:
    """Resolve a missing setting for older projects; reject unknown selections."""
    if style is None or style == "":
        return DEFAULT_LIGHTING
    if style not in LIGHTING_STYLES:
        raise ValueError("Válassz érvényes világítási stílust.")
    return style


def _append(base: str, addition: str) -> str:
    if not addition:
        return base
    cleaned = base.rstrip()
    if not cleaned:
        return addition
    separator = " " if cleaned.endswith((".", "!", "?", ";", ":")) else ". "
    return cleaned + separator + addition


def lighting_prompt(style: str | None, base_prompt: str = "") -> str:
    """Append English lighting direction without replacing the scene or motion."""
    return _append(base_prompt, LIGHTING_STYLES[validate_lighting(style)]["positive"])


def lighting_negative(style: str | None, base_negative: str = "") -> str:
    """Append lighting faults to the model's existing negative prompt."""
    addition = LIGHTING_STYLES[validate_lighting(style)]["negative"]
    if not addition:
        return base_negative
    return ", ".join(part for part in (base_negative.rstrip(" ,"), addition) if part)


def lighting_filter(style: str | None) -> str:
    """Return a fixed FFmpeg chain, or an empty chain for original lighting."""
    return LIGHTING_STYLES[validate_lighting(style)]["filter"]
