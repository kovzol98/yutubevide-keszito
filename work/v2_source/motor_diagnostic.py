"""Bounded startup check for the packaged local AI server, without rendering."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import marshal
import math
import os
from pathlib import Path
import re
import sys
import time


def _safe_text(value):
    # Proxy credentials and URLs must never enter a diagnostic report.
    value = re.sub(r"(?i)\b(?:https?|socks5h?)://[^\s<>\"']+", '[kapcsolat]', str(value))
    value = re.sub(r'(?i)((?:api[_-]?key|authorization|password|token)\s*[:=]\s*)\S+',
                   r'\1[elrejtve]', value)
    return value[-3000:]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def diagnose(request_file):
    """Start an owned server, inspect its API, then stop that exact server.

    The optional probe_timeout controls only post-start API calls. The normal
    startup policy remains unchanged, so this entry point can reproduce a
    packaged application's startup failure without a model or user photograph.
    """
    import comfy_video
    import realistic_video
    import ytmuhely
    import traceback
    import urllib.request

    request_file = Path(request_file).expanduser().resolve()
    request = json.loads(request_file.read_text(encoding='utf-8-sig'))
    if not isinstance(request, dict):
        raise ValueError('Az ellenőrzés beállításai JSON-objektumot igényelnek.')
    root = Path(request['ai_root']).expanduser().resolve()
    report_path = Path(request['report']).expanduser()
    if not report_path.is_absolute():
        report_path = request_file.parent / report_path
    report_path = report_path.resolve()
    timeout = float(request.get('probe_timeout', 10))
    if not math.isfinite(timeout) or not 1 <= timeout <= 30:
        raise ValueError('Az ellenőrzési időkorlát 1 és 30 másodperc közé essen.')
    if not root.is_dir():
        raise ValueError('Az AI-motor mappája nem található.')

    from windows_support import system_info
    started = time.monotonic()
    messages, requests = [], []
    report = dict(schema=1, status='RUNNING', system=system_info(),
        started_utc=datetime.now(timezone.utc).isoformat(),
        frozen=bool(getattr(sys, 'frozen', False)),
        app_release=ytmuhely.APP_RELEASE,
        executable=str(Path(sys.executable).resolve()),
        ai_root=str(root), post_start_probe_timeout_seconds=timeout,
        generation_requested=False, user_media_read=False,
        proxy_environment_present={name: bool(os.environ.get(name)) for name in
            ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')},
        source_sha256={}, request_events=requests, messages=messages)
    if report['frozen']:
        report['executable_sha256'] = _sha256(sys.executable)
    for module in (ytmuhely, comfy_video, realistic_video, sys.modules[__name__]):
        source = Path(module.__file__)
        if source.is_file():
            report['source_sha256'][source.name] = _sha256(source)
    report['startup_code_sha256'] = hashlib.sha256(
        marshal.dumps(comfy_video._ensure_server.__code__)).hexdigest()
    # Összehasonlítás hálózati kérés nélkül: a régi általános opener már a
    # létrehozáskor tanúsítványhibába ütközhetett a Windows EXE-ben.
    try:
        urllib.request.build_opener()
        report['default_opener_check'] = dict(status='PASS')
    except Exception as error:
        report['default_opener_check'] = dict(status='ERROR', error_type=type(error).__name__,
            error=_safe_text(error), traceback=_safe_text(traceback.format_exc()))

    def log(message):
        messages.append(dict(elapsed_seconds=round(time.monotonic()-started, 3),
                             text=_safe_text(message)))

    engine = ytmuhely.Engine(log=log)
    if request.get('profile_imports') is True:
        engine._comfy_profile_imports = True
        report['profile_imports'] = True
    # A final deadline also bounds future startup-policy changes. Each network
    # operation retains its own timeout, and cleanup has bounded process waits.
    normal_check = engine.check
    deadline = started + 300

    def check():
        normal_check()
        if time.monotonic() >= deadline:
            raise TimeoutError('Az AI-motor indítási ellenőrzése túllépte az öt percet.')

    engine.check = check
    original_request = comfy_video._request
    owned_process = [None]

    def observed_request(base, route, payload=None, timeout=15):
        # Observe the actual production transport; do not alter readiness calls.
        process = getattr(engine, '_comfy_process', None)
        if process is not None and owned_process[0] is None:
            owned_process[0] = process
            report['owned_process_id'] = process.pid
        event = dict(route=route, timeout_seconds=timeout,
                     started_seconds=round(time.monotonic()-started, 3))
        tick = time.monotonic()
        requests.append(event)
        try:
            result = original_request(base, route, payload, timeout=timeout)
            event['status'] = 'OK'
            if isinstance(result, dict):
                event['response_keys'] = sorted(result)
            return result
        except BaseException as error:
            event.update(status='ERROR', error_type=type(error).__name__,
                         error=_safe_text(error))
            raise
        finally:
            event['elapsed_seconds'] = round(time.monotonic()-tick, 3)

    comfy_video._request = observed_request
    try:
        base = comfy_video._ensure_server(engine, root,
            model_names=realistic_video.MODELS,
            required_extra=realistic_video.EXTRA_NODES, force_owned=True)
        report['ready_after_seconds'] = round(time.monotonic()-started, 3)
        process = getattr(engine, '_comfy_process', None)
        if process is None or process.poll() is not None:
            raise RuntimeError('Az ellenőrzés saját AI-motorja nem fut.')
        owned_process[0] = process
        report['owned_process_id'] = process.pid
        queue = comfy_video._request(base, '/queue', timeout=timeout)
        running, pending = comfy_video._queue_prompt_ids(queue)
        report['queue'] = dict(running=len(running), pending=len(pending))
        if running or pending:
            raise RuntimeError('A saját indítási próba munkasora váratlanul nem üres.')
        stats = comfy_video._request(base, '/system_stats', timeout=timeout)
        if not isinstance(stats, dict) or not isinstance(stats.get('system'), dict):
            raise ValueError('Az AI-motor rendszeradatai érvénytelenek.')
        report['system_stats_valid'] = True
        info = comfy_video._request(base, '/object_info', timeout=timeout)
        if not isinstance(info, dict):
            raise ValueError('Az AI-motor összetevőlistája érvénytelen.')
        missing = [name for name in realistic_video.EXTRA_NODES if name not in info]
        report['required_nodes'] = dict(expected=list(realistic_video.EXTRA_NODES),
                                        missing=missing)
        if missing:
            raise RuntimeError('Hiányzó AI-motor összetevők: ' + ', '.join(missing))
        report['status'] = 'PASS'
    except BaseException as error:
        report.update(status='FAIL', error_type=type(error).__name__, error=_safe_text(error))
    finally:
        comfy_video._request = original_request
        try:
            # The private port is still reachable by other local clients. Leave
            # their work running if the queue is busy or cannot be verified.
            comfy_video.stop_engine(engine)
        except BaseException as error:
            report['cleanup_error'] = dict(error_type=type(error).__name__, error=_safe_text(error))
            report['status'] = 'FAIL'
        process = owned_process[0]
        report['owned_process_stopped'] = process is None or process.poll() is not None
        if not report['owned_process_stopped']:
            report['status'] = 'FAIL'
            report['owned_process_preserved'] = 'A munkasor foglalt vagy nem igazolható; a futó motort megőriztem.'
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
