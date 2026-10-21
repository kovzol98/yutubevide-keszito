"""Validate decoded AI frames before publishing or interpolating a scene.

This detects corrupt/incorrect files and collapsed, uniformly colored decoder
output from a detailed reference. It does not rate motion or require frames to
differ: a detailed static shot is a valid result.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageStat


FLAT_CHANNEL_SPAN = 2
DETAILED_REFERENCE_STDDEV = 4.0


class FrameIntegrityError(RuntimeError):
    """Invalid generated output must stop a queue before wasting more jobs."""
    halt_queue = True


def _visible_rgb(image):
    if image.mode == 'RGBA' or 'transparency' in image.info:
        rgba = image.convert('RGBA')
        background = Image.new('RGBA', rgba.size, (0, 0, 0, 255))
        return Image.alpha_composite(background, rgba).convert('RGB')
    return image.convert('RGB')


def _save_report(path, report):
    path = Path(path)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def validate_sequence(engine, folder, expected_count, expected_size, *, reference=None,
                      report_path=None):
    """Decode every native PNG, preserving the staging folder on any failure."""
    folder = Path(folder)
    report_path = Path(report_path) if report_path is not None else folder.parent / 'frame_integrity.json'
    if (isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 1 or
            not isinstance(expected_size, (tuple, list)) or len(expected_size) != 2 or
            any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in expected_size)):
        raise ValueError('A képkockák elvárt száma vagy mérete érvénytelen.')
    expected_size = tuple(expected_size)
    report = dict(schema=1, status='checking', expected_frames=expected_count,
                  expected_size=list(expected_size), frames_found=0, checked_frames=0,
                  flat_frames=0, reference_detailed=False,
                  flat_channel_span_limit=FLAT_CHANNEL_SPAN,
                  reference_detail_threshold=DETAILED_REFERENCE_STDDEV)
    try:
        engine.check()
        files = sorted(folder.glob('*.png'))
        report['frames_found'] = len(files)
        if len(files) != expected_count:
            report['reason'] = 'frame_count'
            raise FrameIntegrityError('Az AI-jelenet képkockái hiányosak. Nem mentettem kész videóként; '
                               'a köztes fájlok megmaradtak. Készítsd újra ezt a jelenetet.')
        if any(frame.name != 'frame_%05d.png' % index for index, frame in enumerate(files)):
            report['reason'] = 'frame_numbering'
            raise FrameIntegrityError('Az AI-jelenet képkockáinak számozása hibás. A köztes fájlok '
                               'megmaradtak; készítsd újra ezt a jelenetet.')
        if reference is not None:
            engine.check()
            try:
                with Image.open(reference) as image:
                    image.load()
                    rgb = _visible_rgb(image)
                    detail = max(ImageStat.Stat(rgb).stddev)
            except (OSError, ValueError, SyntaxError) as error:
                report['reason'] = 'reference_decode'
                raise FrameIntegrityError('A jelenet referenciafotója nem olvasható. Ellenőrizd a fotót, '
                                   'majd készítsd újra a jelenetet.') from error
            report['reference_max_stddev'] = round(detail, 6)
            report['reference_detailed'] = detail >= DETAILED_REFERENCE_STDDEV
        for index, frame in enumerate(files):
            engine.check()
            try:
                with Image.open(frame) as image:
                    if image.format != 'PNG':
                        report['reason'] = 'frame_format'
                        raise FrameIntegrityError('Az AI-motor hibás formátumú képkockát adott. A köztes '
                                           'fájlok megmaradtak; készítsd újra ezt a jelenetet.')
                    image.load()
                    if image.size != expected_size:
                        report.update(reason='frame_dimensions', actual_size=list(image.size),
                                      invalid_frame=index)
                        raise FrameIntegrityError('Az AI-motor eltérő méretű képkockát adott. Nem mentettem '
                                           'kész videóként; készítsd újra ezt a jelenetet.')
                    rgb = _visible_rgb(image)
                    flat = all(high - low <= FLAT_CHANNEL_SPAN for low, high in rgb.getextrema())
            except (OSError, ValueError, SyntaxError) as error:
                report.update(reason='frame_decode', invalid_frame=index)
                raise FrameIntegrityError('Az AI-motor sérült vagy olvashatatlan képkockát adott. Nem '
                                   'mentettem kész videóként; a köztes fájlok megmaradtak. '
                                   'Készítsd újra ezt a jelenetet.') from error
            report['checked_frames'] += 1
            report['flat_frames'] += int(flat)
        engine.check()
        if report['flat_frames'] == expected_count and report['reference_detailed']:
            report['reason'] = 'uniform_output_from_detailed_reference'
            raise FrameIntegrityError('Az AI-motor a fotó helyett csak egyszínű képkockákat készített. '
                               'Ezt nem mentettem kész videóként. A köztes fájlok megmaradtak. '
                               'Próbálj rövidebb jelenetet és kisebb részletességet; '
                               'a részletek a jelenet frame_integrity.json fájljában találhatók.')
        report.update(status='passed', reason='valid_sequence')
        _save_report(report_path, report)
        return report
    except BaseException as error:
        report.update(status='cancelled' if type(error).__name__ == 'Cancelled' else 'failed',
                      error_type=type(error).__name__)
        report.setdefault('reason', 'interrupted_validation')
        try:
            _save_report(report_path, report)
        except OSError:
            # Report storage must not hide the original validation/cancel error.
            pass
        raise
