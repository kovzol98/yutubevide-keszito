$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a Python-környezet létrehozása.' }
}
$buildPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $buildPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a függőségek telepítése.' }
& $buildPython -m PyInstaller --noconfirm --clean --distpath dist --workpath work/build work/YouTubeMuhely2.spec
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült az EXE elkészítése.' }
& $buildPython scripts/prepare_distribution.py
if ($LASTEXITCODE -ne 0) { throw 'Nem sikerült a program erőforrásainak másolása.' }
Write-Host 'Elkészült: dist/YouTubeMuhely2/YouTubeMuhely2.exe'
