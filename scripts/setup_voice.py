"""Install checksum-pinned Piper and an English voice; no AI or paid service."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLAN = Path(__file__).with_name('voice_assets.json')
TARGET = ROOT / 'work/v2_source/hangmotor'


def matches(path: Path, asset: dict) -> bool:
    if not path.is_file() or path.stat().st_size != asset['size']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == asset['sha256']


def download(url: str, target: Path, limit: int):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'YouTubeMuhely-VoiceSetup/1'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as output:
            received = 0
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > limit:
                    raise RuntimeError('A hangmotor letöltése túllépte a várt méretet.')
                output.write(block)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def install_runtime(archive: Path, target: Path, entries: dict):
    """Only write named, hashed files; never extract archive-supplied paths."""
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError('Ismétlődő fájlnév a hangmotor archívumában.')
        # Verify the complete expected runtime before writing any runtime file.
        for relative, asset in entries.items():
            parts = Path(relative).parts
            if Path(relative).is_absolute() or '..' in parts or ':' in relative:
                raise RuntimeError('Érvénytelen rögzített hangmotor-fájlútvonal.')
            record = bundle.getinfo(relative)
            if record.file_size != asset['size']:
                raise RuntimeError('Eltérő hangmotor-fájlméret: ' + relative)
            with bundle.open(record) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != asset['sha256']:
                raise RuntimeError('Eltérő hangmotor-ellenőrzőösszeg: ' + relative)
        for relative, asset in entries.items():
            destination = target / relative
            if matches(destination, asset):
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + '.part')
            try:
                with bundle.open(relative) as source, partial.open('wb') as output:
                    shutil.copyfileobj(source, output)
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Csak helyi ellenőrzés; nincs letöltés.')
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    assets = {**plan['runtime_files'], **plan['voice_files']}
    missing = [name for name, asset in assets.items() if not matches(TARGET / name, asset)]
    if not missing:
        print('Az offline angol hangmotor ellenőrizve, használatra kész.')
        return 0
    if args.check:
        print('Hiányzó vagy eltérő hangmotor-fájlok:', len(missing))
        return 1
    if any(name in plan['runtime_files'] for name in missing):
        print('Piper Windows hangmotor letöltése…')
        archive = TARGET / 'downloads/piper_windows_amd64.zip'
        download(plan['piper_url'], archive, 128 * 1024**2)
        install_runtime(archive, TARGET, plan['runtime_files'])
    for name, asset in plan['voice_files'].items():
        destination = TARGET / name
        if not matches(destination, asset):
            print('Angol hang letöltése:', name)
            archive = TARGET / 'downloads' / name
            download(asset['url'], archive, asset['size'])
            if not matches(archive, asset):
                raise RuntimeError('Eltérő angol hang-ellenőrzőösszeg: ' + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, destination)
    for name in ('PIPER_LICENSE.md', 'EN_MODEL_CARD'):
        shutil.copyfile(ROOT / 'voice_notices' / name, TARGET / name)
    remaining = [name for name, asset in assets.items() if not matches(TARGET / name, asset)]
    if remaining:
        raise RuntimeError('A hangmotor ellenőrzése sikertelen: ' + ', '.join(remaining[:3]))
    print('Az offline angol hangmotor telepítve és ellenőrizve.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
        raise SystemExit('Hangmotor telepítési hiba: ' + str(error)) from None
