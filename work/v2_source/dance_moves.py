"""Small local dance presets for coherent image-to-video motion directions.

These are motion descriptions, not motion-capture clips or an identity guarantee.
The existing image-to-video engine still performs the actual animation.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


# Keep insertion order stable: it is also the order of the Hungarian selector.
_MOVES = (
    (
        "Oldalazó ringás",
        "side_sway",
        "Shift weight slowly from left to right with both feet planted, soft knees "
        "and relaxed arms swaying alongside the torso.",
        "Lassú testsúlyáthelyezés jobbra-balra, laza karokkal; a lábak a helyükön maradnak.",
    ),
    (
        "Oldallépés zárással",
        "step_touch",
        "Take one small step to the left and bring the other foot beside it, then "
        "repeat to the right, with a relaxed arm swing and clear foot placement.",
        "Kis oldallépés, mellézárás, majd ugyanez a másik irányba, természetes karlendítéssel.",
    ),
    (
        "Csípőringás",
        "hip_sway",
        "Sway the hips in a small smooth arc from left to right, with feet planted, "
        "soft knees and relaxed hands near the waist.",
        "Finom csípőringás enyhén hajlított térddel; a kezek a derék közelében maradnak.",
    ),
    (
        "Diszkó karmozdulat",
        "disco_arm",
        "Raise one arm diagonally upward to a comfortable height and lower it, "
        "then repeat with the other arm while gently shifting weight in place.",
        "Váltott, átlós karemelés kényelmes magasságig, apró testsúlyáthelyezéssel.",
    ),
    (
        "Finom twist",
        "gentle_twist",
        "Make small alternating heel pivots while the hips and shoulders turn "
        "slightly together, with elbows bent and both feet close to the floor.",
        "Kis sarokfordítások, együtt forduló vállal és csípővel, hajlított könyökkel.",
    ),
    (
        "Tapsolós tánc",
        "clap_dance",
        "Clap both hands gently once at chest height, separate them naturally, "
        "then repeat while softly bending the knees and shifting weight in place.",
        "Laza taps mellmagasságban, finom térdhajlítással és helyben ringással.",
    ),
    (
        "Egyszerű shuffle",
        "simple_shuffle",
        "Alternate short forward-and-back foot shuffles in place, lifting each "
        "foot just above the floor and placing it clearly, with a small arm swing.",
        "Rövid, váltott előre-hátra láblépések helyben, kis karlendítéssel.",
    ),
    (
        "Vidám rugózás",
        "cheerful_bounce",
        "Make gentle rhythmic knee bends with a small buoyant rise of the torso "
        "and relaxed bent arms, keeping the feet grounded without jumping.",
        "Játékos, ruganyos térdhajlítás; a törzs és a karok együtt követik a mozgást.",
    ),
)

DANCE_MOVES: Mapping[str, Mapping[str, str]] = MappingProxyType({
    label: MappingProxyType({"id": move_id, "action": action, "description": description})
    for label, move_id, action, description in _MOVES
})
DANCE_INTENSITIES = ("Finom", "Élénk")
DEFAULT_DANCE = next(iter(DANCE_MOVES))

_INTENSITY = MappingProxyType({
    "Finom": "Use small, slow movements and comfortable joints; no jumps or fast spins.",
    "Élénk": "Use a lively steady rhythm, medium-sized movements and comfortable joints; no jumps or fast spins.",
})

_PRESERVATION = (
    "Keep the head, neck, shoulders and torso moving as one connected body "
    "with natural joints and stable proportions. "
    "Preserve each original identity, face, hairstyle, clothing, glasses and other accessories. "
    "When the full body and feet are visible, keep them visible throughout. "
    "If the photograph crops the body or feet, preserve that crop; "
    "animate only visible joints without inventing unseen body parts. "
    "Keep the original style, background, colors and lighting constant. "
    "Fixed camera, one continuous shot; no cuts or zoom."
)


def dance_prompt(label: str, group: bool = False, intensity: str = "Finom") -> str:
    """Return a self-contained English action from a validated local preset.

    The caller stores this text in the existing video_prompt field. Narration,
    titles and external services are deliberately absent from this interface.
    """
    if not isinstance(label, str) or label not in DANCE_MOVES:
        raise ValueError("Válassz érvényes táncmozdulatot.")
    if type(group) is not bool:
        raise ValueError("A csoportos tánc beállítása igaz vagy hamis lehet.")
    if not isinstance(intensity, str) or intensity not in _INTENSITY:
        raise ValueError("Válassz érvényes táncerősséget: Finom vagy Élénk.")

    if group:
        subject = (
            "All visible characters dance together with the same timing in their "
            "original positions. Keep the original number of characters separate; "
            "do not merge or duplicate them."
        )
    else:
        subject = (
            "The main visible character dances in place. Keep every other visible "
            "character in their original position without adding or removing anyone."
        )
    return " ".join((
        subject, "Dance pattern:", DANCE_MOVES[label]["action"],
        _INTENSITY[intensity], _PRESERVATION,
    ))


def is_dance_prompt(text: object) -> bool:
    """Recognize complete presets so a generic dance action need not be appended.

    Matching the complete generated text avoids treating a custom description
    that happens to contain a familiar move name as a catalog selection.
    """
    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split())
    return normalized in _KNOWN_PROMPTS


def has_dance_preservation(text: object) -> bool:
    """Keep a catalog choreography editable without adding a conflicting move.

    A complete preservation paragraph is sufficient for prompt routing even
    after the user changes the action or adds a note. Exact catalog recognition
    remains separate in is_dance_prompt.
    """
    if not isinstance(text, str):
        return False
    return _PRESERVATION in " ".join(text.split())


_KNOWN_PROMPTS = frozenset(
    dance_prompt(label, group, intensity)
    for label in DANCE_MOVES
    for group in (False, True)
    for intensity in DANCE_INTENSITIES
)
