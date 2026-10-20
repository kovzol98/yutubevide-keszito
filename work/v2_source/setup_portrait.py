"""Install the pinned local portrait engine beside the existing AMD runtime."""
from __future__ import annotations
import hashlib, json, os, shutil, stat, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path
from setup_ai import _guard

VERSION = 'liveportrait-pose-9b294b3-mediapipe-1.1.0-v1'

def asset_plan():
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    for folder in (home, Path(getattr(sys, '_MEIPASS', home)), Path(__file__).resolve().parent):
        path = folder / 'portrait_assets.json'
        if path.is_file(): return json.loads(path.read_text(encoding='utf-8'))
    raise RuntimeError('Hiányzik a portrémotor letöltési listája. Csomagold ki a teljes programot.')

def _sha(path, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            _guard(cancel); digest.update(block)
    return digest.hexdigest()

def _download(url, path, digest, log, cancel):
    if not digest: raise RuntimeError('Hiányzó ellenőrzőösszeg: ' + path.name)
    if path.is_file() and _sha(path, cancel) == digest: return
    path.parent.mkdir(parents=True, exist_ok=True)
    log('Portrémotor letöltése: ' + path.name)
    temp = path.with_suffix(path.suffix + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'YouTubeMuhely-Portrait/2.1'})
    with urllib.request.urlopen(request, timeout=90) as response, temp.open('wb') as sink:
        total = int(response.headers.get('Content-Length', 0)); received = 0; last = time.monotonic()
        while block := response.read(1024 * 1024):
            _guard(cancel); sink.write(block); received += len(block)
            if time.monotonic() - last > 10:
                log('%s: %.0f MB%s' % (path.name, received / 1e6, ' / %.0f MB' % (total / 1e6) if total else ''))
                last = time.monotonic()
    if _sha(temp, cancel) != digest: raise RuntimeError('A letöltött fájl sérült: ' + path.name)
    temp.replace(path)

def _extract(archive, target, cancel, strip_root=False):
    target = target.resolve(); target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if sum(item.file_size for item in bundle.infolist()) > 800 * 1024**2:
            raise RuntimeError('Érvénytelen portrémotor-archívum.')
        for item in bundle.infolist():
            _guard(cancel)
            path = item.filename.replace('\\', '/')
            parts = path.split('/')
            if path.startswith('/') or ':' in path or '..' in parts or stat.S_ISLNK(item.external_attr >> 16):
                raise RuntimeError('Érvénytelen elérési út a portréarchívumban.')
            if strip_root: parts = parts[1:]
            if not any(parts) or item.is_dir(): continue
            output = target.joinpath(*parts).resolve()
            if target not in output.parents: raise RuntimeError('Érvénytelen kicsomagolási út.')
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, output.open('wb') as sink: shutil.copyfileobj(source, sink)

def setup(target, log=print, cancel=None):
    root = Path(target).expanduser().resolve()
    runtime = root / 'ComfyUI_windows_portable/python_embeded'
    if not (runtime / 'python.exe').is_file():
        raise RuntimeError('Először telepítsd az AI-motort, majd a portrémotort ugyanabba a mappába.')
    plan = asset_plan(); portrait = root / 'portrait'; downloads = portrait / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / 'LivePortrait.zip'
    _download(plan['repository']['archive_url'], archive, plan['repository']['archive_sha256'], log, cancel)
    _extract(archive, portrait / 'code', cancel, strip_root=True)
    for item in plan['weights']['files']:
        _download(item['url'], portrait / 'models' / item['path'], item['sha256'], log, cancel)
    detector = plan['mediapipe']['face_detector']
    _download(detector['url'], portrait / 'models/blaze_face_short_range.tflite', detector['sha256'], log, cancel)
    for item in plan['wheels']:
        archive = downloads / item['filename']
        _download(item['url'], archive, item['sha256'], log, cancel)
        _extract(archive, portrait / 'packages', cancel)
    python = portrait / 'python'; python.mkdir(exist_ok=True)
    for path in runtime.iterdir():
        _guard(cancel)
        if path.is_file() and path.suffix.lower() in ('.exe', '.dll', '.pyd', '.zip', '.cat'):
            shutil.copyfile(path, python / path.name)
    (python / 'python312._pth').write_text('../packages\npython312.zip\n.\n../../ComfyUI_windows_portable/python_embeded/Lib/site-packages\nimport site\n', encoding='utf-8')
    (python / 'sitecustomize.py').write_text('import sys,os\nfrom pathlib import Path\nsys.dont_write_bytecode=True\np=Path(__file__).resolve().parent.parent/"cache"/"matplotlib"\np.mkdir(parents=True,exist_ok=True)\nos.environ["MPLCONFIGDIR"]=str(p)\n', encoding='utf-8')
    log('Portrémotor összetevőinek ellenőrzése…')
    from setup_comfy import _run
    _run([python / 'python.exe', '-c', 'import torch,torchvision,cv2,mediapipe,numpy,yaml,scipy,rich; print("PORTRAIT IMPORT OK")'], root, log, cancel, 'Portrémotor ellenőrzése', timeout=120)
    sizes = {str((Path('models') / f['path']).as_posix()): f['size'] for f in plan['weights']['files']}
    sizes['models/blaze_face_short_range.tflite'] = detector['size']
    (portrait / 'installed.json').write_text(json.dumps({'version': VERSION, 'complete': True, 'sizes': sizes}, indent=2), encoding='utf-8')
    log('A helyi portrémotor használatra kész.'); return portrait

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('target'); args = parser.parse_args()
    setup(args.target, lambda text: print(text, flush=True))
