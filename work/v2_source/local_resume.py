"""Verify a saved local render before reusing its finished scene prefix.

This module never starts a model, synthesizes speech, or requests the network.
The immutable journal binds the original settings, exact scene text and source
bytes.  A continuation builds into a new folder; this module only reads the old
project.  Unfinished work without this journal cannot be guessed safely.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import re
import uuid

import scene_library
import soundtrack_cache

JOURNAL = 'folytatas.json'
PROJECT = 'projekt.json'
SCHEMA = 1
MAX_SCENES = 500
MAX_JSON = 16 * 1024 * 1024
LOCAL_MODES = ('AI-videó • helyi', 'AI-videó • helyi Wan',
               'Saját videók', 'Képes diavideó')
LOCAL_VOICES = ('Angol női LJ • offline', 'Saját hangfájl', 'Nincs narráció')
PATH_FIELDS = ('audio', 'music', 'output', 'ai_root', 'comfy_workflow')


@dataclass(frozen=True)
class ResumeState:
    root: Path
    project: dict
    chunks: tuple[str, ...]
    size: tuple[int, int]
    library: scene_library.Library


def _check(engine):
    if engine is not None:
        engine.check()


def _mapping(project):
    from ytmuhely import Project
    if isinstance(project, Project):
        return asdict(project)
    if not isinstance(project, dict) or any(type(k) is not str for k in project):
        raise ValueError('A folytatás projektbeállítása hibás.')
    names = {item.name for item in fields(Project)}
    if set(project) - names:
        raise ValueError('A projekt ismeretlen vagy nem menthető beállítást tartalmaz.')
    return dict(project)


def supported(project):
    """A local-only capability check, without opening a file or starting work."""
    try:
        data = _mapping(project)
        return (data.get('visual_mode', LOCAL_MODES[0]) in LOCAL_MODES
                and data.get('narration', LOCAL_VOICES[0]) in LOCAL_VOICES)
    except (TypeError, ValueError):
        return False


def _local_path(value, base):
    if not value:
        return ''
    if len(value) > 32768 or '\0' in value or re.match(r'(?i)^[a-z][a-z0-9+.-]*://', value):
        raise ValueError('A folytatáshoz helyi fájlútvonal szükséges.')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    # Do not resolve symlinks here: the active input check must still see them.
    return os.path.abspath(str(path))


def _project(project, base, *, strict=False):
    from ytmuhely import Project, SIZES
    from comfy_video import PRESETS
    from image_motion import validate_image_motion
    from realistic_video import IMAGE_ENGINES
    from reference_framing import validate_framing
    from video_look import validate_lighting
    from video_quality import validate_enhancement
    data = _mapping(project)
    defaults = asdict(Project())
    if strict and set(data) != set(defaults):
        raise ValueError('A mentett projekt beállításai hiányosak.')
    defaults.update(data)
    data = defaults
    if not supported(data):
        raise ValueError('Csak helyi videó és offline angol hang folytatható; internetes vagy Grok-feladat nem.')
    for name, default in asdict(Project()).items():
        value = data[name]
        if isinstance(default, str):
            if not isinstance(value, str) or '\0' in value:
                raise ValueError('A mentett projekt szöveges beállítása hibás.')
            limit = 2_000_000 if name in ('text', 'video_prompt') else 32768
            if len(value) > limit:
                raise ValueError('A mentett projekt szövege túl hosszú.')
        elif type(default) is bool:
            if type(value) is not bool:
                raise ValueError('A mentett projekt kapcsolóbeállítása hibás.')
        elif isinstance(default, list):
            if (not isinstance(value, list) or len(value) > MAX_SCENES
                    or any(not isinstance(item, str) or not item for item in value)):
                raise ValueError('A mentett projekt fájllistája hibás.')
            data[name] = [_local_path(item, base) for item in value]
    for name, low, high in (('seconds', 1, 120), ('music_volume', 0, 100), ('video_volume', 0, 100)):
        value = data[name]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('A mentett projekt hossza vagy hangereje hibás.')
    if type(data['grok_max_scenes']) is not int or not 1 <= data['grok_max_scenes'] <= 100:
        raise ValueError('A mentett projekt jelenetkorlátja hibás.')
    if data['grok_resolution'] not in ('720p', '1080p'):
        raise ValueError('A mentett projekt felbontása hibás.')
    if data['format'] not in SIZES or data['ai_quality'] not in PRESETS:
        raise ValueError('A mentett projekt képmérete vagy részletessége hibás.')
    if data['image_engine'] not in IMAGE_ENGINES or data['encoder'] not in (
            'Automatikus GPU / CPU', 'CPU • összes mag', 'NVIDIA GPU', 'AMD GPU', 'Intel GPU'):
        raise ValueError('A mentett projekt motorbeállítása hibás.')
    validate_image_motion(data['image_motion'])
    validate_lighting(data['lighting'])
    validate_enhancement(data['enhancement'])
    validate_framing(data['framing'])
    for name in PATH_FIELDS:
        data[name] = _local_path(data[name], base)
    if data['narration'] == 'Saját hangfájl' and not data['audio']:
        raise ValueError('Hiányzik az eredeti saját narrációs hangfájl.')
    if data['visual_mode'] == 'Saját videók' and not data['videos']:
        raise ValueError('Hiányzik az eredeti videók listája.')
    return data


def normalize_project(project, base=None):
    """Canonical safe settings used for an explicit Engine resume comparison."""
    return _project(project, Path(base).absolute() if base is not None else Path.cwd().absolute())


def _chunks(chunks):
    if (not isinstance(chunks, (list, tuple)) or not 1 <= len(chunks) <= MAX_SCENES
            or any(not isinstance(item, str) or len(item) > 20000 or '\0' in item for item in chunks)
            or sum(map(len, chunks)) > 2_000_000):
        raise ValueError('A folytatás pontos jelenetszövege hiányzik vagy hibás.')
    return tuple(chunks)


def _size(size):
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(type(v) is not int or not 2 <= v <= 16384 or v % 2 for v in size)):
        raise ValueError('A folytatás eredeti képmérete hibás.')
    return tuple(size)


def _safe(path):
    return scene_library._safe(path)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('A mentett leírás ismétlődő adatmezőt tartalmaz.')
        result[key] = value
    return result


def _read(path, missing):
    _safe(path)
    if not path.is_file():
        raise ValueError(missing)
    if not 0 < path.stat().st_size <= MAX_JSON:
        raise ValueError('A folytatás mentett leírásának mérete hibás.')
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_JSON:
            raise ValueError('A folytatás mentett leírása túl nagy.')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nem véges szám.')))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('A folytatás mentett leírása sérült.') from error


def _paths(project):
    paths = project['videos'] if project['visual_mode'] == 'Saját videók' else project['images']
    if project['narration'] == 'Saját hangfájl':
        paths = [*paths, project['audio']]
    if project['music']:
        paths = [*paths, project['music']]
    return tuple(dict.fromkeys(paths))


def _input(engine, filename):
    _check(engine)
    path = _safe(Path(filename))
    if not path.is_file():
        raise ValueError('Az eredeti helyi bemeneti fájl már nem található: ' + str(path))
    before = path.stat()
    digest = soundtrack_cache._digest(path, engine)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ValueError('Egy bemeneti fájl az ellenőrzés közben megváltozott.')
    if not 0 < after.st_size < 2**63:
        raise ValueError('Az eredeti bemeneti fájl üres vagy túl nagy.')
    return {'path': str(path), 'bytes': after.st_size, 'sha256': digest}


def _atomic_create(root, data):
    target = _safe(root / JOURNAL)
    partial = root / ('.folytatas.pending_' + uuid.uuid4().hex + '.json')
    try:
        with partial.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows rename is atomic and refuses an existing destination.
        # It also works where hard-link creation is unavailable/denied.
        # POSIX rename would replace a racing creator, so retain link there.
        try:
            if os.name == 'nt':
                os.rename(partial, target)
            else:
                os.link(partial, target)
        except FileExistsError as error:
            raise ValueError('Ebben a mappában már van folytatási napló; azt nem írjuk felül.') from error
    finally:
        partial.unlink(missing_ok=True)


def create(engine, root, project, chunks, size):
    """Publish the immutable continuation contract before the first scene."""
    _check(engine)
    root = soundtrack_cache._folder(root)
    if (root / JOURNAL).exists() or soundtrack_cache._linked(root / JOURNAL):
        raise ValueError('Ebben a mappában már van folytatási napló; azt nem írjuk felül.')
    base = Path.cwd().absolute()
    data = _project(project, base)
    chunks, size = _chunks(chunks), _size(size)
    saved = _read(root / PROJECT, 'Hiányzik a folytatáshoz szükséges projekt.json.')
    if _project(saved, base, strict=True) != data:
        raise ValueError('A projekt beállításai nem egyeznek a folytatási naplóval.')
    inputs = [_input(engine, filename) for filename in _paths(data)]
    _check(engine)
    _atomic_create(root, {'schema': SCHEMA, 'path_base': str(base), 'project': data,
                          'chunks': list(chunks), 'size': list(size), 'inputs': inputs})
    return root / JOURNAL


def prepare(engine, root):
    """Verify every old byte before a caller creates a destination or uses AI."""
    _check(engine)
    root = soundtrack_cache._folder(root)
    data = _read(root / JOURNAL,
        'Ehhez a régebbi projekthez nincs folytatási napló. Csak az újonnan készített helyi videók folytathatók.')
    if (not isinstance(data, dict) or set(data) !=
            {'schema', 'path_base', 'project', 'chunks', 'size', 'inputs'}
            or type(data['schema']) is not int or data['schema'] != SCHEMA
            or not isinstance(data['path_base'], str) or not data['path_base']
            or len(data['path_base']) > 32768 or '\0' in data['path_base']
            or not Path(data['path_base']).is_absolute()):
        raise ValueError('A folytatási napló formátuma vagy verziója hibás.')
    base = Path(data['path_base'])
    project = _project(data['project'], base, strict=True)
    if project != data['project']:
        raise ValueError('A folytatási napló nem normalizált projektet tartalmaz.')
    chunks, size = _chunks(data['chunks']), _size(data['size'])
    saved = _read(root / PROJECT, 'Hiányzik a folytatáshoz szükséges projekt.json.')
    if _project(saved, base, strict=True) != project:
        raise ValueError('Az eredeti projekt beállításai megváltoztak; a folytatás nem biztonságos.')
    inputs, paths = data['inputs'], _paths(project)
    if not isinstance(inputs, list) or len(inputs) != len(paths):
        raise ValueError('A folytatás bemeneti fájllistája hibás.')
    for filename, record in zip(paths, inputs):
        if (not isinstance(record, dict) or set(record) != {'path', 'bytes', 'sha256'}
                or record['path'] != filename or type(record['bytes']) is not int
                or not 0 < record['bytes'] < 2**63 or not isinstance(record['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise ValueError('A folytatás bemeneti fájlleírása hibás.')
        if _input(engine, filename) != record:
            raise ValueError('Az eredeti kép, videó, hang vagy zene megváltozott; a folytatás leállt.')
    library = scene_library.load(root, engine)
    if library.expected_scenes != len(chunks):
        raise ValueError('A jelenettár jelenetszáma nem egyezik az eredeti tervvel.')
    from reference_framing import SUBJECT_FRAME
    cropped=(project['format']=='Fotó eredeti aránya' and project['images']
        and project['visual_mode'] in LOCAL_MODES[:2] and project['framing']==SUBJECT_FRAME)
    final_size=library.scenes[0].size if library.scenes else size
    if not cropped and final_size!=size:
        raise ValueError('A megtartott jelenetek szövege vagy képmérete megváltozott.')
    for index, scene in enumerate(library.scenes):
        _check(engine)
        if scene.label != chunks[index] or scene.size != final_size:
            raise ValueError('A megtartott jelenetek szövege vagy képmérete megváltozott.')
        scene_library._decode(engine, scene.video, scene.frames)
        from ytmuhely import ffmpeg
        details=engine.run([ffmpeg(),'-hide_banner','-nostdin','-v','info',
            '-protocol_whitelist','file,pipe','-i',str(scene.video),'-map','0:V:0',
            '-an','-vf','showinfo','-frames:v','1','-f','null','-'],timeout=30)
        dimensions=re.search(r'\bn:\s*0\s[^\r\n]*?\bs:(\d+)x(\d+)\b',details)
        if not dimensions or tuple(map(int,dimensions.groups()))!=scene.size:
            raise ValueError('A megtartott jelenet tényleges képmérete eltér a mentett adattól.')
    if library.status == 'complete':
        scene_library._decode(engine, root / 'video.mp4', sum(scene.frames for scene in library.scenes))
    _check(engine)
    return ResumeState(root, project, chunks, size, library)


def resume(engine, root):
    """Continue through the same Engine path used by the command line and UI."""
    from ytmuhely import Project
    state = prepare(engine, root)
    return engine.build(Project(**state.project), resume_folder=state.root)
