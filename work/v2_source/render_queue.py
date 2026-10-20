"""Durable, explicitly started queue for free local video projects.

The queue stores settings and file names, never an engine or credentials. Opening
it only recovers an interrupted status; it never starts a renderer. One lifetime
file lock protects it from a second application process.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import uuid


SCHEMA = 1
MAX_JOBS = 1000
MAX_BYTES = 32 * 1024 * 1024
REPLACE_ATTEMPTS = 10
STATUSES = frozenset(('pending', 'running', 'complete', 'failed', 'cancelled', 'interrupted'))
RETRYABLE = frozenset(('failed', 'cancelled', 'interrupted'))
JOB_FIELDS = frozenset(('id', 'title', 'project', 'status', 'progress', 'message',
                        'result', 'error', 'created_at', 'updated_at'))


class QueueError(RuntimeError):
    """A queue is busy, closed, or could not be read safely."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('A munkasor ismétlődő adatmezőt tartalmaz.')
        result[name] = value
    return result


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _keep_awake(enabled):
    """A thread-scoped Windows request; the display may still turn off."""
    if os.name != 'nt':
        return True
    import ctypes
    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    set_state.argtypes = [ctypes.c_uint]
    set_state.restype = ctypes.c_uint
    return bool(set_state(0x80000000 | (0x00000001 if enabled else 0)))


def _replace_atomic(temporary, destination):
    # A normal Windows reader briefly holds a handle without FILE_SHARE_DELETE.
    # Keep the old JSON intact while that reader closes; never replace via delete.
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, destination)
            return
        except OSError as error:
            if (getattr(error, 'winerror', None) not in (5, 32, 33)
                    or attempt == REPLACE_ATTEMPTS - 1):
                raise
            time.sleep(min(.01 * (2 ** attempt), .2))


def _project(project, base=None, *, check_files=False):
    # Imports are deliberately lazy: importing this module starts no UI/runtime.
    from dataclasses import asdict, is_dataclass
    from local_resume import normalize_project
    # Frozen entry points define __main__.Project separately from ytmuhely.Project.
    # The exact field allowlist is still enforced by normalize_project below.
    if is_dataclass(project) and not isinstance(project, type):
        project = asdict(project)
    data = normalize_project(project, base=base)
    if data['visual_mode'].startswith('AI-videó'):
        try:
            address = urlsplit(data['comfy_url'])
            valid = (address.scheme in ('http', 'https')
                     and address.hostname in ('localhost', '127.0.0.1', '::1')
                     and address.username is None and address.password is None
                     and not address.query and not address.fragment)
            address.port  # Reject malformed ports before a worker is started.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('A munkasor csak a saját gépen futó ComfyUI-t használhatja.')
    if not data['output']:
        data['output'] = str(Path(base or Path.cwd()).absolute())
    if check_files:
        mode = data['visual_mode']
        if mode.startswith('AI-videó') and not (
                data['text'].strip() or data['images'] or data['video_prompt'].strip()):
            raise ValueError('Adj meg szöveget, jelenetleírást vagy képet a helyi AI-videóhoz.')
        if mode == 'Képes diavideó' and not data['text'].strip() and not data['images']:
            raise ValueError('Adj meg szöveget vagy képet a diavideóhoz.')
        files = data['videos'] if mode == 'Saját videók' else data['images']
        if data['narration'] == 'Saját hangfájl':
            files = [*files, data['audio']]
        if data['music']:
            files = [*files, data['music']]
        for filename in files:
            path = Path(filename)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('A munkasor bemeneti fájlja hiányzik vagy üres: ' + str(path))
        output = Path(data['output'])
        if output.exists() and not output.is_dir():
            raise ValueError('A videók mentési helye egy mappa legyen.')
    return data


