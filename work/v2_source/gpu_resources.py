"""Read-only Windows dedicated GPU memory observations, without GPU libraries."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time


GIB = 1024 ** 3
_QUERY_SECONDS = 4.5  # Reserve time within five seconds for stopping our own probe.
_POLL_SECONDS = 0.1
_MAX_JSON_BYTES = 2 * 1024 ** 2
_PID = re.compile(r'(?:^|_)pid_(\d+)(?:_|$)', re.IGNORECASE)
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
# A caller running PowerShell 7 can export its PSModulePath to this Windows
# PowerShell 5.1 child. Its Diagnostics module then fails to load. Use this
# child's own built-in modules, without changing the parent environment.
$env:PSModulePath = "$PSHOME\Modules"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    $sample = Get-Counter -Counter '\GPU Process Memory(*)\Dedicated Usage' -MaxSamples 1 -ErrorAction Stop
    $rows = @($sample.CounterSamples | Where-Object { $_.CookedValue -gt 0 } | ForEach-Object {
        @{ instance = $_.InstanceName; bytes = $_.CookedValue }
    })
    $processes = @(Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
        @{ pid = $_.Id; name = $_.ProcessName }
    })
    @{ rows = $rows; processes = $processes } | ConvertTo-Json -Depth 4 -Compress
} catch {
    [Console]::Out.Write('null')
}
""".strip()


def _positive_integer(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _name(value, pid):
    # Display only a process name, never a command line or executable path.
    if not isinstance(value, str) or '/' in value or '\\' in value:
        return f'Folyamat {pid}'
    cleaned = ''.join(char for char in value if char.isprintable()).strip()
    return cleaned[:96] or f'Folyamat {pid}'


def parse_counter_rows(rows, processes, own_pid=None):
    """Aggregate a single counter sample by PID across physical adapters.

    Rows use ``instance``/``bytes`` (or CounterSample's
    ``InstanceName``/``CookedValue``). Processes use ``pid``/``name``
    (or Get-Process's ``Id``/``ProcessName``). Malformed rows are ignored.
    The result contains only PID, process name and dedicated-memory bytes.
    """
    own_pid = os.getpid() if own_pid is None else own_pid
    names = {}
    if isinstance(processes, dict):
        processes = [{'pid': pid, 'name': name} for pid, name in processes.items()]
    if isinstance(processes, (tuple, list)):
        for process in processes:
            if not isinstance(process, dict):
                continue
            pid = _positive_integer(process.get('pid', process.get('Id')))
            if pid:
                names[pid] = _name(process.get('name', process.get('ProcessName')), pid)
    if not isinstance(rows, (tuple, list)):
        return []
    totals, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance = row.get('instance', row.get('InstanceName'))
        if not isinstance(instance, str):
            continue
        match = _PID.search(instance)
        if not match:
            continue
        pid = _positive_integer(match.group(1))
        if pid is None or pid in (0, 4, own_pid):
            continue
        value = row.get('bytes', row.get('CookedValue'))
        if isinstance(value, bool):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        byte_count = round(value)
        if byte_count <= 0 or byte_count > 2 ** 63 - 1:
            continue
        key = instance.casefold()
        if key in seen:
            continue
        seen.add(key)
        totals[pid] = totals.get(pid, 0) + byte_count
    return [{'pid': pid, 'name': names.get(pid, f'Folyamat {pid}'), 'bytes': byte_count}
            for pid, byte_count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))]


def _stop_probe(process):
    """Stop only the child PowerShell that this module itself created."""
    try:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=0.2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.communicate(timeout=0.2)
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        pass


def probe_gpu_users(cancel=None) -> list[dict] | None:
    """Return a successful observation, or None when Windows cannot provide it.

    This runs a hidden, profile-free, read-only counter query. A timeout or
    cancellation terminates only our probe process, never a GPU application.
    Calling engines should check their own cancellation flag after return.
    """
    if os.name != 'nt' or (cancel is not None and cancel.is_set()):
        return None
    process = None
    deadline = time.monotonic() + _QUERY_SECONDS
    try:
        process = subprocess.Popen(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', _SCRIPT],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        while True:
            if cancel is not None and cancel.is_set():
                _stop_probe(process)
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_probe(process)
                return None
            try:
                output, _ = process.communicate(timeout=min(_POLL_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if (cancel is not None and cancel.is_set()) or process.returncode != 0 or not output or len(output) > _MAX_JSON_BYTES:
            return None
        data = json.loads(output.decode('utf-8-sig'))
        if (not isinstance(data, dict) or not isinstance(data.get('rows'), list)
                or not isinstance(data.get('processes'), list)):
            return None
        return parse_counter_rows(data['rows'], data['processes'])
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        if process is not None:
            _stop_probe(process)
        return None


def describe_gpu_users(users, threshold_bytes=2 * GIB) -> str:
    """Explain only substantial dedicated-memory users, without stopping them."""
    if users is None:
        return 'Videomemória: a Windows nem szolgáltatott ellenőrizhető adatot.'
    threshold = _positive_integer(threshold_bytes) or 2 * GIB
    large = []
    for user in users if isinstance(users, (tuple, list)) else []:
        if not isinstance(user, dict):
            continue
        pid = _positive_integer(user.get('pid'))
        byte_count = _positive_integer(user.get('bytes'))
        if pid and byte_count and byte_count >= threshold:
            large.append((byte_count, pid, _name(user.get('name'), pid)))
    threshold_text = f'{threshold / GIB:g}'.replace('.', ',')
    if not large:
        return ('Videomemória: nincs kimutatható, ' + threshold_text
                + ' GiB feletti memóriahasználat más folyamatnál.')
    large.sort(reverse=True)
    details = ', '.join(f'{name} ({format(byte_count / GIB, ".1f").replace(".", ",")} GiB)'
                        for byte_count, pid, name in large[:8])
    if len(large) > 8:
        details += f', további {len(large) - 8} folyamat'
    return ('Videomemória: ' + details + '. Más programok videomemória-használata '
            'lassíthatja az AI-videó készítését.')
