"""Optional xAI video transport. No local model imports and no implicit POST retry.

Only the user's explicit invocation of ``generate`` can submit a paid request.
``resume_existing`` polls/downloads the saved request and cannot submit one.
This module checks MP4 framing only; the caller must decode/validate with FFmpeg
before using a downloaded file in the final video.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings

MODEL = 'grok-imagine-video-1.5'
LABEL = 'Grok Imagine • internet'
API_BASE = 'https://api.x.ai/v1'
# The official REST example uses this host. Unknown CDN hosts fail closed and
# leave the saved job available for a later client update, without a new POST.
MEDIA_HOSTS = frozenset(('vidgen.x.ai',))
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # Our client limit, not a claimed I2V API limit.
MAX_IMAGE_PIXELS = 40_000_000
MAX_JSON_BYTES = 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
REMOTE_CONTINUES = ('A várakozás leállt. A felhős generálás tovább futhat és '
                    'számlázható; a mentett feladat folytatható új generálás nélkül.')
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')


class GrokError(RuntimeError):
    """A sanitized user-facing error; server bodies are never copied here."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _key(value):
    if not isinstance(value, str):
        raise ValueError('Add meg a saját xAI API-kulcsodat.')
    value = value.strip()
    if not 8 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('Az xAI API-kulcs formátuma érvénytelen.')
    return value


def _settings(prompt, duration, resolution):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise ValueError('Adj meg 1–16000 karakter hosszú angol jelenetleírást.')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError('A Grok-jelenet hossza 1–15 másodperc lehet.')
    if not math.isfinite(duration) or not 1 <= duration <= 15:
        raise ValueError('A Grok-jelenet hossza 1–15 másodperc lehet.')
    if resolution not in ('720p', '1080p'):
        raise ValueError('A Grok felbontása 720p vagy 1080p lehet.')
    return prompt.strip(), math.ceil(duration), resolution


def _timings(timeout, poll_interval):
    for value in (timeout, poll_interval):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('A várakozás időkorlátja és ellenőrzési köze legyen pozitív.')
    if timeout > 3600 or poll_interval > 60:
        raise ValueError('Legfeljebb 3600 másodperces várakozás és 60 másodperces ellenőrzési köz adható meg.')


