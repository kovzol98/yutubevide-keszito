"""Read-only Windows GPU reset detection scoped to one local AI submission.

ComfyUI may report success after Windows recovers a hung display driver.  Keep
this guard independent of model/GPU libraries so it can reject that result and
stop a queue without probing the damaged GPU context.  No settings are changed.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid


POLL_SECONDS = 20.0
QUERY_TIMEOUT = 4.0
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('A GPU-ellenőrzéshez időzónával megadott idő szükséges.')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ResetEvent:
    record_id: int
    timestamp: datetime
    event_id: int = 4101
    provider: str = 'Display'
    timestamp_text: str = ''


class GpuHealthError(RuntimeError):
    """A GPU result is unsafe to accept; the pending queue must stop."""

    halt_queue = True


class GpuResultError(GpuHealthError):
    """Invalid/nonfinite GPU output, even if no Windows reset was readable."""

    def __init__(self, message=None):
        super().__init__(message or
            'Az AI-motor hibás számítási eredményt adott. A jelenetet nem '
            'mentem kész videóként. Az újabb GPU-feladatok előtt indítsd újra '
            'az AI-motort, és válassz rövidebb jelenetet vagy kisebb részletességet.')


class GpuResetError(GpuHealthError):
    """A confirmed new Windows display-driver reset; never retry GPU work."""

    def __init__(self, event):
        self.event = event
        self.record_id = event.record_id
        self.timestamp = event.timestamp
        super().__init__(
            'A videókártya illesztőprogramja újraindult az AI-jelenet készítése '
            'közben. A hibás eredményt nem mentem kész videóként. '
            'A munkasort állítsd le, majd az AI-motor újraindítása után válassz '
            'rövidebb jelenetet vagy kisebb részletességet. '
            f'(Windows Display {event.event_id}; '
            f'{event.timestamp_text or event.timestamp.isoformat()}; eseményazonosító: {event.record_id}.)')


def _event(value):
    if isinstance(value, ResetEvent):
        if (type(value.record_id) is int and value.record_id > 0
                and value.event_id == 4101 and value.provider == 'Display'):
            try:
                return ResetEvent(value.record_id, _utc(value.timestamp), timestamp_text=value.timestamp_text)
            except (ValueError, TypeError):
                return None
        return None
    if not isinstance(value, dict):
        return None
    if (value.get('provider') != 'Display' or value.get('event_id') != 4101
            or type(value.get('record_id')) is not int or value['record_id'] <= 0):
        return None
    timestamp = value.get('timestamp')
    timestamp_text = timestamp if isinstance(timestamp, str) else ''
    try:
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        return ResetEvent(value['record_id'], _utc(timestamp), timestamp_text=timestamp_text)
    except (ValueError, TypeError):
        return None


def read_display_resets(start, end):
    """Return only Display/4101 records in the UTC interval, or None if unreadable.

    The fixed script never reads unrelated messages or launches a visible
    terminal.  EncodedCommand and an argument array avoid shell interpolation.
    Missing permissions/PowerShell/event service and timeouts are nonfatal.
    """
    if os.name != 'nt':
        return None
    start, end = _utc(start), _utc(end)
    if end < start:
        return None
    # These strings are produced solely from aware datetime values, never input.
    start_text, end_text = start.isoformat(), end.isoformat()
    script = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        f"$jobStart=[datetimeoffset]::Parse('{start_text}').LocalDateTime; "
        f"$jobEnd=[datetimeoffset]::Parse('{end_text}').LocalDateTime; "
        # Select the host's built-in module explicitly.  A caller may inherit
        # PSModulePath from a different/bundled PowerShell installation.
        "try { Import-Module -Name (Join-Path $PSHOME "
        "'Modules/Microsoft.PowerShell.Diagnostics/Microsoft.PowerShell.Diagnostics.psd1') "
        "-ErrorAction Stop; $events=@(Get-WinEvent -FilterHashtable @{LogName='System'; "
        "ProviderName='Display'; Id=4101; StartTime=$jobStart; EndTime=$jobEnd} "
        "-MaxEvents 32 -ErrorAction Stop); "
        "$records=@($events | ForEach-Object { [pscustomobject]@{ "
        "record_id=[long]$_.RecordId; "
        "timestamp=$_.TimeCreated.ToUniversalTime().ToString('o'); "
        "event_id=[int]$_.Id; provider=[string]$_.ProviderName } }); "
        "ConvertTo-Json -InputObject $records -Compress "
        "} catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') "
        "{ Write-Output '[]' } else { exit 3 } }"
    )
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
             '-EncodedCommand', encoded],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=QUERY_TIMEOUT, creationflags=CREATE_NO_WINDOW, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 65536:
            return None
        data = json.loads(result.stdout.lstrip('\ufeff').strip())
        if not isinstance(data, list) or len(data) > 32:
            return None
        return [event for item in data if (event := _event(item)) is not None]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class GpuMonitor:
    """Baseline before submission; cheap check() in progress, forced at success."""

    def __init__(self, *, reader=None, clock=None, monotonic=None,
                 platform=None, interval=POLL_SECONDS, cancel=None, report_path=None):
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 15 <= interval <= 30:
            raise ValueError('A GPU-ellenőrzés gyakorisága 15–30 másodperc lehet.')
        self.reader = reader or read_display_resets
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.interval = float(interval)
        self.cancel = cancel
        self.report_path = Path(report_path) if report_path is not None else None
        self.enabled = (platform if platform is not None else os.name) == 'nt'
        self.started_at = _utc(self.clock())
        self.baseline_record_id = 0
        self.available = False
        self.reset_event = None
        self.last_checked_at = None
        self._last_check = self.monotonic()
        if self.enabled:
            # A short read provides a cursor even when a reset happened just
            # before this job.  Older records can never reject the new render.
            events = self._read(self.started_at - timedelta(seconds=60), self.started_at)
            if events is not None:
                self.baseline_record_id = max((event.record_id for event in events
                    if event.timestamp <= self.started_at), default=0)
        self._save_report()

    def _save_report(self):
        if self.report_path is None:
            return
        event = self.reset_event
        data = {
            'schema': 1,
            'status': 'reset' if event else ('watching' if self.available else 'unavailable'),
            'started_at': self.started_at.isoformat(),
            'last_checked_at': self.last_checked_at.isoformat() if self.last_checked_at else None,
            'baseline_record_id': self.baseline_record_id,
            'available': self.available,
            'reason': None if self.available else (
                'Windows Display event log could not be read.' if self.enabled
                else 'Windows Display event log is not available on this platform.'),
            'reset_event': None if event is None else {
                'provider': event.provider, 'event_id': event.event_id,
                'record_id': event.record_id,
                'timestamp': event.timestamp_text or event.timestamp.isoformat(),
            },
        }
        temporary = self.report_path.with_name(self.report_path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.report_path)
        except OSError:
            # A diagnostic file must not hide a real reset or fail a render.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read(self, start, end):
        if self.cancel is not None:
            self.cancel()
        try:
            values = self.reader(start, end)
            if values is None or not isinstance(values, (tuple, list)):
                self.available = False
                result = None
            else:
                self.available = True
                result = [event for value in values if (event := _event(value)) is not None]
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            self.available = False
            result = None
        if self.cancel is not None:
            self.cancel()
        self.last_checked_at = end
        return result

    def check(self, *, force=False):
        """Raise GpuResetError only for a new confirmed event from this job.

        A reset poisons this monitor permanently: callers must not recover a
        latent, accept a history success or start another GPU job with it.
        """
        if self.reset_event is not None:
            raise GpuResetError(self.reset_event)
        if not self.enabled:
            return
        tick = self.monotonic()
        if not force and tick - self._last_check < self.interval:
            return
        self._last_check = tick
        ended_at = _utc(self.clock())
        if ended_at < self.started_at:
            return
        events = self._read(self.started_at, ended_at)
        if events is None:
            self._save_report()
            return
        new_events = [event for event in events
                      if event.record_id > self.baseline_record_id
                      and self.started_at <= event.timestamp <= ended_at]
        if new_events:
            self.reset_event = min(new_events, key=lambda event: (event.timestamp, event.record_id))
            self._save_report()
            raise GpuResetError(self.reset_event)
        self._save_report()


def begin_monitor(**kwargs):
    """Create immediately before submitting one local ComfyUI AI workflow."""
    return GpuMonitor(**kwargs)
