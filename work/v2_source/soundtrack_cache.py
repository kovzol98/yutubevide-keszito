"""Keep a self-contained, music-free movie for inexpensive soundtrack edits."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import uuid

import background_music


CACHE_FOLDER = 'hang_alap'
MANIFEST_NAME = 'hang_alap.json'
VIDEO_NAME = 'alapvideo.mp4'
NARRATION_NAME = 'narracio.flac'


@dataclass(frozen=True)
class SoundtrackSource:
    video: Path
    narration: str
    duration: float
    manifest: Path
    mix_narration: bool = False


def project_folder_for_selection(filename):
    """Also accept the surviving project/cache after a failed final music mix."""
    path=Path(filename).expanduser().absolute()
    if not path.is_file():
        raise ValueError('A kiválasztott videó vagy projekt nem található.')
    folder=path.parent
    if folder.name==CACHE_FOLDER and path.name in (VIDEO_NAME,MANIFEST_NAME):
        folder=folder.parent
    return folder


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _folder(project_folder):
    path = Path(project_folder).expanduser().absolute()
    if not path.is_dir() or any(_linked(item) for item in (path, *path.parents)):
        raise ValueError('Nem található a kész videó saját projektmappája.')
    return path.resolve()


def _seconds(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('A mentett videó hossza hibás.') from None
    if isinstance(value, bool) or not math.isfinite(result) or result <= 0:
        raise ValueError('A mentett videó hossza hibás.')
    return result


def _digest(path, engine=None):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            if engine is not None:
                engine.check()
            block = source.read(4 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _description(path, engine=None):
    return {'name': path.name, 'bytes': path.stat().st_size,
            'sha256': _digest(path, engine)}


def _cached_file(folder, record, expected, engine=None):
    if (not isinstance(record, dict) or set(record) != {'name', 'bytes', 'sha256'}
            or record.get('name') != expected
            or type(record.get('bytes')) is not int or record['bytes'] <= 0
            or not isinstance(record.get('sha256'), str)
            or len(record['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in record['sha256'])):
        raise ValueError('A mentett hangalap leírása hibás.')
    path = folder / expected
    if (not path.is_file() or _linked(path) or path.stat().st_size != record['bytes']
            or _digest(path, engine) != record['sha256']):
        raise ValueError('A mentett hangalap hiányzik vagy megsérült. Az eredeti videó megmaradt.')
    return path


def load_source(project_folder, engine=None):
    """Read only fixed local names; reject missing, redirected or changed files."""
    if engine is not None:
        engine.check()
    root = _folder(project_folder)
    folder = root / CACHE_FOLDER
    manifest = folder / MANIFEST_NAME
    if not folder.is_dir() or _linked(folder) or not manifest.is_file() or _linked(manifest):
        raise ValueError('Ehhez a videóhoz még nincs mentett hangalap. Az újonnan készített videók zenéje külön is módosítható.')
    try:
        if manifest.stat().st_size > 16384:
            raise ValueError('Túl nagy a hangalap leírása.')
        if engine is not None:
            engine.check()
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
            raise ValueError('Ismeretlen hangalap-verzió.')
        seconds = _seconds(data['duration'])
        mix_narration = data.get('mix_narration', False)
        if type(mix_narration) is not bool:
            raise ValueError('Hibás narrációkeverés.')
        video = _cached_file(folder, data['video'], VIDEO_NAME, engine)
        voice = data.get('narration')
        narration = str(_cached_file(folder, voice, NARRATION_NAME, engine)) if voice is not None else ''
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError('A mentett hangalap nem olvasható vagy megsérült. Az eredeti videó megmaradt.') from error
    if engine is not None:
        engine.check()
    return SoundtrackSource(video, narration, seconds, manifest, mix_narration)


def _cleanup(folder, root):
    # Only remove a uniquely created sibling, never a published cache or project.
    if folder.parent == root and not _linked(folder) and folder.is_dir():
        shutil.rmtree(folder)


def save_source(engine, merged, project_folder, duration, *, narration='', mix_narration=False):
    """Save before adding music. A valid earlier cache is never replaced."""
    engine.check()
    root = _folder(project_folder)
    seconds = _seconds(duration)
    if type(mix_narration) is not bool:
        raise ValueError('A narrációkeverés beállítása hibás.')
    source = Path(merged)
    if not source.is_file():
        raise ValueError('Nem található a videó zenementes alapja.')
    if narration and not Path(narration).is_file():
        raise ValueError('Nem található a megtartandó narráció hangfájlja.')
    video_digest = _digest(source, engine)
    voice_digest = _digest(Path(narration), engine) if narration else None
    destination = root / CACHE_FOLDER
    if destination.exists() or _linked(destination):
        previous = load_source(root, engine)
        data = json.loads(previous.manifest.read_text(encoding='utf-8'))
        if (previous.duration == seconds and data['video']['sha256'] == video_digest
                and data.get('source_narration_sha256') == voice_digest
                and previous.mix_narration == mix_narration):
            return previous
        raise ValueError('Ebben a projektben már van mentett hangalap; azt nem írjuk felül.')
    stage = root / ('.hang_alap.pending_' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        video = stage / VIDEO_NAME
        with source.open('rb') as reader, video.open('xb') as writer:
            while True:
                engine.check()
                block = reader.read(4 * 1024 * 1024)
                if not block:
                    break
                writer.write(block)
        if _digest(video, engine) != video_digest:
            raise ValueError('A videó alapja másolás közben megváltozott.')
        # A decoded frame proves that this is a movie, rather than only a file.
        from ytmuhely import ffmpeg
        engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                    '-i', str(video), '-map', '0:v:0', '-frames:v', '1', '-an',
                    '-f', 'null', '-'], timeout=30)
        voice = None
        if narration:
            background_music.validate_audio(engine, narration)
            voice = stage / NARRATION_NAME
            engine.run([ffmpeg(), '-hide_banner', '-nostdin', '-y', '-v', 'error',
                        '-xerror', '-i', str(narration), '-map', '0:a:0', '-vn',
                        '-af', 'aresample=async=1:first_pts=0', '-c:a', 'flac', str(voice)])
            if _digest(Path(narration), engine) != voice_digest:
                raise ValueError('A narráció mentés közben megváltozott.')
        data = {'schema': 1, 'duration': seconds,
                'video': _description(video, engine),
                'narration': _description(voice, engine) if voice else None,
                'source_narration_sha256': voice_digest,
                'mix_narration': mix_narration}
        (stage / MANIFEST_NAME).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                           encoding='utf-8')
        engine.check()
        if destination.exists() or _linked(destination):
            raise ValueError('A hangalap már létezik; a meglévő fájlokat nem írjuk felül.')
        stage.rename(destination)
    finally:
        _cleanup(stage, root)
    return load_source(root, engine)


def _variant_project(root, source, music, volume):
    """Keep this a directly loadable Project JSON, without cache-only keys."""
    original = next((root / name for name in ('projekt.json', 'project.json')
                     if (root / name).is_file()), None)
    if original is None:
        return None
    if _linked(original):
        raise ValueError('A projekt leírása átirányított fájl.')
    try:
        data = json.loads(original.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Hibás projekt.')
        from ytmuhely import Project
        allowed = {item.name for item in fields(Project)}
        data = {key: value for key, value in data.items() if key in allowed}
        data.update(music=str(Path(music).resolve()) if music else '', music_volume=volume)
        if source.narration:
            data.update(narration='Saját hangfájl', audio=source.narration)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError('A projekt leírása nem olvasható; az eredeti videó megmaradt.') from error


def _publish_new_file(staging, destination):
    # Windows rename is atomic, refuses an existing destination and also works
    # on exFAT/FAT USB drives, where hard links are unavailable.
    if os.name == 'nt':
        os.rename(staging, destination)
        return
    # POSIX rename could overwrite a racing destination; an exclusive link cannot.
    os.link(staging, destination)
    try:
        staging.unlink()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def remix(engine, project_folder, *, music='', music_volume=12):
    """Export a unique variant using retained picture/voice; no AI generation."""
    engine.check()
    root = _folder(project_folder)
    source = load_source(root, engine)
    engine.check()
    volume = background_music.validate_audio(engine, music, music_volume)
    project_text = _variant_project(root, source, music, volume)
    stem = 'video_zene_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    destination = root / (stem + '.mp4')
    counter = 2
    while destination.exists() or destination.with_suffix('.projekt.json').exists():
        destination = root / (stem + '_' + str(counter) + '.mp4')
        counter += 1
    token = uuid.uuid4().hex
    partial = root / ('.partial_' + token + '.mp4')
    project_partial = root / ('.partial_' + token + '.json')
    project_path = destination.with_suffix('.projekt.json')
    published_movie = published_project = False
    try:
        engine.check()
        background_music.export_video(engine, source.video, partial, source.duration,
                                      music=music, music_volume=volume,
                                      narration=source.narration,
                                      mix_narration=source.mix_narration)
        if not partial.is_file() or partial.stat().st_size == 0:
            raise RuntimeError('A zenével módosított videó nem készült el.')
        if project_text is not None:
            project_partial.write_text(project_text, encoding='utf-8')
        engine.check()
        _publish_new_file(partial, destination)
        published_movie = True
        if project_text is not None:
            _publish_new_file(project_partial, project_path)
            published_project = True
        return destination
    except BaseException:
        if published_movie:
            destination.unlink(missing_ok=True)
        if published_project:
            project_path.unlink(missing_ok=True)
        raise
    finally:
        partial.unlink(missing_ok=True)
        project_partial.unlink(missing_ok=True)
