"""Install the pinned AMD ComfyUI runtime and the verified shared Wan models.

Public API: setup(target, log, cancel) -> pathlib.Path. The small pinned addon
archive and wheels can be bundled in tools/; otherwise they are downloaded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

import setup_ai as native_setup

VERSION = "comfy-amd-v0.40.0-wan22-gguf-6ea2651-official5b-v3"
PORTABLE_ASSET = {
    "name": "AMD ComfyUI videómotor",
    "path": "downloads/ComfyUI_windows_portable_amd_v0.40.0.7z",
    "url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.40.0/ComfyUI_windows_portable_amd.7z",
    "sha256": "f3a58381de51872815f38dbd092ad2c829e3c8d528e03600e760514ae44d45fb",
    "size": None,
}
ADDON_ASSET = {
    "name": "GGUF videómodell betöltő",
    "path": "downloads/ComfyUI-GGUF-6ea2651.zip",
    "url": "https://codeload.github.com/city96/ComfyUI-GGUF/zip/6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
    "sha256": "aad273a0b774684285b4496f6edff7e2293bd29d71ef89926d6861bfdf4947ac",
    "size": None,
}
WHEELS = (
    {"package": "gguf", "version": "0.19.0", "filename": "gguf-0.19.0-py3-none-any.whl",
     "size": 118475, "sha256": "70bcd10edfe697fb2dad6e40af2234b9d8ece9a41a99761405121ebda1c3c1cd"},
    {"package": "protobuf", "version": "7.36.2", "filename": "protobuf-7.36.2-py3-none-any.whl",
     "size": 179806, "sha256": "bdb3a345d48db958e6ce1f18e508beb0cc981d64f24088427549c866cd039f1e"},
)


def _homes():
    home = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    paths = [home]
    if getattr(sys, "_MEIPASS", None):
        paths.append(Path(sys._MEIPASS))
    paths.append(Path(__file__).resolve().parent)
    return list(dict.fromkeys(path.resolve() for path in paths))


def _emit(log, message):
    native_setup._emit(log, message)


def _bundled(filename):
    for home in _homes():
        for candidate in (home / "tools" / filename, home / filename):
            if candidate.is_file():
                return candidate
    return None


def _find_7zr():
    result = _bundled("7zr.exe")
    if result is None:
        # Source-tree testing only; distributable programs include tools/7zr.exe.
        candidate = Path(__file__).resolve().parent.parent / "7zr.exe"
        if candidate.is_file():
            result = candidate
    if result is None:
        raise RuntimeError("Hiányzik a program tools/7zr.exe fájlja. Csomagold ki újra a teljes programot.")
    return result


def _install_record(root):
    existing = native_setup._read_manifest(root)
    return existing or {"version": native_setup.VERSION, "files": {}, "complete": False}


def _obtain(root, asset, record, log, cancel):
    destination = root / asset["path"]
    if not native_setup._asset_ready(root, asset, record):
        bundle = _bundled(destination.name)
        if bundle is not None:
            native_setup._guard(cancel)
            _emit(log, asset["name"] + ": csomagolt fájl ellenőrzése…")
            if native_setup._hash(bundle, cancel) != asset["sha256"]:
                raise RuntimeError("A program csomagolt AI-összetevője sérült: " + bundle.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle, destination)
            record.setdefault("files", {})[asset["path"]] = {
                "size": destination.stat().st_size, "sha256": asset["sha256"]}
            native_setup._write_manifest(root, record)
    native_setup._download(root, asset, record, log, cancel)
    return destination


def _run(command, root, log, cancel, purpose, timeout=900):
    _emit(log, purpose)
    path = root / "comfy_setup.log"
    with path.open("ab", buffering=0) as stream:
        process = subprocess.Popen([str(value) for value in command], stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        started, last_report = time.monotonic(), time.monotonic()
        try:
            while process.poll() is None:
                native_setup._guard(cancel)
                if time.monotonic() - started > timeout:
                    raise RuntimeError("Az AI-motor telepítési lépése túl sokáig tartott.")
                if time.monotonic() - last_report >= 20:
                    _emit(log, purpose + " (%d másodperc)" % int(time.monotonic() - started))
                    last_report = time.monotonic()
                time.sleep(0.2)
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise
    if process.returncode:
        detail = path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError("Az AI-motor telepítése hibát jelzett.\n" + detail)


def _extract_addon(archive_path, target, cancel):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise RuntimeError("Hibás GGUF-összetevő méret.")
        for item in archive.infolist():
            native_setup._guard(cancel)
            path = item.filename.replace("\\", "/")
            pieces = path.split("/")
            if path.startswith("/") or ":" in path or ".." in pieces or stat.S_ISLNK(item.external_attr >> 16):
                raise RuntimeError("Hibás elérési út a GGUF-összetevőben.")
            relative = "/".join(pieces[1:])
            if not relative:
                continue
            output = (target / relative).resolve()
            if target.resolve() not in output.parents:
                raise RuntimeError("A GGUF-összetevő elérési útja kilép a telepítési mappából.")
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".extracting")
            with archive.open(item) as source, temporary.open("wb") as sink:
                while True:
                    native_setup._guard(cancel)
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    sink.write(block)
            os.replace(temporary, output)


def _wheel_asset(details, root, record, cancel):
    asset = {"name": "AI-motor kiegészítő: " + details["package"],
             "path": "downloads/" + details["filename"], "url": "",
             "sha256": details["sha256"], "size": details["size"]}
    # A bundled or previously verified wheel needs no PyPI network request.
    if native_setup._asset_ready(root, asset, record) or _bundled(details["filename"]):
        return asset
    native_setup._guard(cancel)
    request = urllib.request.Request("https://pypi.org/pypi/%s/%s/json" % (details["package"], details["version"]),
                                     headers={"User-Agent": "YouTubeMuhely-AIVideo/2.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        metadata = json.load(response)
    for candidate in metadata["urls"]:
        if candidate["filename"] == details["filename"]:
            if candidate["digests"]["sha256"] != details["sha256"]:
                raise RuntimeError("A rögzített AI-kiegészítő ellenőrzőösszege eltér a kiadástól.")
            asset["url"] = candidate["url"]
            return asset
    raise RuntimeError("A rögzített AI-kiegészítő nem található: " + details["filename"])


def _check_dependencies(python):
    script = "import importlib.metadata,json; print(json.dumps({name:importlib.metadata.version(name) for name in ['gguf','protobuf']}))"
    result = subprocess.run([str(python), "-s", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return json.loads(result.stdout.decode("utf-8")) if result.returncode == 0 else {}
    except ValueError:
        return {}


def check(target=None):
    root = Path(target or native_setup.default_target()).expanduser().resolve()
    portable = root / "ComfyUI_windows_portable"
    missing = list(native_setup.check(root)["missing"])
    for relative in ("ComfyUI_windows_portable/ComfyUI/main.py",
                     "ComfyUI_windows_portable/python_embeded/python.exe",
                     "ComfyUI_windows_portable/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py"):
        if not (root / relative).is_file():
            missing.append(relative)
    try:
        manifest = json.loads((root / "comfy_installation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    ready = not missing and manifest.get("version") == VERSION and manifest.get("complete") is True
    return {"ready": ready, "target": str(root), "missing": missing, "version": VERSION}


def setup(target=None, log=None, cancel=None):
    root = Path(target or native_setup.default_target()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    portable = root / "ComfyUI_windows_portable"
    available = (portable / "ComfyUI/main.py").is_file() and (portable / "python_embeded/python.exe").is_file()
    if not available:
        previous = native_setup._read_manifest(root)
        remaining_models = sum(asset["size"] or 4_200_000_000 for asset in native_setup.ASSETS[1:]
                               if not native_setup._asset_ready(root, asset, previous))
        needed = remaining_models + 7_000_000_000
        if shutil.disk_usage(root).free < needed:
            raise RuntimeError("Az AMD AI-motor telepítéséhez legalább %.1f GB szabad lemezterület szükséges." % (needed / 1e9))
    # Reuse native_setup's resumable downloads and pinned model integrity checks.
    native_setup.setup(root, log, cancel)
    record = _install_record(root)
    if not available:
        archive = _obtain(root, PORTABLE_ASSET, record, log, cancel)
        extractor = _find_7zr()
        _run([extractor, "x", "-y", "-bsp0", "-o" + str(root), archive], root, log, cancel,
             "AMD AI-videómotor kicsomagolása…", timeout=900)
        if not (portable / "ComfyUI/main.py").is_file() or not (portable / "python_embeded/python.exe").is_file():
            raise RuntimeError("Az AMD AI-motor nem a várt mappába került.")
    python = portable / "python_embeded/python.exe"
    addon_zip = _obtain(root, ADDON_ASSET, record, log, cancel)
    _emit(log, "GGUF videómodell betöltő telepítése…")
    _extract_addon(addon_zip, portable / "ComfyUI/custom_nodes/ComfyUI-GGUF", cancel)
    installed = _check_dependencies(python)
    for details in WHEELS:
        if installed.get(details["package"]) == details["version"]:
            _emit(log, "AI-kiegészítő már telepítve: " + details["package"])
            continue
        asset = _wheel_asset(details, root, record, cancel)
        wheel = _obtain(root, asset, record, log, cancel)
        _run([python, "-s", "-m", "pip", "install", "--no-deps", "--no-index", wheel],
             root, log, cancel, "AI-kiegészítő telepítése: " + details["package"], timeout=180)
    model_root = str(root / "models").replace("\\", "/")
    configuration = "youtube_muhely_shared:\n  base_path: %s\n  diffusion_models: diffusion_models\n  unet: diffusion_models\n  text_encoders: text_encoders\n  clip: text_encoders\n  vae: vae\n" % json.dumps(model_root)
    (root / "youtube_muhely_models.yaml").write_text(configuration, encoding="utf-8")
    probe_script = (
        "import torch,gguf,sentencepiece,google.protobuf,json; "
        "ok=torch.cuda.is_available(); "
        "print(json.dumps({'gpu_ready':ok,'gpu':torch.cuda.get_device_name(0) if ok else '', 'torch':torch.__version__})); "
        "raise SystemExit(0 if ok else 2)"
    )
    _run([python, "-s", "-c", probe_script], root, log, cancel,
         "AMD videokártya és AI-összetevők ellenőrzése…", timeout=90)
    report = {"version": VERSION, "complete": True, "models_version": native_setup.VERSION,
              "portable_release": "v0.40.0", "portable_sha256": PORTABLE_ASSET["sha256"],
              "gguf_commit": "6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
              "gguf_zip_sha256": ADDON_ASSET["sha256"],
              "default_model": native_setup.ASSETS[1]['path'],
              "automatic_model_variant": "official_wan22_5b",
              "wheels": [{"package": d["package"], "version": d["version"], "sha256": d["sha256"]} for d in WHEELS]}
    temporary = root / "comfy_installation.json.tmp"
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(temporary, root / "comfy_installation.json")
    _emit(log, "Az AMD AI-videómotor és a modellek telepítve. Indíthatod a videókészítést.")
    return root


def main():
    parser = argparse.ArgumentParser(description="Helyi AMD AI-videómotor telepítése")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        status = check(args.target)
        print(json.dumps(status, ensure_ascii=True))
        return 0 if status["ready"] else 1
    def logger(message):
        try:
            print(message, flush=True)
        except (OSError, UnicodeError):
            pass
    setup(args.target, logger, None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        try:
            print("ERROR:", str(error), file=sys.stderr, flush=True)
        except (OSError, UnicodeError):
            pass
        raise SystemExit(1)
