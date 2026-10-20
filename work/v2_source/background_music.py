"""Local soundtrack validation and final audio mixing without re-encoding video."""
from __future__ import annotations

import math
from pathlib import Path
import re

DEFAULT_MUSIC_VOLUME = 12.0
DEFAULT_VIDEO_VOLUME = 100.0

_STEREO = 'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo'
# FFmpeg already shifts an input's container start to zero. Fill or trim the
# remaining per-stream offset before resetting timestamps, so delayed clip
# audio stays in sync with the picture instead of moving to its first frame.
_TIMELINE_STEREO = 'aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo'
_LIMITER = 'alimiter=limit=0.95:attack=5:release=50:level=0:latency=1'


def validate_video_volume(volume=DEFAULT_VIDEO_VOLUME):
    """Accept the decimal comma used by the Hungarian original-audio control."""
    try:
        value = float(volume.strip().replace(',', '.') if isinstance(volume, str) else volume)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Az eredeti hang hangereje 0 és 100 közötti szám legyen.') from None
    if isinstance(volume, bool) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('Az eredeti hang hangereje 0 és 100 közötti szám legyen.')
    return value


def scene_audio(has_original_audio, video_volume=DEFAULT_VIDEO_VOLUME, voice=False):
    """Select or mix an imported clip's audio with the scene's voice input.

    Input 0 is the imported video; input 1 is generated narration or silence.
    The caller bounds the padded audio with its scene duration and maps video
    independently, so audio filtering never changes video packets.
    """
    volume = validate_video_volume(video_volume)
    if not has_original_audio or volume == 0:
        return [], '1:a:0'
    filters = [
        f'[0:a:0]{_TIMELINE_STEREO},volume={volume / 100:.8f},'
        'asetpts=PTS-STARTPTS,apad[original]'
    ]
    if voice:
        filters += [
            f'[1:a:0]{_STEREO},asetpts=PTS-STARTPTS,apad[voice]',
            '[original][voice]amix=inputs=2:duration=first:normalize=0,'
            f'{_LIMITER}[scene_audio]',
        ]
    else:
        filters[0] = filters[0].removesuffix('[original]') + '[scene_audio]'
    return ['-filter_complex', ';'.join(filters)], '[scene_audio]'


def validate_settings(music, volume=DEFAULT_MUSIC_VOLUME):
    """Validate even a muted selection so broken projects fail before AI work."""
    try:
        value = float(volume.strip().replace(',', '.') if isinstance(volume, str) else volume)
    except (TypeError, ValueError):
        raise ValueError('A zene hangereje 0 és 100 közötti szám legyen.') from None
    if isinstance(volume, bool) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('A zene hangereje 0 és 100 közötti szám legyen.')
    if music and not Path(music).is_file():
        raise ValueError('Nem található a kiválasztott zenefájl: ' + str(music))
    return value


def validate_audio(engine, music, volume=DEFAULT_MUSIC_VOLUME, work=None):
    """Decode a short sample, including at 0%, before starting slow AI rendering.

    ashowinfo proves that an audio frame was actually decoded. Merely opening
    the container would also accept a video without an audio stream or a
    damaged, zero-length audio track. No temporary audio or external service
    is needed. ``work`` is accepted for the engine's preflight API.
    """
    value = validate_settings(music, volume)
    if not music:
        return value
    from ytmuhely import ffmpeg
    try:
        output = engine.run([
            ffmpeg(), '-hide_banner', '-nostdin', '-v', 'info', '-xerror',
            '-i', str(music), '-map', '0:a:0', '-vn', '-sn', '-dn',
            '-t', '0.25', '-af', 'ashowinfo', '-f', 'null', '-',
        ], timeout=30)
    except (RuntimeError, OSError) as error:
        raise ValueError(
            'A kijelölt zenéből nem olvasható hang. Válassz egy ép MP3-, WAV-, '
            'FLAC-, M4A- vagy más támogatott hangfájlt.'
        ) from error
    if not re.search(r'\bnb_samples:[1-9]\d*\b', output):
        raise ValueError('A kijelölt zenefájl nem tartalmaz lejátszható hangot.')
    return value


def export_command(merged, destination, duration, *, music='',
                   music_volume=DEFAULT_MUSIC_VOLUME, narration='',
                   mix_narration=False, binary=None):
    """Build one FFmpeg invocation; every filename remains its own argument."""
    volume = validate_settings(music, music_volume)
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        raise ValueError('A videó hossza pozitív, véges szám legyen.') from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('A videó hossza pozitív, véges szám legyen.')
    if binary is None:
        from ytmuhely import ffmpeg
        binary = ffmpeg()
    length = f'{seconds:.6f}'
    inputs = ['-i', str(merged)]
    base_audio = '0:a:0'
    filters = []
    if narration:
        inputs += ['-i', str(narration)]
        if mix_narration:
            filters += [
                f'[0:a:0]{_TIMELINE_STEREO},asetpts=PTS-STARTPTS,apad[original]',
                f'[1:a:0]{_TIMELINE_STEREO},asetpts=PTS-STARTPTS,apad[voice]',
            ]
            narration_mix = '[original][voice]amix=inputs=2:duration=first:normalize=0'
            if not (music and volume > 0):
                narration_mix += ',' + _LIMITER
            filters.append(narration_mix + '[narr]')
        else:
            filters.append('[1:a:0]aresample=48000:async=1:first_pts=0,apad[narr]')
        base_audio = 'narr'
    if music and volume > 0:
        music_index = 2 if narration else 1
        inputs += ['-stream_loop', '-1', '-i', str(music)]
        stereo = _STEREO
        fade = min(.35, seconds / 4)
        filters.append(
            f'[{base_audio}]{_TIMELINE_STEREO},apad,atrim=duration={length},'
            'asetpts=PTS-STARTPTS[base]'
        )
        filters.append(
            f'[{music_index}:a:0]{stereo},atrim=duration={length},'
            f'asetpts=PTS-STARTPTS,volume={volume / 100:.8f},'
            f'afade=t=in:st=0:d={fade:.6f},'
            f'afade=t=out:st={seconds - fade:.6f}:d={fade:.6f}[music]'
        )
        # Do not normalize quiet passages up or duck the chosen soundtrack.
        # Compensated limiter latency keeps narration aligned to the picture.
        filters.append(
            '[base][music]amix=inputs=2:duration=first:normalize=0,'
            f'{_LIMITER}[mix]'
        )
        base_audio = 'mix'
    args = [str(binary), '-y', *inputs]
    if filters:
        args += ['-filter_complex', ';'.join(filters)]
    args += [
        '-map', '0:v:0', '-map', f'[{base_audio}]' if filters else base_audio,
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
    ]
    if (music and volume > 0) or (narration and mix_narration):
        args += ['-ar', '48000', '-ac', '2']
    args += ['-t', length, '-movflags', '+faststart', str(destination)]
    return args


def export_video(engine, merged, destination, duration, *, music='',
                 music_volume=DEFAULT_MUSIC_VOLUME, narration='',
                 mix_narration=False):
    """Keep scene audio and optionally mix, or replace it with an own voice file."""
    args = export_command(merged, destination, duration, music=music,
                          music_volume=music_volume, narration=narration,
                          mix_narration=mix_narration)
    engine.run(args)
    return Path(destination)
