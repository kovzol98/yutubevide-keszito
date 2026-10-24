"""Read-only Windows 11 support and bounded GPU checks in the selected AI runtime."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import time

AMD_SUPPORT_URL = 'https://www.amd.com/en/resources/support-articles/release-notes/RN-AMDGPU-WINDOWS-PYTORCH-7-2-1.html'
_MARKER = 'YTM_GPU_CHECK:'
_GPU_SCRIPT = r"""
import json
try:
    import torch
    ready = torch.cuda.is_available()
    result = dict(gpu_ready=ready, torch=str(torch.__version__),
                  hip=str(torch.version.hip or ''), cuda=str(torch.version.cuda or ''))
    if ready:
        x = torch.ones((32, 32), dtype=torch.float32, device='cuda')
        y = x @ x
        result['finite'] = bool(torch.isfinite(y).all().item())
        result['correct'] = bool((y == 32).all().item())
        torch.cuda.synchronize()
        result['gpu'] = torch.cuda.get_device_name(0)
        result['memory_bytes'] = int(torch.cuda.get_device_properties(0).total_memory)
    print('YTM_GPU_CHECK:' + json.dumps(result))
except Exception as error:
    print('YTM_GPU_CHECK:' + json.dumps(dict(gpu_ready=False, error_type=type(error).__name__)))
