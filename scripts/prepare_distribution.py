"""Copy redistributable resources beside a locally built Windows executable."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'work/v2_source'
    destination = ROOT / 'dist/YouTubeMuhely2'
    if not (destination / 'YouTubeMuhely2.exe').is_file():
        raise SystemExit('Előbb készítsd el az EXE-t a build.ps1 szkripttel.')
    for item in source.iterdir():
        if item.is_file() and item.suffix in ('.py', '.json', '.md', '.txt'):
            shutil.copy2(item, destination / item.name)
    for name in ('tools', 'face_restore_vendor', 'hangmotor'):
        if (source / name).is_dir():
            shutil.copytree(source / name, destination / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.part', 'downloads'))
    for name in ('LICENSES', 'voice_notices'):
        shutil.copytree(ROOT / name, destination / name, dirs_exist_ok=True)
    for name in ('LICENSE.txt', 'THIRD_PARTY.md', 'FFMPEG_LICENSE.txt', 'requirements.txt', 'README.md'):
        shutil.copy2(ROOT / name, destination / name)
    print('A teljes programcsomag elkészült:', destination)


if __name__ == '__main__':
    main()