class QueueStore:
    """Transactional JSON queue. Call close() when its owning window closes."""

    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self._lock = threading.RLock()
        self._closed = False
        self._runner = None
        self._jobs = []
        self._lock_file = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._acquire_file_lock()
            self._jobs = self._read()
            recovered = deepcopy(self._jobs)
            for job in recovered:
                if job['status'] == 'running':
                    job.update(status='interrupted', message='A program bezárult. Kézzel újraindítható.',
                               error='', result='', updated_at=_now())
            if recovered != self._jobs:
                self._commit(recovered)
        except BaseException:
            self._release_file_lock()
            self._closed = True
            raise

    def _acquire_file_lock(self):
        lock_path = self.path.with_name(self.path.name + '.lock')
        if self.path.is_symlink() or lock_path.is_symlink():
            raise QueueError('A munkasor helyén közvetlen helyi fájl szükséges.')
        handle = open(lock_path, 'a+b')
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise QueueError('Ez a munkasor egy másik programablakban már nyitva van.') from error
        self._lock_file = handle

    def _release_file_lock(self):
        handle, self._lock_file = self._lock_file, None
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _assert_open(self):
        if self._closed:
            raise QueueError('A munkasor már be van zárva.')

    def _read(self):
        if not self.path.exists():
            return []
        try:
            if not self.path.is_file() or not 0 < self.path.stat().st_size <= MAX_BYTES:
                raise ValueError('Hibás fájlméret.')
            raw = self.path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ValueError('Túl nagy fájl.')
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Hibás szám.')))
            if (not isinstance(data, dict) or set(data) != {'schema', 'jobs'}
                    or type(data['schema']) is not int or data['schema'] != SCHEMA
                    or not isinstance(data['jobs'], list) or len(data['jobs']) > MAX_JOBS):
                raise ValueError('Ismeretlen munkasor-formátum.')
            identifiers = set()
            from dataclasses import asdict
            from ytmuhely import Project
            project_fields = set(asdict(Project()))
            for job in data['jobs']:
                if not isinstance(job, dict) or set(job) != JOB_FIELDS:
                    raise ValueError('Hibás munkaleírás.')
                identifier = job['id']
                if (not isinstance(identifier, str) or len(identifier) != 32
                        or any(char not in '0123456789abcdef' for char in identifier)
                        or identifier in identifiers):
                    raise ValueError('Hibás munkaazonosító.')
                identifiers.add(identifier)
                if (job['status'] not in STATUSES
                        or type(job['progress']) not in (int, float)
                        or not math.isfinite(job['progress']) or not 0 <= job['progress'] <= 100
                        or not _timestamp(job['created_at']) or not _timestamp(job['updated_at'])):
                    raise ValueError('Hibás munkaállapot.')
                for name in ('title', 'message', 'result', 'error'):
                    if (not isinstance(job[name], str) or '\0' in job[name]
                            or len(job[name]) > (32768 if name == 'result' else 4000)):
                        raise ValueError('Hibás munkaszöveg.')
                if (not isinstance(job['project'], dict) or set(job['project']) != project_fields
                        or _project(job['project'], self.path.parent) != job['project']
                        or job['title'] != job['project']['title']):
                    raise ValueError('Hibás mentett projekt.')
            return data['jobs']
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            raise QueueError('A mentett munkasor sérült vagy nem támogatott. Az eredeti fájlt megőriztem.') from error

    def _write_atomic(self, jobs):
        raw = json.dumps({'schema': SCHEMA, 'jobs': jobs}, ensure_ascii=False,
                         indent=2, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise ValueError('A munkasor elérte a menthető méretet.')
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with open(temporary, 'xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_atomic(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _commit(self, jobs):
        self._write_atomic(jobs)
        self._jobs = jobs

    def _index(self, identifier):
        for index, job in enumerate(self._jobs):
            if job['id'] == identifier:
                return index
        raise ValueError('Ez a feladat már nincs a munkasorban.')

    def items(self):
        with self._lock:
            self._assert_open()
            return deepcopy(self._jobs)

    def add(self, project, base=None):
        data = _project(project, base=base, check_files=True)
        if len(data['title']) > 4000:
            raise ValueError('A videó címe túl hosszú.')
        timestamp = _now()
        item = dict(id=uuid.uuid4().hex, title=data['title'], project=data,
                    status='pending', progress=0, message='Várakozik', result='', error='',
                    created_at=timestamp, updated_at=timestamp)
        with self._lock:
            self._assert_open()
            if len(self._jobs) >= MAX_JOBS:
                raise ValueError('Legfeljebb 1000 feladat lehet a munkasorban.')
            self._commit([*deepcopy(self._jobs), item])
            return deepcopy(item)

    def remove(self, identifier):
        with self._lock:
            self._assert_open()
            index = self._index(identifier)
            if self._jobs[index]['status'] == 'running':
                raise ValueError('A készülő feladat előbb álljon le.')
            jobs = deepcopy(self._jobs)
            jobs.pop(index)
            self._commit(jobs)

    def move(self, identifier, delta):
        if type(delta) is not int or delta not in (-1, 1):
            raise ValueError('Egyszerre egy hellyel mozgatható a feladat.')
        with self._lock:
            self._assert_open()
            index = self._index(identifier)
            target = index + delta
            if self._jobs[index]['status'] == 'running':
                raise ValueError('A készülő feladat nem mozgatható.')
            if target < 0 or target >= len(self._jobs):
                return
            if self._jobs[target]['status'] == 'running':
                raise ValueError('A készülő feladat sorrendje már rögzített.')
            jobs = deepcopy(self._jobs)
            jobs[index], jobs[target] = jobs[target], jobs[index]
            self._commit(jobs)

    def retry(self, identifier):
        with self._lock:
            self._assert_open()
            index = self._index(identifier)
            if self._jobs[index]['status'] not in RETRYABLE:
                raise ValueError('Csak hibás, megszakított vagy félbemaradt munka indítható újra.')
            jobs = deepcopy(self._jobs)
            jobs[index].update(status='pending', progress=0, message='Várakozik',
                               error='', result='', updated_at=_now())
            self._commit(jobs)
            return deepcopy(jobs[index])

    def _update(self, identifier, **changes):
        with self._lock:
            self._assert_open()
            index = self._index(identifier)
            if all(self._jobs[index][name] == value for name, value in changes.items()):
                return deepcopy(self._jobs[index])
            jobs = deepcopy(self._jobs)
            jobs[index].update(changes, updated_at=_now())
            self._commit(jobs)
            return deepcopy(jobs[index])

    def close(self):
        with self._lock:
            if self._closed:
                return
            if self._runner is not None:
                raise QueueError('Előbb állítsd le a futó munkasort.')
            self._closed = True
            self._release_file_lock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class QueueRunner:
    """One background worker; failures do not prevent the next pending job."""

    def __init__(self, store, engine_factory, on_event=None):
        self.store = store
        self.engine_factory = engine_factory
        self.on_event = on_event or (lambda kind, data: None)
        self._thread = None
        self._active_engine = None
        self._running = False
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self.last_error = ''

    @property
    def is_running(self):
        with self.store._lock:
            return self._running

    @property
    def active_engine(self):
        with self.store._lock:
            return self._active_engine

    def snapshot(self):
        return self.store.items()

    def _emit(self, kind, data):
        # Notification failures must never change a successfully rendered job.
        try:
            self.on_event(kind, deepcopy(data))
        except Exception:
            pass

    def start(self):
        with self.store._lock:
            self.store._assert_open()
            if self._running:
                return False
            if self.store._runner is not None:
                raise QueueError('Ehhez a munkasorhoz már tartozik futó feldolgozó.')
            self._stop.clear()
            self._cancel.clear()
            self.last_error = ''
            self._running = True
            self.store._runner = self
            self._thread = threading.Thread(target=self._run, name='Helyi video-munkasor', daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._running = False
                self.store._runner = None
                self._thread = None
                raise
            return True

    def stop_after_current(self):
        with self.store._lock:
            self._stop.set()

    def cancel_current(self):
        with self.store._lock:
            self._stop.set()
            self._cancel.set()
            if self._active_engine is not None:
                self._active_engine.cancel.set()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return not self.is_running

    def _log(self, identifier, message):
        message = str(message).replace('\0', '')[-4000:]
        self.store._update(identifier, message=message)
        self._emit('log', {'id': identifier, 'message': message})

    def _progress(self, identifier, progress):
        if type(progress) not in (float, int) or not math.isfinite(progress):
            return
        progress = round(max(0, min(100, progress)), 1)
        self.store._update(identifier, progress=progress)
        self._emit('progress', {'id': identifier, 'progress': progress})

    def _run(self):
        try:
            try:
                awake = _keep_awake(True)
            except Exception:
                awake = False
            if not awake:
                self._emit('log', {'id': '', 'message': 'Az automatikus alvást nem sikerült ideiglenesen tiltani. Ellenőrizd a gép energiabeállítását.'})
            while True:
                with self.store._lock:
                    if self._stop.is_set():
                        break
                    pending = next((job for job in self.store._jobs if job['status'] == 'pending'), None)
                    if pending is None:
                        break
                    identifier = pending['id']
                    job = self.store._update(identifier, status='running', progress=0,
                                             message='Videó készül…', error='', result='')
                self._emit('job', job)
                engine = None
                try:
                    # Input files may have changed while waiting overnight.
                    settings = _project(job['project'], check_files=True)
                    engine = self.engine_factory(lambda text: self._log(identifier, text),
                                                 lambda value: self._progress(identifier, value))
                    with self.store._lock:
                        self._active_engine = engine
                        if self._cancel.is_set():
                            engine.cancel.set()
                    from ytmuhely import Project
                    folder = Path(engine.build(Project(**settings))).absolute()
                    if engine.cancel.is_set():
                        raise RuntimeError('Megszakítva.')
                    video = folder / 'video.mp4'
                    if not video.is_file() or video.stat().st_size == 0:
                        raise ValueError('A feldolgozó nem készített video.mp4 kimenetet.')
                    job = self.store._update(identifier, status='complete', progress=100,
                                             message='Elkészült', result=str(video), error='')
                except Exception as error:
                    cancelled = self._cancel.is_set() or (engine is not None and engine.cancel.is_set())
                    if cancelled:
                        self._stop.set()
                    message = str(error).replace('\0', '')[-4000:] or type(error).__name__
                    job = self.store._update(identifier, status='cancelled' if cancelled else 'failed',
                                             message='Megszakítva' if cancelled else 'Hiba',
                                             result='', error='' if cancelled else message)
                finally:
                    with self.store._lock:
                        self._active_engine = None
                self._emit('job', job)
        except Exception as error:
            self.last_error = str(error).replace('\0', '')[-4000:] or type(error).__name__
            self._emit('error', {'message': self.last_error})
        finally:
            try:
                if not _keep_awake(False):
                    self._emit('log', {'id': '', 'message': 'Az ideiglenes ébren tartás visszaállítása nem sikerült.'})
            except Exception:
                self._emit('log', {'id': '', 'message': 'Az ideiglenes ébren tartás visszaállítása nem sikerült.'})
            with self.store._lock:
                self._active_engine = None
                self._running = False
                self.store._runner = None
            self._emit('done', {'stopped': self._stop.is_set(), 'error': self.last_error})
