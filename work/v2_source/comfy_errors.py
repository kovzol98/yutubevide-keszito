"""Rövid, olvasható hibák a teljes ComfyUI-előzmény megjelenítése helyett."""
from __future__ import annotations

import re


class ComfyExecutionError(RuntimeError):
    """A teljes technikai előzmény a jelenet history.json fájljában marad."""


class ComfyOutOfMemoryError(ComfyExecutionError):
    # Ugyanilyen beállításokkal a következő munkasorelem is kifuthat a memóriából.
    halt_queue = True


def _short_text(value, limit):
    """Csak rövid szövegmezőt olvasunk; tensorokat és más objektumokat nem írunk ki."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ''
    text = str(value)[:limit + 1].replace('\x00', '')
    text = ' '.join(text.split())
    return text[:limit] + ('…' if len(text) > limit else '')


def _details(status):
    if not isinstance(status, dict):
        return {}, False
    messages = status.get('messages', [])
    if not isinstance(messages, (list, tuple)):
        return {}, False
    interrupted = False
    error = {}
    for event in messages:
        if not isinstance(event, (list, tuple)) or not event:
            continue
        if event[0] == 'execution_interrupted':
            interrupted = True
        if len(event) > 1 and event[0] == 'execution_error' and isinstance(event[1], dict):
            error = event[1]
    return error, interrupted


def has_invalid_gpu_result(status):
    """A GPU-hibajelző csak a motor hibaüzenetéből származhat, bemeneti képből nem."""
    if not isinstance(status, dict):
        return False
    messages = status.get('messages', [])
    if not isinstance(messages, (list, tuple)):
        return False
    for event in messages:
        if (not isinstance(event, (list, tuple)) or len(event) < 2 or
                event[0] != 'execution_error' or not isinstance(event[1], dict)):
            continue
        message = event[1].get('exception_message')
        if (isinstance(message, str) and
                message.find('YTMUHELY_INVALID_GPU_RESULT', 0, 2048) >= 0):
            return True
    return False


def execution_error(status, history_path):
    """Kivételt ad vissza kizárólag az execution_error négy rövid mezőjéből.

    A hívó még ez előtt elmenti a teljes history.json fájlt. Sem current_inputs,
    sem current_outputs, sem traceback nem kerül a felugró ablakba.
    """
    details, interrupted = _details(status)
    kind = _short_text(details.get('exception_type'), 100)
    raw_message = details.get('exception_message')
    # Az első sor rendszerint a tényleges hiba; az utána következő tömbök nem.
    message = _short_text(raw_message[:2000].splitlines()[0] if
                          isinstance(raw_message, str) and raw_message else '', 420)
    node_id = _short_text(details.get('node_id'), 24)
    node_type = _short_text(details.get('node_type'), 90)
    lower = message.lower()
    out_of_memory = ('outofmemoryerror' in kind.lower() or
                     'out of memory' in lower or 'not enough memory' in lower)
    if out_of_memory:
        gpu = any(word in lower for word in ('hip', 'cuda', 'gpu', 'device memory'))
        text = ('Az AI-motor videomemória-hiány miatt leállt.' if gpu else
                'Nincs elég memória az AI-jelenet elkészítéséhez.')
        requested = re.search(r'tried to allocate\s+([\d.,]+\s*(?:[KMGT]i?B|bytes?))',
                              message, re.IGNORECASE)
        capacity = re.search(r'total capacity of\s+([\d.,]+\s*(?:[KMGT]i?B|bytes?))',
                             message, re.IGNORECASE)
        if requested:
            text += ' Kért memória: ' + requested.group(1) + '.'
        if capacity:
            text += ' A kártya teljes memóriája: ' + capacity.group(1) + '.'
        text += (' Válassz Normál AI-részletességet vagy kisebb képet.'
                 ' A munkasor további elemei várakoznak.')
        error_class = ComfyOutOfMemoryError
    elif interrupted and not details:
        text = 'Az AI-motor megszakította a jelenet készítését.'
        error_class = ComfyExecutionError
    else:
        text = 'Az AI-motor nem tudta elkészíteni a jelenetet.'
        if kind or message:
            text += ' ' + ': '.join(part for part in (kind, message) if part)
        error_class = ComfyExecutionError
    if node_id or node_type:
        text += ' Érintett lépés: ' + ' / '.join(part for part in (node_id, node_type) if part) + '.'
    if len(text) > 600:
        text = text[:599].rstrip() + '…'
    return error_class(text + '\nRészletek: ' + str(history_path))
