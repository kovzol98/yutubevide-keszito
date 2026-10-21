"""Bound local Wan A14B sampling without shortening or looping a scene.

These are conservative application limits, not a GPU memory estimator or a
promise that a particular driver will remain stable. Each continuation must
use the last *accepted native frame* as its reference. Its first generated
frame is conditioning overlap, so it is omitted from the assembled timeline.
The caller must validate every segment before using it or its last frame.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


POLICY_VERSION = 'wan-a14b-bounded-v1'
NATIVE_FPS = 16
MAX_SEGMENTS = 128
_POLICIES = {
    'Normál': (49, 640 * 480),
    'Részletes': (33, 1280 * 704),
}


@dataclass(frozen=True)
class SegmentPlan:
    index: int
    output_start: int
    output_frames: int
    generated_frames: int
    trim_start: int
    reference_frame: int | None

    @property
    def output_stop(self):
        return self.output_start + self.output_frames

    @property
    def trim_stop(self):
        return self.trim_start + self.output_frames

    @property
    def uses_previous_frame(self):
        return self.reference_frame is not None


@dataclass(frozen=True)
class WorkloadPlan:
    width: int
    height: int
    fps: int
    duration: float
    quality: str
    output_frames: int
    max_generated_frames: int
    segments: tuple[SegmentPlan, ...]
    policy: str = POLICY_VERSION

    @property
    def native_duration(self):
        return self.output_frames / self.fps

    @property
    def generated_frames(self):
        return sum(part.generated_frames for part in self.segments)

    @property
    def peak_pixel_frames(self):
        return self.width * self.height * max(part.generated_frames for part in self.segments)

    @property
    def user_hint(self):
        return ('Helyi AI: %d×%d · %d képkocka/mp · %d egymást követő rész. '
                'Kért hossz: %s mp; a folytatás az előző rész utolsó képkockájából készül. '
                'Egy AI-feladat legfeljebb %d képkocka; a videó hossza megmarad.' %
                (self.width, self.height, self.fps, len(self.segments),
                 format(self.duration, '.6g'), self.max_generated_frames))


def _policy(quality):
    if not isinstance(quality, str):
        raise ValueError('Válassz érvényes AI-részletességet.')
    label = quality.split(' •', 1)[0]
    if label not in _POLICIES:
        raise ValueError('Válassz érvényes AI-részletességet.')
    return label, _POLICIES[label]


def _dimensions(width, height, area):
    if any(type(value) is not int or value < 32 or value % 32 for value in (width, height)):
        raise ValueError('Az AI-jelenet mérete pozitív, 32 képpont többszöröse legyen.')
    if width * height > area:
        raise ValueError('Az AI-jelenet felbontása meghaladja a választott részletesség terhelési keretét.')


def validate_segment(width, height, frames, quality):
    """Reject a graph exceeding the application budget before starting the GPU."""
    _, (maximum, area) = _policy(quality)
    _dimensions(width, height, area)
    if type(frames) is not int or frames < 5 or frames > maximum or (frames - 1) % 4:
        raise ValueError('Az AI-feladat képkockaszáma hibás vagy túllépi a terhelési keretet; '
                         'a hosszabb jelenetet egymást követő részekben kell elkészíteni.')


def plan_workload(width, height, fps, duration, quality):
    """Plan contiguous native frames, with one conditioning frame per continuation.

    Every output frame belongs to exactly one segment. Sampling uses 4n+1
    frames, and only surplus tail frames are discarded. Rounding up to a native
    frame never shortens the requested duration (the export may trim the tail).
    No crossfade, frame repetition, source-photo restart or video re-encoding
    is part of this plan. Those operations cannot substitute for continuation.
    """
    label, (maximum, area) = _policy(quality)
    _dimensions(width, height, area)
    if type(fps) is not int or fps != NATIVE_FPS:
        raise ValueError('Az élethű helyi motor natív sebessége 16 képkocka/mp.')
    if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
            not math.isfinite(duration) or duration <= 0):
        raise ValueError('A jelenet hosszának pozitív, véges értéknek kell lennie.')
    # Bound before multiplying or allocating. 128 detailed parts cover over
    # four minutes; an ordinary user-selected scene is limited to 120 seconds.
    limit = maximum + (MAX_SEGMENTS - 1) * (maximum - 1)
    if duration > limit / fps:
        raise ValueError('A jelenet túl hosszú a helyi AI-feladat terhelési keretéhez. '
                         'Bontsd több külön jelenetre; a program nem rövidíti le automatikusan.')
    needed = math.ceil(duration * fps)
    segments = []
    cursor = 0
    while cursor < needed:
        overlap = int(bool(segments))
        count = min(needed - cursor, maximum - overlap)
        generated = max(5, 1 + 4 * math.ceil((count + overlap - 1) / 4))
        segment = SegmentPlan(len(segments), cursor, count, generated, overlap,
                              cursor - 1 if overlap else None)
        validate_segment(width, height, generated, label)
        segments.append(segment)
        cursor = segment.output_stop
    return WorkloadPlan(width, height, fps, float(duration), label, needed,
                        maximum, tuple(segments))
