"""Durable scene previews and local picture replacement without AI calls.

Scene movies already contain their original sound and any burned subtitles.
Only a replacement receives the saved ASS/grade; unchanged clips are copied.
The finished movie's entire audio stream is retained without re-encoding.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid

import soundtrack_cache

FOLDER = 'jelenetek'
MANIFEST = 'jelenetek.json'
MAX_SCENES = 10000


@dataclass(frozen=True)
class Scene:
    index: int
    video: Path
    caption: Path | None
    frames: int
    size: tuple[int, int]
    captions_enabled: bool
    cues: tuple[tuple[float, float, str], ...]
    label: str
    enhancement: str
    lighting: str

    @property
    def duration(self):
        return self.frames / 30


@dataclass(frozen=True)
class Library:
    root: Path
    status: str
    expected_scenes: int
    scenes: tuple[Scene, ...]


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('A mentett jelenet számadata hibás.')
    return value


def _check(engine):
    if engine is not None:
        engine.check()


def _root(folder):
    return soundtrack_cache._folder(folder)


def _safe(path):
    if any(soundtrack_cache._linked(item) for item in (path, *path.parents)):
        raise ValueError('A jelenettár átirányított fájlt vagy mappát tartalmaz.')
    return path


def _write(root, data):
    target = _safe(root / MANIFEST)
    partial = root / ('.jelenetek.pending_' + uuid.uuid4().hex + '.json')
    try:
        with partial.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _read(root):
    manifest = _safe(root / MANIFEST)
    if not manifest.is_file() or manifest.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Ehhez a projekthez nincs olvasható jelenettár.')
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or set(data) !=
                {'schema', 'status', 'expected_scenes', 'scenes', 'final_video'}
                or type(data['schema']) is not int or data['schema'] != 1
                or data['status'] not in ('building', 'complete')):
            raise ValueError('Ismeretlen jelenettár-verzió.')
        _integer(data['expected_scenes'], 1, MAX_SCENES)
        if (not isinstance(data['scenes'], list)
                or len(data['scenes']) > data['expected_scenes']
                or data['status'] == 'complete' and
                   len(data['scenes']) != data['expected_scenes']
                or data['status'] == 'building' and data['final_video'] is not None):
            raise ValueError('Hiányos jelenetlista.')
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError('A jelenettár leírása sérült.') from error


def _file(folder, record, name, engine=None):
    try:
        return soundtrack_cache._cached_file(folder, record, name, engine)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError('A jelenettár egyik fájlja hiányzik, átirányított vagy megsérült.') from error


def _record(record, name):
    """Validate an existing record without re-reading its large media file."""
    if (not isinstance(record, dict) or set(record) != {'name', 'bytes', 'sha256'}
            or record['name'] != name or type(record['bytes']) is not int
            or record['bytes'] <= 0 or not isinstance(record['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
        raise ValueError('A jelenet mentett fájlleírása hibás.')


def _scene_meta(record):
    from video_look import validate_lighting
    from video_quality import validate_enhancement
    if not isinstance(record, dict) or set(record) != {
            'video', 'caption', 'frames', 'size', 'captions_enabled', 'cues',
            'label', 'enhancement', 'lighting'}:
        raise ValueError('A jelenet leírása hibás.')
    _integer(record['frames'], 1, 30 * 86400)
    size = record['size']
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError('Hibás jelenetméret.')
    for value in size:
        _integer(value, 2, 16384)
        if value % 2:
            raise ValueError('A jelenetméret páros szám legyen.')
    if type(record['captions_enabled']) is not bool:
        raise ValueError('Hibás feliratbeállítás.')
    if not isinstance(record['label'], str) or len(record['label']) > 20000:
        raise ValueError('Hibás jelenetcím.')
    validate_enhancement(record['enhancement'])
    validate_lighting(record['lighting'])
    if not isinstance(record['cues'], list) or len(record['cues']) > 10000:
        raise ValueError('Hibás feliratlista.')
    previous = 0
    for cue in record['cues']:
        if (not isinstance(cue, list) or len(cue) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in cue[:2])
                or not 0 <= cue[0] <= cue[1] <= record['frames'] / 30 + .05
                or cue[0] < previous or not isinstance(cue[2], str)
                or len(cue[2]) > 20000):
            raise ValueError('Hibás feliratidőzítés.')
        previous = cue[0]


def load(project_folder, engine=None):
    """Validate every retained file, including previews of a building library."""
    _check(engine)
    root = _root(project_folder)
    data = _read(root)
    folder = _safe(root / FOLDER)
    if not folder.is_dir():
        raise ValueError('A jelenetek mappája nem található.')
    scenes = []
    for index, record in enumerate(data['scenes']):
        _check(engine)
        try:
            _scene_meta(record)
            video = _file(folder, record['video'], f'{index+1:04d}.mp4', engine)
            caption = (_file(folder, record['caption'], f'{index+1:04d}.ass', engine)
                       if record['caption'] is not None else None)
            if record['captions_enabled'] and record['cues'] and caption is None:
                raise ValueError('Hiányzik a jelenet felirata.')
            scenes.append(Scene(index, video, caption, record['frames'], tuple(record['size']),
                                record['captions_enabled'], tuple(tuple(c) for c in record['cues']),
                                record['label'], record['enhancement'], record['lighting']))
        except (KeyError, TypeError, OSError, ValueError) as error:
            raise ValueError(f'A(z) {index+1}. jelenet nem olvasható vagy megsérült.') from error
    if data['status'] == 'complete':
        _file(root, data['final_video'], 'video.mp4', engine)
    _check(engine)
    return Library(root, data['status'], data['expected_scenes'], tuple(scenes))


def create(engine, project_folder, expected_scenes):
    _check(engine)
    _integer(expected_scenes, 1, MAX_SCENES)
    root = _root(project_folder)
    if (root / MANIFEST).exists() or (root / FOLDER).exists():
        raise ValueError('Ebben a projektben már van jelenettár; azt nem írjuk felül.')
    folder = root / FOLDER
    folder.mkdir()
    try:
        _write(root, {'schema': 1, 'status': 'building', 'expected_scenes': expected_scenes,
                      'scenes': [], 'final_video': None})
    except BaseException:
        folder.rmdir()
        raise
    return load(root, engine)


def _copy(engine, source, destination):
    source = Path(source)
    digest = soundtrack_cache._digest(source, engine)
    created = False
    try:
        with source.open('rb') as reader, destination.open('xb') as writer:
            created = True
            while True:
                _check(engine)
                block = reader.read(4 * 1024 * 1024)
                if not block:
                    break
                writer.write(block)
        if soundtrack_cache._digest(destination, engine) != digest:
            raise ValueError('A jelenet másolás közben megváltozott.')
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def add_scene(engine, project_folder, clip, frames, *, caption_file=None,
              captions_enabled=False, cues=(), label='', size,
              enhancement='Eredeti megjelenés', lighting='Eredeti világítás'):
    _check(engine)
    root = _root(project_folder)
    snapshot = _read(root)
    folder = _safe(root / FOLDER)
    if not folder.is_dir():
        raise ValueError('A jelenetek mappája nem található.')
    # A render appends in order. Re-reading every previous movie at every
    # append makes a long project quadratic in disk I/O; public load and
    # completion still perform the complete hash verification.
    for position, previous in enumerate(snapshot['scenes'], 1):
        _scene_meta(previous)
        _record(previous['video'], f'{position:04d}.mp4')
        if previous['caption'] is not None:
            _record(previous['caption'], f'{position:04d}.ass')
    if snapshot['status'] != 'building' or len(snapshot['scenes']) >= snapshot['expected_scenes']:
        raise ValueError('A jelenettár már elkészült vagy megtelt.')
    record = {'video': None, 'caption': None, 'frames': frames, 'size': list(size),
              'captions_enabled': captions_enabled, 'cues': [list(c) for c in cues],
              'label': label, 'enhancement': enhancement, 'lighting': lighting}
    _scene_meta(record)
    if captions_enabled and cues and not caption_file:
        raise ValueError('A bekapcsolt felirat fájlja hiányzik.')
    if not Path(clip).is_file() or caption_file and not Path(caption_file).is_file():
        raise ValueError('A megtartandó jelenet vagy felirat nem található.')
    index = len(snapshot['scenes']) + 1
    destinations = []
    try:
        for source, suffix, key in ((clip, '.mp4', 'video'), (caption_file, '.ass', 'caption')):
            if source:
                target = _safe(folder / f'{index:04d}{suffix}')
                _copy(engine, source, target)
                destinations.append(target)
                record[key] = soundtrack_cache._description(target, engine)
        data = _read(root)
        if data != snapshot:
            raise ValueError('A jelenettár időközben megváltozott.')
        data['scenes'].append(record)
        _check(engine)
        _write(root, data)
        destinations.clear()
    finally:
        for target in destinations:
            target.unlink(missing_ok=True)
    return Scene(index-1, folder/f'{index:04d}.mp4',
                 folder/f'{index:04d}.ass' if caption_file else None,
                 frames, tuple(size), captions_enabled, tuple(tuple(c) for c in cues),
                 label, enhancement, lighting)


def complete(engine, project_folder):
    library = load(project_folder, engine)
    if library.status == 'complete':
        return library
    if len(library.scenes) != library.expected_scenes:
        raise ValueError('Még nem készült el minden jelenet.')
    cache = soundtrack_cache.load_source(library.root, engine)
    if abs(cache.duration - sum(s.duration for s in library.scenes)) > 1 / 30 + .001:
        raise ValueError('A mentett hangalap és a jelenetek hossza eltér.')
    video = _safe(library.root / 'video.mp4')
    if not video.is_file() or video.stat().st_size == 0:
        raise ValueError('A végleges videó még nem készült el.')
    data = _read(library.root)
    data['final_video'] = soundtrack_cache._description(video, engine)
    data['status'] = 'complete'
    _check(engine)
    _write(library.root, data)
    # Publication wins a cancellation arriving immediately after this atomic
    # write. No subsequent cancellable read can turn a committed library into
    # an apparent failure and make the caller remove its finished movie.
    return Library(library.root, 'complete', library.expected_scenes, library.scenes)


def _decode(engine, filename, expected_frames=None):
    """Decode the complete moving picture, with audio explicitly excluded."""
    from ytmuhely import ffmpeg
    try:
        output = engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                             '-protocol_whitelist', 'file,pipe', '-i', str(filename),
                             '-map', '0:V:0', '-an', '-sn', '-dn',
                             '-vf', 'setpts=PTS-STARTPTS,fps=30:eof_action=pass',
                             '-nostats', '-progress', 'pipe:1', '-f', 'null', '-'])
    except (OSError, RuntimeError) as error:
        raise ValueError('A cserevideó nem olvasható végig. Válassz egy ép mozgóképet.') from error
    frames = re.findall(r'(?m)^frame=\s*(\d+)\s*$', output)
    times = re.findall(r'(?m)^out_time_us=\s*(-?\d+)\s*$', output)
    if not frames or int(frames[-1]) < 2 or not times or int(times[-1]) <= 0:
        raise ValueError('A cserevideó nem tartalmaz lejátszható mozgóképet.')
    count = int(frames[-1])
    if expected_frames is not None and count != expected_frames:
        raise ValueError('A szerkesztett mozgókép képkockaszáma nem egyezik a jelenetek hosszával.')
    return count / 30


def _mux(engine, picture, audio_source, destination):
    from ytmuhely import ffmpeg
    engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-xerror',
                '-protocol_whitelist', 'file,pipe', '-i', str(picture),
                '-protocol_whitelist', 'file,pipe', '-i', str(audio_source), '-map', '0:V:0',
                '-map', '1:a:0', '-c', 'copy', '-map_metadata', '-1',
                '-movflags', '+faststart', str(destination)])


def _caption_exports(folder, scenes):
    def stamp(t):
        ms = round(t * 1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    elapsed = 0
    rows = []
    for scene in scenes:
        for start, end, text in scene.cues:
            if text:
                rows.append(f'{len(rows)+1}\n{stamp(elapsed+start)} --> {stamp(elapsed+end)}\n{text}')
        elapsed += scene.duration
    (folder / 'felirat.srt').write_text('\n\n'.join(rows) + '\n', encoding='utf-8')


def _project(root, stage, destination, library, source):
    from ytmuhely import Project, SIZES
    allowed = {item.name for item in fields(Project)}
    data = asdict(Project())
    original = next((root / name for name in ('projekt.json', 'project.json')
                     if (root / name).is_file()), None)
    if original:
        _safe(original)
        if original.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('A projekt leírása túl nagy.')
        try:
            stored = json.loads(original.read_text(encoding='utf-8'))
            if not isinstance(stored, dict):
                raise ValueError('Hibás projekt.')
            # Never copy arbitrary fields (especially credentials) from JSON.
            data.update({key: value for key, value in stored.items() if key in allowed})
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('A projekt leírása nem olvasható.') from error
    data.update(visual_mode='Saját videók', images=[],
                # A normal project reload imports the self-contained finished
                # movie, including its complete English narration and music.
                # Detailed scene editing and soundtrack edits use the library
                # and music-free cache independently of this import project.
                videos=[str(destination / 'video.mp4')],
                text='', video_prompt='', subtitles=False, use_video_length=True,
                video_volume=100.0, lighting='Eredeti világítás',
                enhancement='Eredeti megjelenés', face_restore=False,
                music='', output=str(destination), narration='Nincs narráció', audio='')
    # Imported pictures already have the intended frame and subtitle layout.
    size = library.scenes[0].size
    data['format'] = next((key for key, value in SIZES.items() if value == size),
                          'Shorts • 9:16' if size[1] > size[0] else '720p • 16:9')
    text = json.dumps(data, ensure_ascii=False, indent=2)
    (stage / 'projekt.json').write_text(text, encoding='utf-8')
    (stage / 'project.json').write_text(text, encoding='utf-8')


def export_variant(engine, project_folder, replacements: dict[int, str], *, soundtrack=None):
    """Replace selected pictures locally; publish only a complete new project."""
    from ytmuhely import ffmpeg
    from video_look import lighting_filter
    from video_quality import post_filter, pre_filter
    engine.check()
    library = load(project_folder, engine)
    if library.status != 'complete':
        raise ValueError('Jelenetcsere csak az elkészült videóban végezhető.')
    if (not isinstance(replacements, dict) or not replacements
            or any(type(i) is not int or i < 0 or i >= len(library.scenes) for i in replacements)):
        raise ValueError('Válassz legalább egy érvényes, cserélendő jelenetet.')
    source = soundtrack_cache.load_source(library.root, engine)
    duration = sum(scene.duration for scene in library.scenes)
    if abs(source.duration - duration) > 1 / 30 + .001:
        raise ValueError('A hangalap és a jelenettár hossza eltér.')
    if len({scene.size for scene in library.scenes}) != 1:
        raise ValueError('A jelenetek képmérete eltér.')
    original_soundtrack = library.root / 'video.mp4'
    if soundtrack is not None:
        try:
            chosen = _safe(Path(soundtrack).expanduser().absolute())
            if (chosen.parent.resolve() != library.root or chosen.suffix.lower() != '.mp4'
                    or not chosen.is_file()):
                raise ValueError('A megtartandó hang a projekt saját MP4-videójából választható.')
            original_soundtrack = chosen.resolve()
        except (TypeError, OSError) as error:
            raise ValueError('A megtartandó hang videója nem található.') from error
        _decode(engine, original_soundtrack, sum(scene.frames for scene in library.scenes))
        # Validate the complete chosen audio, rather than only its header or a
        # short preview. Protocol restrictions also apply to imported remixes.
        try:
            engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                        '-protocol_whitelist', 'file,pipe', '-i', str(original_soundtrack),
                        '-map', '0:a:0', '-vn', '-sn', '-dn', '-f', 'null', '-'])
        except (OSError, RuntimeError) as error:
            raise ValueError('A kiválasztott változat teljes hangja nem olvasható.') from error
    soundtrack_digest = soundtrack_cache._digest(original_soundtrack, engine)
    selected = {}
    for index, filename in sorted(replacements.items()):
        engine.check()
        try:
            path = Path(filename).expanduser().resolve(strict=True)
        except (TypeError, ValueError, OSError) as error:
            raise ValueError('A kiválasztott cserevideó nem található.') from error
        if not path.is_file():
            raise ValueError('Válassz egy helyi videófájlt.')
        engine.log(f'{index+1}. cserevideó teljes ellenőrzése…')
        seconds = _decode(engine, path)
        required = library.scenes[index].duration
        if seconds + 1 / 30 + .000001 < required:
            raise ValueError(f'A(z) {index+1}. cserevideó túl rövid: {seconds:.2f} mp; '
                             f'legalább {required:.2f} mp szükséges. Nem ismételjük és nem fagyasztjuk ki a képet.')
        selected[index] = (path, soundtrack_cache._digest(path, engine))
    token = uuid.uuid4().hex
    root = library.root
    stage = root / ('.jelenetcsere.pending_' + token)
    destination = root / ('Jelenetcsere_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + token[:8])
    stage.mkdir()
    work = stage / '.munka'
    work.mkdir()
    try:
        create(engine, stage, library.expected_scenes)
        for scene in library.scenes:
            engine.check()
            clip = scene.video
            if scene.index in selected:
                path, digest = selected[scene.index]
                engine.log(f'{scene.index+1}. jelenet képének cseréje · az eredeti hang megmarad.')
                w, h = scene.size
                # Normalize time before scale/pad, and pad after fps. FFmpeg
                # 7.1 may discard a terminal frame or the pad when fps follows
                # tpad/spatial filters. Exactly one 30fps clone is available;
                # preflight rejects any larger shortfall and output is bounded.
                filters = ['setpts=PTS-STARTPTS', 'fps=30:eof_action=pass',
                           'tpad=stop_mode=clone:stop=1']
                filters += [part for part in (pre_filter(scene.enhancement, True),
                                              lighting_filter(scene.lighting)) if part]
                filters += [f'scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos',
                            f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2', 'setsar=1']
                after = post_filter(scene.enhancement)
                if after:
                    filters.append(after)
                if scene.captions_enabled and scene.caption:
                    _copy(engine, scene.caption, work / 'felirat.ass')
                    filters.append('ass=felirat.ass')
                picture = work / 'kep.mp4'
                engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-xerror',
                            '-protocol_whitelist', 'file,pipe', '-i', str(path),
                            '-map', '0:V:0', '-an', '-vf', ','.join(filters),
                            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                            '-pix_fmt', 'yuv420p', '-r', '30', '-frames:v', str(scene.frames),
                            '-movflags', '+faststart', str(picture)], cwd=work)
                _decode(engine, picture, scene.frames)
                clip = work / 'jelenet.mp4'
                _mux(engine, picture, scene.video, clip)
                if soundtrack_cache._digest(path, engine) != digest:
                    raise ValueError('A cserevideó feldolgozás közben megváltozott.')
            add_scene(engine, stage, clip, scene.frames, caption_file=scene.caption,
                      captions_enabled=scene.captions_enabled, cues=scene.cues,
                      label=scene.label, size=scene.size, enhancement=scene.enhancement,
                      lighting=scene.lighting)
            for item in ('felirat.ass', 'kep.mp4', 'jelenet.mp4'):
                (work / item).unlink(missing_ok=True)
        # Each scene has an exact timeline. Normalize codec parameters even when
        # original clips came from different hardware encoders/profiles.
        edited = load(stage, engine)
        listing = stage / 'jelenetek_lista.txt'
        listing.write_text(''.join(f"file '{FOLDER}/{s.video.name}'\nduration {s.duration:.9f}\n"
                                   for s in edited.scenes), encoding='utf-8')
        picture = work / 'szerkesztett_kep.mp4'
        engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-xerror',
                    '-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe',
                    '-i', listing.name, '-map', '0:V:0', '-an',
                    '-vf', 'setpts=PTS-STARTPTS,fps=30:eof_action=pass,setsar=1',
                    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                    '-pix_fmt', 'yuv420p', '-r', '30', '-frames:v', str(sum(s.frames for s in edited.scenes)),
                    '-movflags', '+faststart', str(picture)], cwd=stage)
        _decode(engine, picture, sum(s.frames for s in edited.scenes))
        _mux(engine, picture, original_soundtrack, stage / 'video.mp4')
        if soundtrack_cache._digest(original_soundtrack, engine) != soundtrack_digest:
            raise ValueError('A megtartandó hang videója feldolgozás közben megváltozott.')
        music_free = work / 'zenementes.mp4'
        _mux(engine, picture, source.video, music_free)
        soundtrack_cache.save_source(engine, music_free, stage, duration,
                                     narration=source.narration, mix_narration=source.mix_narration)
        _project(root, stage, destination, edited, source)
        _caption_exports(stage, edited.scenes)
        for filename in ('youtube_leiras.txt',):
            if (root / filename).is_file():
                _copy(engine, _safe(root / filename), stage / filename)
        engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error',
                    '-i', str(stage / 'video.mp4'), '-frames:v', '1', str(stage / 'boritokep.png')])
        listing.unlink()
        if work.resolve().parent != stage.resolve() or soundtrack_cache._linked(work):
            raise ValueError('A munkamappa helye megváltozott.')
        shutil.rmtree(work)
        complete(engine, stage)
        engine.check()
        if destination.exists() or soundtrack_cache._linked(destination):
            raise ValueError('A célmappa már létezik.')
        stage.rename(destination)
        engine.log('A jelenetcserés változat külön mappában elkészült: ' + str(destination))
        return destination / 'video.mp4'
    finally:
        # Delete only this uniquely named, unpublished child after containment
        # validation. Never remove the original or a completed variant.
        if (stage.is_dir() and stage.name == '.jelenetcsere.pending_' + token
                and stage.resolve().parent == root.resolve()
                and not soundtrack_cache._linked(stage)):
            shutil.rmtree(stage)
