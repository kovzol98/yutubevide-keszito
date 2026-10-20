"""Fast, cancellable checks for imported local video clips."""
from __future__ import annotations

import math
from pathlib import Path
import re


def probe_video(engine, path):
    """Return container duration in seconds and whether an audio stream exists.

    Copying a single video packet verifies that there is usable moving-picture
    data without decoding or scanning a long recording. FFmpeg's uppercase V
    stream selector excludes album covers and other attached pictures. The
    input header reports duration to centisecond precision; unknown lengths
    are rejected instead of silently turning a recording into a five-second
    scene. Cancellation raised by the engine is deliberately not intercepted.
    """
    try:
        source = Path(path)
    except (TypeError, ValueError):
        raise ValueError('Válassz egy videófájlt a gépedről.') from None
    if not source.is_file():
        raise ValueError('Nem található a videófájl: ' + str(source))

    from ytmuhely import ffmpeg
    try:
        output = engine.run([
            ffmpeg(), '-hide_banner', '-nostdin', '-v', 'info',
            '-i', str(source.resolve()), '-map', '0:V:0', '-c:v', 'copy',
            '-frames:v', '1', '-an', '-sn', '-dn', '-nostats',
            '-progress', 'pipe:1', '-f', 'null', '-',
        ], timeout=30)
    except (OSError, RuntimeError) as error:
        raise ValueError(
            'A kijelölt fájlból nem olvasható videó. Válassz egy ép MP4-, '
            'MOV-, MKV- vagy más támogatott videófájlt.'
        ) from error

    if not re.search(r'(?m)^frame=\s*[1-9]\d*\s*$', output):
        raise ValueError('A kijelölt fájl nem tartalmaz lejátszható videót.')

    # Restrict metadata parsing to the input, excluding output stream details.
    metadata = output.split('Output #0,', 1)[0]
    match = re.search(r'\bDuration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)', metadata)
    if match:
        hours, minutes, seconds = map(float, match.groups())
        duration = hours * 3600 + minutes * 60 + seconds
    else:
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(
            'A videó hossza nem állapítható meg. Mentsd újra MP4 formátumban, '
            'majd add hozzá az új fájlt.'
        )
    has_audio = bool(re.search(r'(?m)^\s*Stream #0:\d+[^\r\n]*:\s*Audio:', metadata))
    return {'duration': duration, 'has_audio': has_audio}
