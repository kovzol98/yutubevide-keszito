"""Describe the selected video route without starting a renderer or AI motor."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from reference_framing import FULL_FRAME,SUBJECT_FRAME,validate_framing


@dataclass(frozen=True)
class VideoPlan:
    pipeline: str
    native_size: tuple[int, int] | None
    native_fps: int | None
    export_size: tuple[int, int] | None
    export_fps: int
    generation_hint: str
    export_hint: str
    time_hint: str


def _dimensions(size):
    return '%d×%d' % size if size else 'a választott képméret'


def _portrait_size(path):
    # Match the portrait worker's full-photo canvas, including EXIF orientation.
    with Image.open(path) as source:
        width, height = ImageOps.exif_transpose(source).size
    if max(width, height) > 1920:
        ratio = 1920 / max(width, height)
        width, height = max(2, round(width * ratio)), max(2, round(height * ratio))
    return width // 2 * 2, height // 2 * 2


def describe_video_plan(visual_mode, image_motion, image_engine, ai_quality,
                        images, video_format, *, export_sizes=None, framing=FULL_FRAME,
                        grok_resolution='720p'):
    """Return current dimensions and timing guidance for the first scene.

    Opening a photo is the only I/O. Missing or unreadable photos yield a useful
    hint; no model, network connection, subprocess or GPU is initialized.
    """
    from comfy_video import PRESETS as legacy_presets, normalized_quality
    from image_motion import DEFAULT_IMAGE_MOTION, source_dimensions
    from realistic_video import PRESETS as quality_presets, QUALITY_ENGINE
    from video_quality import SOURCE_FORMAT, source_size
    validate_framing(framing)

    if export_sizes is None:
        from ytmuhely import SIZES
        export_sizes = SIZES
    first = Path(images[0]) if images else None
    native_size = native_fps = None
    export_size = export_sizes.get(video_format)
    photo_error = False
    framing_pending = False
    framing_unchanged = False
    if video_format == SOURCE_FORMAT and first and visual_mode != 'Saját videók':
        try:
            export_size = source_size(first)
        except (OSError, ValueError):
            pass

    if visual_mode == 'Grok videó • internet':
        if grok_resolution not in ('720p','1080p'):
            raise ValueError('A Grok felbontása 720p vagy 1080p lehet.')
        pipeline='grok-image' if first else 'grok-text'
        generation=('Fizetős xAI Grok Imagine 1.5 · %s. '%grok_resolution +
            ('A teljes fotót és a leírást az xAI szolgáltatásának küldi; a videó a fotó arányát követi.'
             if first else 'Az angol leírást az xAI szolgáltatásának küldi.'))
        generation+=' A helyi AI-részletesség, fotómotor, keretezés és arcjavítás itt nem érvényes.'
        timing='Jelenetenként legfeljebb 15 mp. A felhős várakozás több perc is lehet; a helyi GPU a generálást nem gyorsítja.'
    elif visual_mode == 'Saját videók':
        pipeline = 'own-video'
        generation = 'Saját felvételből készül; az AI-motor és az AI-részletesség választója itt nem érvényes.'
        timing = 'A szükséges időt a felvételek hossza és az MP4-mentés határozza meg.'
    elif visual_mode == 'Képes diavideó':
        pipeline = 'slideshow'
        native_size, native_fps = export_size, 30
        generation = 'Diavideó: fotóközelítés és felirat, %s · 30 képkocka/mp.' % _dimensions(export_size)
        timing = 'A szereplők külön AI-mozgását ez a mód nem generálja.'
    elif first and image_motion == DEFAULT_IMAGE_MOTION:
        pipeline = 'portrait'
        native_fps = 30
        try:
            native_size = _portrait_size(first)
        except (OSError, ValueError):
            photo_error = True
        generation = 'Portré: %s · 30 képkocka/mp, külön portrémotorral.' % _dimensions(native_size)
        timing = 'Finom fejmozgásra való. A Normál/Részletes és a fotóanimációs motor választója erre nem hat.'
    else:
        quality = normalized_quality(ai_quality)
        detailed = quality.startswith('Részletes')
        enhanced = bool(first) and image_engine == QUALITY_ENGINE
        width, height, native_fps, _ = (quality_presets if enhanced else legacy_presets)[quality]
        if not first and 'Shorts' in video_format:
            width, height = height, width
        native_size = (width, height)
        if first:
            try:
                native_size = source_dimensions(first, width, height)
                if enhanced and framing==SUBJECT_FRAME:
                    source_width,source_height=_portrait_size(first)
                    framing_pending=source_width*4>source_height*3
                    framing_unchanged=not framing_pending
                    if framing_pending:
                        native_size=None
                        if video_format==SOURCE_FORMAT:export_size=None
            except (OSError, ValueError):
                native_size = None
                photo_error = True
        pipeline = 'quality-image' if enhanced else ('legacy-image' if first else 'legacy-text')
        label = 'Élethű generálás' if enhanced else ('Alap generálás' if first else 'Fotó nélkül a korábbi motor')
        generation = '%s: %s · %d képkocka/mp.' % (label, _dimensions(native_size), native_fps)
        if framing_pending:
            generation = '%s: %d képkocka/mp.' % (label,native_fps)
            generation += (' Alanyra közelítés: arc felismerése esetén oldalsó, várhatóan 3:4 álló kivágás készül;'
                           ' a teljes fotómagasság megmarad. Arc nélkül a teljes fotót használja.'
                           ' A pontos AI-méret az arcfelismerés után derül ki.')
        elif framing_unchanged:
            generation += ' A fotó már álló; az alanyra közelítés nem vág az oldalából.'
        if enhanced:
            generation += ' Mozgássimítás: 30 képkocka/mp.'
        timing = ('Részletes: több időt és memóriát kér; egy rövid jelenet is több tíz percig készülhet; első próba: 2 mp.'
                  if detailed else 'Normál: egy rövid jelenet is több tíz percig készülhet; első próba: 2 mp.')
        if enhanced:
            cap=33 if detailed else 49
            timing+=(' A hosszabb jelenet legfeljebb %d képkockás részekben készül, '
                     'az előző rész utolsó képkockájából folytatva. A kért hossz megmarad. '
                     'A részek határán a mozgás változhat. GPU-újraindulás vagy hibás '
                     'képkockák esetén a készítés és a munkasor leáll.'%cap)
    if photo_error:
        generation = 'Az első fotó nem olvasható; a pontos generálási méret nem állapítható meg. ' + generation
    if len(images) > 1 and pipeline in ('portrait', 'quality-image', 'legacy-image'):
        generation += ' A méret az első fotóra vonatkozik.'

    if framing_pending and video_format==SOURCE_FORMAT:
        export_hint = ('Az eredeti arányú export pontos mérete az arcfelismerés utáni kivágástól függ;'
                       ' arc nélkül a teljes fotó aránya marad. Export: 30 képkocka/mp.')
    elif export_size is None:
        export_hint = 'Az export pontos méretéhez olvasható fotó kell; Saját videók módban válassz fix exportméretet.'
    else:
        export_hint = 'Export: %s · 30 képkocka/mp.' % _dimensions(export_size)
        if pipeline not in ('slideshow', 'own-video'):
            export_hint += ' A jelenet ehhez méretezve, szükség esetén szegéllyel kerül bele. A nagyítás nem ad új AI-részletet.'
        elif pipeline == 'own-video':
            export_hint += ' A forrásfelvételt ehhez méretezi; a nagyítás nem ad új részletet.'
    return VideoPlan(pipeline, native_size, native_fps, export_size, 30,
                     generation, export_hint, timing)
