"""Install the pinned local Wan video engine; standard library only.

Public API: setup(target, log, cancel, binaries_only=False) -> pathlib.Path.
`log` accepts a message string. `cancel` may be a threading.Event or callable.
CLI --check performs fast size/manifest checks; downloads are SHA-256 verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "wan22-vulkan-master-964-1d0a58a-v1"
MANIFEST = "installation.json"
ASSETS = (
    {
        "name": "Vulkan videómotor",
        "path": "downloads/sd-master-1d0a58a-bin-win-vulkan-x64.zip",
        "url": "https://github.com/leejet/stable-diffusion.cpp/releases/download/master-964-1d0a58a/sd-master-1d0a58a-bin-win-vulkan-x64.zip",
        "sha256": "74211cb5c382aec2aeb086b64f2fdf525efec5faec4200a2d3257bc2328954ab",
        "size": None,
    },
    {
        "name": "Wan 2.2 mozgóképgeneráló modell",
        "path": "models/diffusion_models/Wan2.2-TI2V-5B-Q5_K_M.gguf",
        "url": "https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF/resolve/main/Wan2.2-TI2V-5B-Q5_K_M.gguf",
        "sha256": "4424633a876511b9be58a41119f7c9d762ea92b3cb74649cdb43cac850e42dba",
        "size": None,
    },
    {
        "name": "Angol jelenetleírás értelmező modell",
        "path": "models/text_encoders/umt5-xxl-encoder-Q5_K_M.gguf",
        "url": "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q5_K_M.gguf",
        "sha256": "eaea358bb438c5d211721a4feecc162000e3636e9cb96f51e216f1f44ebd12ce",
        "size": None,
    },
    {
        "name": "Wan 2.2 képkocka dekódoló modell",
        "path": "models/vae/wan2.2_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors",
        "sha256": "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        "size": 1409400960,
    },
)


class SetupCancelled(RuntimeError):
    pass


def default_target() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "AI_Motor"


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())


def _guard(cancel):
    if _cancelled(cancel):
        raise SetupCancelled("A telepítést megszakítottad. A részleges letöltés folytatható.")


def _emit(log, message):
    if log is not None:
        try:
            log(message)
        except Exception:
            pass  # A destroyed GUI or unavailable stdout must not break a download.


def _read_manifest(target: Path) -> dict:
    try:
        value = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("version") == VERSION else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(target: Path, data: dict):
    temporary = target / (MANIFEST + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target / MANIFEST)


def _hash(path: Path, cancel=None) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _guard(cancel)
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            result.update(block)
    return result.hexdigest()


def _asset_ready(target: Path, asset: dict, record: dict) -> bool:
    path = target / asset["path"]
    entry = record.get("files", {}).get(asset["path"], {})
    try:
        return bool(path.is_file() and entry.get("sha256") == asset["sha256"]
                    and isinstance(entry.get("size"), int) and entry["size"] > 0
                    and path.stat().st_size == entry["size"]
                    and (asset["size"] is None or entry["size"] == asset["size"]))
    except OSError:
        return False


def _download(target: Path, asset: dict, record: dict, log, cancel):
    destination = target / asset["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if _asset_ready(target, asset, record):
        _emit(log, asset["name"] + ": már telepítve.")
        return
    if destination.is_file():
        _emit(log, asset["name"] + ": meglévő fájl ellenőrzése…")
        if _hash(destination, cancel) == asset["sha256"]:
            record.setdefault("files", {})[asset["path"]] = {
                "size": destination.stat().st_size, "sha256": asset["sha256"]}
            _write_manifest(target, record)
            return
        destination.replace(destination.with_name(destination.name + ".invalid"))
    last_error = None
    for attempt in range(1, 7):
        _guard(cancel)
        offset = partial.stat().st_size if partial.exists() else 0
        if asset["size"] is not None and offset > asset["size"]:
            partial.unlink()
            offset = 0
        try:
            headers = {"User-Agent": "YouTubeMuhely-AIVideo/2.0", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = "bytes=%d-" % offset
            # A fresh request lets Hugging Face renew expiring signed redirects.
            request = urllib.request.Request(asset["url"], headers=headers)
            try:
                response = urllib.request.urlopen(request, timeout=45)
            except urllib.error.HTTPError as error:
                if error.code == 416 and offset and _hash(partial, cancel) == asset["sha256"]:
                    os.replace(partial, destination)
                    record.setdefault("files", {})[asset["path"]] = {
                        "size": destination.stat().st_size, "sha256": asset["sha256"]}
                    _write_manifest(target, record)
                    return
                if error.code == 416 and partial.exists():
                    partial.unlink()
                raise
            with response:
                status = getattr(response, "status", 200)
                append = bool(offset and status == 206)
                if status not in (200, 206):
                    raise RuntimeError("Nem várt letöltési válasz: %s" % status)
                if append:
                    content_range = response.headers.get("Content-Range", "")
                    match = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)$", content_range)
                    if not match or int(match[1]) != offset:
                        raise RuntimeError("A kiszolgáló hibás folytatási tartományt küldött.")
                    total = int(match[3]) if match[3] != "*" else None
                else:
                    offset = 0
                    total_header = response.headers.get("Content-Length")
                    total = int(total_header) if total_header else None
                if asset["size"] is not None and total is not None and total != asset["size"]:
                    raise RuntimeError("A letöltés mérete eltér a rögzített modell méretétől.")
                _emit(log, "%s: letöltés%s…" % (asset["name"], " folytatása" if append else ""))
                last_report = 0.0
                with partial.open("ab" if append else "wb") as stream:
                    received = offset
                    while True:
                        _guard(cancel)
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        received += len(chunk)
                        if time.monotonic() - last_report >= 3:
                            percentage = " / %.0f%%" % (100 * received / total) if total else ""
                            _emit(log, "%s: %.2f GB%s" % (asset["name"], received / 1e9, percentage))
                            last_report = time.monotonic()
                    stream.flush()
                if total is not None and received != total:
                    raise RuntimeError("Megszakadt a letöltés; a következő próbálkozás folytatja.")
            _emit(log, asset["name"] + ": SHA-256 ellenőrzés…")
            if _hash(partial, cancel) != asset["sha256"]:
                partial.replace(partial.with_name(partial.name + ".invalid"))
                raise RuntimeError("A letöltött fájl sérült. Új letöltés szükséges.")
            os.replace(partial, destination)
            record.setdefault("files", {})[asset["path"]] = {
                "size": destination.stat().st_size, "sha256": asset["sha256"]}
            _write_manifest(target, record)
            _emit(log, asset["name"] + ": kész és ellenőrizve.")
            return
        except SetupCancelled:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            last_error = error
            if attempt == 6:
                break
            _emit(log, "%s: kapcsolati hiba (%d/6), újrapróbálkozás…" % (asset["name"], attempt))
            deadline = time.monotonic() + min(attempt * 2, 10)
            while time.monotonic() < deadline:
                _guard(cancel)
                time.sleep(0.2)
    raise RuntimeError("%s letöltése nem sikerült: %s. Indítsd újra a telepítést; folytatni fogja." %
                       (asset["name"], last_error))


def find_executable(target) -> Path:
    root = Path(target).resolve()
    candidates = sorted((root / "bin").rglob("sd-cli.exe")) if (root / "bin").exists() else []
    if len(candidates) != 1:
        raise RuntimeError("A helyi videómotor indítófájlja hiányzik vagy több példánya található.")
    result = candidates[0].resolve()
    if root not in result.parents:
        raise RuntimeError("A videómotor helye kívül esik a telepítési könyvtáron.")
    return result


def _extract(target: Path, record: dict, log, cancel):
    runtime_files = record.get("runtime_files", {})
    if runtime_files and all((target / name).is_file() and (target / name).stat().st_size == size
                             for name, size in runtime_files.items()):
        find_executable(target)
        return
    _emit(log, "Videómotor kicsomagolása…")
    destination = target / "bin"
    destination.mkdir(parents=True, exist_ok=True)
    extracted = {}
    with zipfile.ZipFile(target / ASSETS[0]["path"]) as archive:
        if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
            raise RuntimeError("A videómotor csomagjának kitömörített mérete hibás.")
        for item in archive.infolist():
            _guard(cancel)
            name = item.filename.replace("\\", "/")
            mode = item.external_attr >> 16
            parts = name.split("/")
            if name.startswith("/") or ":" in name or ".." in parts or stat.S_ISLNK(mode):
                raise RuntimeError("Nem biztonságos elérési út a videómotor csomagjában.")
            output = (destination / name).resolve()
            if destination.resolve() not in output.parents and output != destination.resolve():
                raise RuntimeError("Hibás kicsomagolási útvonal.")
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + ".extracting")
            with archive.open(item) as source, temporary.open("wb") as sink:
                while True:
                    _guard(cancel)
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    sink.write(block)
            os.replace(temporary, output)
            extracted[output.relative_to(target).as_posix()] = output.stat().st_size
    executable = find_executable(target)
    record["executable"] = executable.relative_to(target).as_posix()
    record["runtime_files"] = extracted
    _write_manifest(target, record)


def detect_devices(target) -> dict:
    executable = find_executable(target)
    result = subprocess.run([str(executable), "--list-devices"], cwd=str(executable.parent),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = result.stdout.decode("utf-8", errors="replace")
    devices = []
    for line in output.splitlines():
        if "\t" in line:
            name, description = line.split("\t", 1)
            if name.strip().lower().startswith("vulkan"):
                devices.append({"name": name.strip(), "description": description.strip()})
    return {"returncode": result.returncode, "devices": devices, "output": output,
            "gpu_ready": result.returncode == 0 and bool(devices)}


def check(target=None, binaries_only=False) -> dict:
    root = Path(target or default_target()).resolve()
    record = _read_manifest(root)
    missing = []
    for asset in ASSETS[:1] if binaries_only else ASSETS:
        if not _asset_ready(root, asset, record):
            missing.append(asset["path"])
    runtime = record.get("runtime_files", {})
    if not runtime:
        missing.append("bin/sd-cli.exe")
    else:
        for name, size in runtime.items():
            path = root / name
            try:
                if not path.is_file() or path.stat().st_size != size:
                    missing.append(name)
            except OSError:
                missing.append(name)
    return {"ready": not missing, "target": str(root), "missing": missing,
            "version": VERSION, "executable": record.get("executable"),
            "binaries_only": binaries_only}


def setup(target=None, log=None, cancel=None, binaries_only=False) -> Path:
    root = Path(target or default_target()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    record = _read_manifest(root) or {"version": VERSION, "files": {}, "complete": False}
    _download(root, ASSETS[0], record, log, cancel)
    _extract(root, record, log, cancel)
    _guard(cancel)
    probe = detect_devices(root)
    record["last_device_check"] = probe
    _write_manifest(root, record)
    if not probe["gpu_ready"]:
        raise RuntimeError("A Vulkan videómotor nem lát működő videokártyát. "
                           "Frissítsd a videokártya illesztőprogramját, majd próbáld újra. "
                           "A nagy modellek letöltése nem kezdődött el.\n" + probe["output"][-3000:])
    _emit(log, "Videokártya ellenőrizve: " + "; ".join(d["description"] for d in probe["devices"]))
    if not binaries_only:
        remaining = sum(asset["size"] or 4_200_000_000 for asset in ASSETS[1:]
                        if not _asset_ready(root, asset, record))
        if shutil.disk_usage(root).free < remaining + 1_000_000_000:
            raise RuntimeError("A videómotor modelljeihez nincs elég szabad lemezterület (kb. 11 GB szükséges).")
        for asset in ASSETS[1:]:
            _download(root, asset, record, log, cancel)
        record["complete"] = True
        _write_manifest(root, record)
        _emit(log, "A helyi AI mozgóképmotor és mindhárom modell telepítve.")
    return root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Helyi AI mozgóképmotor telepítése")
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--binaries-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.target or default_target()).resolve()
    if args.check:
        result = check(root, args.binaries_only)
        try:
            print(json.dumps(result, ensure_ascii=False))
        except (OSError, UnicodeError):
            print(json.dumps(result, ensure_ascii=True))
        return 0 if result["ready"] else 1
    root.mkdir(parents=True, exist_ok=True)
    def logger(message):
        try:
            with (root / "setup.log").open("a", encoding="utf-8") as stream:
                stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
        except OSError:
            pass
        try:
            print(message, flush=True)
        except (OSError, UnicodeError):
            pass
    try:
        setup(root, logger, None, args.binaries_only)
        return 0
    except KeyboardInterrupt:
        logger("A letöltés megszakadt; a következő indítás folytatja.")
        return 130
    except Exception as error:
        logger("HIBA: " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
