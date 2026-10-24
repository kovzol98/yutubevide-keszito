"""Pinned, resumable installation for the quality photo animation profile."""
from __future__ import annotations
import json,shutil,zipfile
from pathlib import Path
import setup_ai
from realistic_video import asset_home,engine_files

VERSION='wan22-i2v14b-lightx260412-coherent-motion-v2'

def assets():
    home=asset_home()
    return json.loads((home/'realism_assets.json').read_text(encoding='utf-8'))+[
        json.loads((home/'face_restore_vendor/model_asset.json').read_text(encoding='utf-8')),
        json.loads((home/'rife_asset.json').read_text(encoding='utf-8'))]

def setup(target,log=print,cancel=None):
    from windows_support import require_amd_windows
    require_amd_windows()
    import setup_comfy,setup_portrait,portrait_video
    root=Path(target).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    setup_ai._guard(cancel)
    if not setup_comfy.check(root)['ready']:setup_comfy.setup(root,log,cancel)
    try:portrait_video.engine_files(root)
    except RuntimeError:setup_portrait.setup(root,log,cancel)
    record=setup_ai._read_manifest(root)
    plan=assets()
    missing=sum(a['size'] for a in plan if not (root/a['path']).is_file())
    if shutil.disk_usage(root).free<missing+5_000_000_000:
        raise RuntimeError('Az élethű kiegészítéshez még legalább %.1f GB szabad lemezterület szükséges.'%((missing+5_000_000_000)/1e9))
    for asset in plan:
        setup_ai._guard(cancel);setup_ai._download(root,asset,record,log,cancel)
    with zipfile.ZipFile(root/plan[-1]['path']) as archive:
        expected=('rife-ncnn-vulkan.exe','rife-v4.6/flownet.bin','rife-v4.6/flownet.param','LICENSE','README.md')
        for relative in expected:
            setup_ai._guard(cancel)
            matches=[info for info in archive.infolist() if info.filename.endswith('/'+relative)]
            if len(matches)!=1 or matches[0].file_size>50*1024**2:raise RuntimeError('Érvénytelen mozgássimító-archívum.')
            out=root/'interpolation'/relative;out.parent.mkdir(parents=True,exist_ok=True)
            temporary=out.with_suffix(out.suffix+'.tmp');temporary.write_bytes(archive.read(matches[0]));temporary.replace(out)
    addon=root/'ComfyUI_windows_portable/ComfyUI/custom_nodes/YouTubeMuhely_RAM'
    addon.mkdir(parents=True,exist_ok=True)
    source=asset_home()/'tools/quality_nodes/__init__.py'
    temporary=addon/'__init__.py.tmp';shutil.copyfile(source,temporary);temporary.replace(addon/'__init__.py')
    manifest=root/'realism_installation.json';temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(version=VERSION,complete=True,assets=plan),indent=2),encoding='utf-8')
    temporary.replace(manifest);engine_files(root)
    log('Az élethű motor és az egységes jelenetfeldolgozás használatra kész.')
    return root

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('target');args=parser.parse_args()
    setup(args.target,lambda value:print(value,flush=True))