def _photo(image):
    """Normalize orientation and send the entire image, never a separate face."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    path = Path(image)
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError('A referenciafotó üres, hiányzik vagy nagyobb a program 20 MiB-os korlátjánál.')
        with path.open('rb') as stream:
            source = stream.read(MAX_IMAGE_BYTES + 1)
        if len(source) > MAX_IMAGE_BYTES:
            raise ValueError('A referenciafotó nagyobb a program 20 MiB-os korlátjánál.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as opened:
                if opened.width * opened.height > MAX_IMAGE_PIXELS:
                    raise ValueError('A referenciafotó meghaladja a program 40 megapixeles korlátját.')
                if getattr(opened, 'is_animated', False):
                    raise ValueError('Egyetlen álló referenciafotót adj meg.')
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                if 'A' in oriented.getbands() or 'transparency' in oriented.info:
                    rgba = oriented.convert('RGBA')
                    rgb = Image.new('RGB', rgba.size, 'white')
                    rgb.paste(rgba, mask=rgba.getchannel('A'))
                else:
                    rgb = oriented.convert('RGB')
                target = io.BytesIO()
                rgb.save(target, format='JPEG', quality=95, subsampling=0)
                encoded = target.getvalue()
                size = list(rgb.size)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('A referenciafotó nem olvasható vagy sérült.') from None
    if not 0 < len(encoded) <= MAX_IMAGE_BYTES:
        raise ValueError('Az előkészített referenciafotó nagyobb a program 20 MiB-os korlátjánál.')
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'), {
        'uploaded_image_sha256': hashlib.sha256(encoded).hexdigest(),
        'uploaded_image_size': size,
        'uploaded_image_bytes': len(encoded),
        'full_photo': True,
        'face_replacement': False,
    }


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save(path, state, **updates):
    state.update(updates, updated_at=_now())
    _atomic_json(path, state)


@contextlib.contextmanager
def _job_lock(path):
    """OS lock releases on crashes; an old .lock file never blocks recovery."""
    lock = Path(path).with_name(Path(path).name + '.lock')
    with lock.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise GrokError('Ezt a felhős feladatot már egy másik folyamat kezeli.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _load(path):
    try:
        if Path(path).stat().st_size > MAX_JSON_BYTES:
            raise ValueError
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(state, dict) or state.get('schema') != 1 or state.get('backend') != 'xai':
            raise ValueError
        if state.get('model') != MODEL or not _HASH.fullmatch(state.get('request_hash', '')):
            raise ValueError
        if type(state.get('duration')) is not int or not 1 <= state['duration'] <= 15:
            raise ValueError
        if state.get('resolution') not in ('720p', '1080p'):
            raise ValueError
        request_id = state.get('request_id')
        if request_id is not None and (not isinstance(request_id, str) or not _ID.fullmatch(request_id)):
            raise ValueError
        return state
    except (OSError, ValueError, TypeError):
        raise GrokError('A mentett Grok-feladat nem olvasható vagy sérült. Nem indult új generálás.') from None


def _checked(engine, job_file, state):
    try:
        engine.check()
    except BaseException:
        _save(job_file, state, local_status='waiting_stopped')
        engine.log(REMOTE_CONTINUES)
        raise


def _media_url(value):
    if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise GrokError('A Grok nem adott érvényes videóletöltési címet. A feladat mentve maradt.')
    try:
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme != 'https' or parts.hostname not in MEDIA_HOSTS or
                parts.port not in (None, 443) or parts.username is not None or
                parts.password is not None or parts.fragment or not parts.path):
            raise ValueError
    except ValueError:
        raise GrokError('A videó nem az engedélyezett HTTPS xAI-tárhelyen van. A feladat mentve maradt; nem indult új generálás.') from None
    return value


class _Client:
    def __init__(self, opener=None):
        self._open = opener or urllib.request.build_opener(_NoRedirect()).open

    def api(self, method, suffix, key, payload=None, timeout=30):
        # Internal suffixes only: callers cannot supply a foreign API URL.
        if not (method == 'POST' and suffix == '/videos/generations' or
                method == 'GET' and suffix.startswith('/videos/') and _ID.fullmatch(suffix[8:])):
            raise GrokError('Érvénytelen Grok API-művelet.')
        encoded = None if payload is None else json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(API_BASE + suffix, data=encoded, method=method,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'User-Agent': 'YouTubeMuhely/optional-xai'})
        try:
            with self._open(request, timeout=timeout) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise GrokError('A Grok API HTTP %d hibát jelzett. Nem történt automatikus új generálás.' % status)
                raw = response.read(MAX_JSON_BYTES + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise GrokError('A Grok API HTTP %d hibát jelzett. Nem történt automatikus új generálás.' % code) from None
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            raise GrokError('A Grok API nem érhető el vagy túllépte az időkorlátot. Nem történt automatikus új generálás.') from None
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise GrokError('A Grok API üres vagy túl nagy választ küldött. Nem indult új generálás.')
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeDecodeError):
            raise GrokError('A Grok API válasza nem olvasható. Nem indult új generálás.') from None

    def download(self, url, output, engine, checked, deadline):
        url = _media_url(url)
        temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.part')
        try:
            for redirect in range(4):
                checked()
                if time.monotonic() >= deadline:
                    raise GrokError('A videó letöltése túllépte az időkorlátot. A feladat új generálás nélkül folytatható.')
                request = urllib.request.Request(url, headers={'Accept': 'video/mp4,application/octet-stream',
                    'User-Agent': 'YouTubeMuhely/optional-xai'})
                try:
                    response = self._open(request, timeout=max(.1, min(30, deadline - time.monotonic())))
                except urllib.error.HTTPError as error:
                    if error.code in (301, 302, 303, 307, 308):
                        location = error.headers.get('Location')
                        error.close()
                        if not location or redirect == 3:
                            raise GrokError('A videó letöltésének átirányítása nem követhető. A feladat mentve maradt.') from None
                        url = _media_url(urllib.parse.urljoin(url, location))
                        continue
                    error.close()
                    raise GrokError('A videó letöltése HTTP-hiba miatt megszakadt. A mentett feladat folytatható.') from None
                with response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get('Location')
                        if not location or redirect == 3:
                            raise GrokError('A videó letöltésének átirányítása nem követhető. A feladat mentve maradt.')
                        url = _media_url(urllib.parse.urljoin(url, location))
                        continue
                    if response.status != 200:
                        raise GrokError('A videótárhely nem adott teljes fájlt. A mentett feladat folytatható.')
                    media_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                    if media_type not in ('video/mp4', 'application/octet-stream'):
                        raise GrokError('A videótárhely válasza nem MP4-fájl. A mentett feladat folytatható.')
                    length = response.headers.get('Content-Length')
                    try:
                        expected = None if length is None else int(length)
                        if expected is not None and not 32 <= expected <= MAX_VIDEO_BYTES:
                            raise ValueError
                    except (ValueError, TypeError):
                        raise GrokError('A videófájl mérete üres, érvénytelen vagy túl nagy.') from None
                    digest = hashlib.sha256()
                    count = 0
                    with temporary.open('xb') as stream:
                        while True:
                            checked()
                            if time.monotonic() >= deadline:
                                raise GrokError('A videó letöltése túllépte az időkorlátot. A mentett feladat folytatható.')
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            count += len(chunk)
                            if count > MAX_VIDEO_BYTES:
                                raise GrokError('A videófájl nagyobb a program 512 MiB-os korlátjánál.')
                            stream.write(chunk)
                            digest.update(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if count < 32 or expected is not None and count != expected:
                        raise GrokError('A videó letöltése hiányos vagy üres. A mentett feladat folytatható.')
                    with temporary.open('rb') as stream:
                        header = stream.read(32)
                    if header[4:8] != b'ftyp':
                        raise GrokError('A letöltött fájl nem felismerhető MP4-videó.')
                    checked()
                    os.replace(temporary, output)
                    return {'download_sha256': digest.hexdigest(), 'download_bytes': count,
                            'download_file': output.name, 'verification': 'mp4-header-only; caller must decode'}
            raise GrokError('A videó letöltésének túl sok átirányítása volt.')
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            raise GrokError('A videó letöltése hálózati vagy fájlhiba miatt megszakadt. A mentett feladat folytatható.') from None
        finally:
            temporary.unlink(missing_ok=True)


def _cached_output(job_file, state):
    name = state.get('download_file')
    # Do not trust a saved file's path, absolute paths, or traversal components.
    expected = Path(job_file).stem + '_video.mp4'
    if name != expected:
        raise GrokError('A mentett videófájl neve érvénytelen. Nem indult új generálás.')
    output = Path(job_file).parent / expected
    digest = hashlib.sha256()
    try:
        size = output.stat().st_size
        if not 32 <= size <= MAX_VIDEO_BYTES or size != state.get('download_bytes'):
            raise ValueError
        with output.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != state.get('download_sha256'):
            raise ValueError
        return output
    except (OSError, ValueError):
        return None


def _wait(engine, job_file, state, key, client, timeout, poll_interval):
    request_id = state.get('request_id')
    if not request_id:
        raise GrokError('A korábbi beküldés eredménye bizonytalan, nincs mentett feladatazonosító. Nem indult új generálás; ellenőrizd az xAI-fiók feladatait.')
    if request_id == key or key in request_id:
        raise GrokError('A mentett Grok-feladatazonosító érvénytelen.')
    if state.get('status') == 'downloaded':
        output = _cached_output(job_file, state)
        if output is not None:
            engine.check()
            return output, dict(state)
    if state.get('status') in ('failed', 'expired', 'unknown_status'):
        raise GrokError('A mentett Grok-feladat sikertelen, lejárt vagy ismeretlen állapotú. Nem indult új generálás.')
    deadline = time.monotonic() + timeout
    checked = lambda: _checked(engine, job_file, state)
    while True:
        checked()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _save(job_file, state, local_status='timed_out')
            raise GrokError('A Grok-várakozás túllépte az időkorlátot. ' + REMOTE_CONTINUES)
        result = client.api('GET', '/videos/' + request_id, key, timeout=min(30, remaining))
        status = result.get('status')
        if status not in ('pending', 'done', 'failed', 'expired'):
            _save(job_file, state, status='unknown_status', local_status='invalid_response')
            raise GrokError('A Grok ismeretlen vagy hibás állapotot küldött. A feladat mentve maradt; nem indult új generálás.')
        _save(job_file, state, status=status, local_status='polling')
        if status in ('failed', 'expired'):
            raise GrokError('A Grok videógenerálása sikertelen vagy lejárt. Nem történt automatikus új generálás.')
        if status == 'done':
            video = result.get('video')
            if not isinstance(video, dict) or video.get('respect_moderation') is not True:
                _save(job_file, state, local_status='moderation_not_approved')
                raise GrokError('A Grok nem adott engedélyezett videóeredményt. A feladat mentve maradt.')
            url = _media_url(video.get('url'))
            output = Path(job_file).with_name(Path(job_file).stem + '_video.mp4')
            engine.log('A Grok-jelenet elkészült; a videót a gépre mentem.')
            downloaded = client.download(url, output, engine, checked, deadline)
            _save(job_file, state, status='downloaded', local_status='completed', **downloaded)
            return output, dict(state)
        engine.log('A Grok még készíti a jelenetet. A feladat mentve van.')
        until = min(deadline, time.monotonic() + poll_interval)
        while time.monotonic() < until:
            checked()
            time.sleep(min(.2, max(0, until - time.monotonic())))


def generate(engine, prompt, image, duration, resolution, job_file, key, *,
             aspect_ratio='16:9', timeout=600, poll_interval=5, _opener=None):
    """Submit once, or resume the exact previously saved job.

    Return ``(local_mp4_path, key_free_manifest)``. A preexisting job with any
    uncertain submission state blocks a new POST. A changed request requires
    an explicit, different job filename chosen by the caller.
    """
    key = _key(key)
    prompt, duration, resolution = _settings(prompt, duration, resolution)
    if aspect_ratio not in ('16:9', '9:16', '1:1', '4:3', '3:4', '3:2', '2:3', '21:9', '5:2'):
        raise ValueError('A Grok képaránya érvénytelen.')
    _timings(timeout, poll_interval)
    engine.check()
    payload = {'model': MODEL, 'prompt': prompt,
               'duration': duration, 'resolution': resolution, 'generate_audio': False}
    if image is None:
        photo = {'image_input': False, 'aspect_ratio': aspect_ratio}
        payload['aspect_ratio'] = aspect_ratio
    else:
        data_url, photo = _photo(image)
        photo['image_input'] = True
        payload['image'] = {'url': data_url}
    request_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    job_file = Path(job_file).resolve()
    job_file.parent.mkdir(parents=True, exist_ok=True)
    client = _Client(_opener)
    with _job_lock(job_file):
        if job_file.exists():
            state = _load(job_file)
            if state['request_hash'] != request_hash:
                raise GrokError('Ez a feladatfájl másik jelenethez tartozik. Nem indult új generálás.')
            return _wait(engine, job_file, state, key, client, timeout, poll_interval)
        engine.check()
        state = {'schema': 1, 'backend': 'xai', 'model': MODEL,
                 'request_hash': request_hash, 'status': 'submitting', 'request_id': None,
                 'created_at': _now(), 'updated_at': _now(), 'local_status': 'submitting',
                 'duration': duration, 'resolution': resolution, 'generate_audio': False,
                 **photo}
        _atomic_json(job_file, state)  # Durable before the only POST attempt.
        engine.log('A jelenetet és a teljes referenciafotót elküldöm a kiválasztott Grok-motornak.'
                   if image is not None else 'A jelenetleírást elküldöm a kiválasztott Grok-motornak.')
        try:
            result = client.api('POST', '/videos/generations', key, payload)
            request_id = result.get('request_id')
            if (not isinstance(request_id, str) or not _ID.fullmatch(request_id) or
                    request_id == key or key in request_id):
                raise GrokError('A Grok nem adott menthető feladatazonosítót. A beküldés eredménye bizonytalan; nem történt újrapróbálkozás.')
        except BaseException:
            _save(job_file, state, status='submission_unknown', local_status='submission_unknown')
            raise
        _save(job_file, state, request_id=request_id, status='pending', local_status='polling')
        return _wait(engine, job_file, state, key, client, timeout, poll_interval)


def validate_options(prompt, duration, resolution, api_key):
    """Validate before any external side effect; never return/store the key."""
    _key(api_key)
    return _settings(prompt, duration, resolution)


def preflight_image(image):
    """Pure local validation with the same complete-photo preparation rules."""
    _data_url, metadata = _photo(image)
    return metadata


def expected_duration(job_file):
    """Read validated local job metadata, without an API request or credential."""
    return _load(Path(job_file))['duration']


def generate_scene(engine, image, prompt, duration, resolution, folder, api_key, **kwargs):
    """Engine routing adapter; the durable job and MP4 live in ``folder``."""
    output, _manifest = generate(engine, prompt, image, duration, resolution,
        Path(folder) / 'grok_job.json', api_key, **kwargs)
    return output


def resume_existing(engine, job_file, key, *, timeout=600, poll_interval=5, _opener=None):
    """GET/download only. This function has no path to POST generation."""
    key = _key(key)
    _timings(timeout, poll_interval)
    engine.check()
    job_file = Path(job_file).resolve()
    if not job_file.is_file():
        raise GrokError('Nincs mentett Grok-feladat. Nem indult új generálás.')
    with _job_lock(job_file):
        state = _load(job_file)
        output, _manifest = _wait(engine, job_file, state, key, _Client(_opener), timeout, poll_interval)
        return output
