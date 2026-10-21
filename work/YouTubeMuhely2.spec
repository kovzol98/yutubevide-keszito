# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import ssl
from PyInstaller.utils.hooks import collect_all

SOURCE = Path(SPECPATH).resolve() / 'v2_source'
datas = [(str(SOURCE / 'tools'), 'tools'),
         (str(SOURCE / 'face_restore_vendor'), 'face_restore_vendor')]
for name in ('portrait_assets.json', 'liveportrait_worker.py', 'portrait_detail.py',
             'quality_face_worker.py', 'reference_framing.py', 'scene_background.py',
             'realism_assets.json', 'rife_asset.json'):
    datas.append((str(SOURCE / name), '.'))
binaries = []
hiddenimports = []
ssl_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if ssl_policy.is_file():
    datas.append((str(ssl_policy), '.'))
for package in ('imageio_ffmpeg', 'edge_tts'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
a = Analysis([str(SOURCE / 'ytmuhely.py')], pathex=[str(SOURCE)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YouTubeMuhely2',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[],
               name='YouTubeMuhely2')