""".strip()


def classify_windows(build, architecture, python_bits=64, *, release='', revision=None):
    """ProductName can still say Windows 10 after an upgrade; use the actual NT build."""
    build = build if type(build) is int and build > 0 else None
    architecture = str(architecture).upper()
    supported = bool(build and build >= 22000 and architecture in ('AMD64', 'X86_64')
                     and python_bits == 64)
    name = 'Windows 11' if build and build >= 22000 else ('Windows 10' if build and build >= 10240 else 'Windows')
    return dict(name=name, build=build, release=str(release)[:24], revision=revision,
                architecture=architecture, python_bits=python_bits, supported=supported)


def system_info():
    if os.name != 'nt':
        return dict(name=platform.system(), build=None, release='', revision=None,
                    architecture=platform.machine().upper(), python_bits=struct.calcsize('P') * 8,
                    supported=False)
    build, release, revision = None, '', None
    try:
        version = sys.getwindowsversion()
        actual = getattr(version, 'platform_version', None)
        build = max(version.build, actual[2]) if actual and len(actual) == 3 else version.build
    except (AttributeError, OSError, TypeError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            release = winreg.QueryValueEx(key, 'DisplayVersion')[0]
            revision = winreg.QueryValueEx(key, 'UBR')[0]
    except (ImportError, OSError):
        pass
    return classify_windows(build, platform.machine(), struct.calcsize('P') * 8,
                            release=release, revision=revision)


def system_label(info=None):
    info = system_info() if info is None else info
    suffix = f" • {info['release']}" if info.get('release') else ''
    if info.get('build'):
        suffix += ' • build ' + str(info['build'])
        if type(info.get('revision')) is int:
            suffix += '.' + str(info['revision'])
    return info['name'] + suffix


def require_amd_windows():
    # Keep source tools/fixtures importable on other platforms.
    if os.name != 'nt':
        return
    info = system_info()
    if not info['supported']:
        raise RuntimeError('A csomagolt AMD AI-motorhoz 64 bites Windows 11 és x64-es program szükséges. '
                           'Felismert rendszer: ' + system_label(info) + '. '
                           'A saját videók vágása és a hang tovább használható.')


class WindowsGpuError(RuntimeError):
    halt_queue = True


def native_failure(exit_code):
    if type(exit_code) is not int:
        return None
    code = exit_code & 0xffffffff
    meanings = {0xc0000005: 'natív összeomlás', 0xc0000409: 'natív összeomlás',
                0xc000001d: 'nem támogatott gépi utasítás',
                0xc0000135: 'hiányzó futtatókörnyezet', 0xc000007b: 'hibás motorösszetevő'}
    if code not in meanings:
        return None
    return WindowsGpuError(
        'Az AI-motor Windows/GPU-összetevője leállt (' + meanings[code]
        + f'; 0x{code:08X}). A munkasor megáll. Használd a Windows 11 és AI ellenőrzése gombot; '
        'ellenőrizd a Windows 11-hez készült AMD illesztőprogramot és a motor telepítését.')


def _stop(process):
    try:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.communicate(timeout=1)
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        pass


def _parse_gpu(output):
    if not output or len(output) > 1024 * 1024:
        return dict(status='unavailable', reason='Az AI-motor nem adott értelmezhető választ.')
    try:
        lines = output.decode('utf-8-sig').splitlines()
        line = next(line[len(_MARKER):] for line in reversed(lines) if line.startswith(_MARKER))
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError()
        # Do not copy arbitrary child-process output to a dialog or report.
        result = {key: str(data.get(key, ''))[:160] for key in ('torch', 'hip', 'cuda', 'gpu', 'error_type')}
        result['gpu_ready'] = data.get('gpu_ready') is True
        result['finite'] = data.get('finite') is True
        result['correct'] = data.get('correct') is True
        memory = data.get('memory_bytes')
        result['memory_bytes'] = memory if type(memory) is int and 0 < memory < 2 ** 60 else None
        result['backend'] = 'AMD ROCm / HIP' if result['hip'] else ('NVIDIA CUDA' if result['cuda'] else 'ismeretlen')
        result['status'] = 'PASS' if (result['gpu_ready'] and result['finite'] and result['correct']
                                      and (result['hip'] or result['cuda'])) else 'FAIL'
        return result
    except (UnicodeError, ValueError, StopIteration, TypeError):
        return dict(status='unavailable', reason='Az AI-motor válasza nem olvasható.')


def probe_gpu(root, cancel=None, timeout=45):
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or not 1 <= timeout <= 90):
        raise ValueError('Az ellenőrzési időkorlát 1–90 másodperc lehet.')
    if cancel is not None and cancel.is_set():
        return dict(status='cancelled')
    root = Path(root).expanduser().resolve()
    portable = next((p for p in (root / 'ComfyUI_windows_portable', root)
                     if (p / 'python_embeded/python.exe').is_file()
                     and (p / 'ComfyUI/main.py').is_file()), None)
    if portable is None:
        return dict(status='missing', reason='Válaszd ki a meglévő AI-motor mappáját, vagy használd az AI-motor letöltése gombot.')
    process, started = None, time.monotonic()
    try:
        process = subprocess.Popen([str(portable / 'python_embeded/python.exe'), '-s', '-c', _GPU_SCRIPT],
                                   cwd=str(portable), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while True:
            if cancel is not None and cancel.is_set():
                _stop(process)
                return dict(status='cancelled')
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                _stop(process)
                return dict(status='timeout', reason='A GPU-ellenőrzés nem fejeződött be időben. Ellenőrizd az AMD illesztőprogramot.')
            try:
                output, _ = process.communicate(timeout=min(.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if cancel is not None and cancel.is_set():
            return dict(status='cancelled')
        if process.returncode:
            native = native_failure(process.returncode)
            if native is not None:
                return dict(status='FAIL', reason=str(native), exit_code='0x%08X' % (process.returncode & 0xffffffff))
            return dict(status='FAIL', reason='Az AI-motor Python-folyamata hibával kilépett. Ellenőrizd a motor telepítését és az AMD illesztőprogramot.')
        return _parse_gpu(output)
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            _stop(process)
        return dict(status='unavailable', reason='Az AI-motor ellenőrzése nem indítható.')


def check_environment(root, cancel=None, timeout=45):
    system = system_info()
    gpu = probe_gpu(root, cancel, timeout) if system['supported'] else dict(status='not_checked')
    status = ('cancelled' if gpu['status'] == 'cancelled' else
              'PASS' if system['supported'] and gpu['status'] == 'PASS' else 'FAIL')
    return dict(schema=1, status=status, checked_utc=datetime.now(timezone.utc).isoformat(),
                system=system, gpu=gpu, driver_support_url=AMD_SUPPORT_URL,
                read_only=True, model_download_requested=False)


def describe(report):
    lines = [system_label(report['system'])]
    gpu = report['gpu']
    if not report['system']['supported']:
        lines.append('A csomagolt AMD AI-motorhoz 64 bites Windows 11 és x64-es program szükséges.')
    elif report['status'] == 'PASS':
        lines.append(gpu['gpu'] + ' • ' + gpu['backend'])
        if gpu.get('memory_bytes'):
            lines[-1] += ' • %.1f GiB' % (gpu['memory_bytes'] / 1024 ** 3)
        lines.append('A rövid GPU-számítás sikeres.')
    else:
        lines.append(gpu.get('reason') or 'A GPU-számítás ellenőrzése sikertelen. Ellenőrizd az AMD illesztőprogramot és az AI-motort.')
        if gpu.get('error_type'):
            lines.append('Motorhiba: ' + gpu['error_type'])
    return '\n'.join(lines)


def diagnose(request_file):
    request_file = Path(request_file).resolve()
    request = json.loads(request_file.read_text(encoding='utf-8-sig'))
    report_path = Path(request['report'])
    if not report_path.is_absolute():
        report_path = request_file.parent / report_path
    report = check_environment(request['ai_root'], timeout=request.get('timeout', 45))
    report['summary'] = describe(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report