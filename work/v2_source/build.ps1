$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a Python-környezet létrehozása.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a függőségek telepítése.' }
$sslPolicy = (& .\.venv\Scripts\python.exe -c "import ssl; from pathlib import Path; p=Path(ssl.__file__).with_name('codex-rejected-cas.pem'); print(str(p) if p.is_file() else '')").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a hálózati biztonsági adatfájl ellenőrzése.' }
$sslData = @()
if ($sslPolicy) { $sslData = @('--add-data', ($sslPolicy + ';.')) }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --name YouTubeMuhely2 --collect-all imageio_ffmpeg --collect-all edge_tts @sslData --add-data 'tools;tools' --add-data 'portrait_assets.json;.' --add-data 'liveportrait_worker.py;.' --add-data 'portrait_detail.py;.' --add-data 'quality_face_worker.py;.' --add-data 'reference_framing.py;.' --add-data 'scene_background.py;.' --add-data 'face_restore_vendor;face_restore_vendor' --add-data 'realism_assets.json;.' --add-data 'rife_asset.json;.' ytmuhely.py
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült az EXE elkészítése.' }
Copy-Item -LiteralPath .\hangmotor -Destination .\dist\YouTubeMuhely2\hangmotor -Recurse -Force
Write-Host 'Elkészült: dist\YouTubeMuhely2\YouTubeMuhely2.exe'
