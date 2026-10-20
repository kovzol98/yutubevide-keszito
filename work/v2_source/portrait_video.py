"""Bounded neural portrait motion, generated locally without a Comfy queue."""
from __future__ import annotations
import json, math, os, subprocess, sys, time
from pathlib import Path

PORTRAIT_MOTION = 'Portré • finom fejmozgás'

def engine_files(root):
    portrait = Path(root).expanduser().resolve() / 'portrait'
    try:
        record = json.loads((portrait / 'installed.json').read_text(encoding='utf-8'))
        if not record.get('complete'): raise ValueError('incomplete')
        for path, size in record['sizes'].items():
            if (portrait / path).stat().st_size != size: raise ValueError(path)
        for path in ('python/python.exe', 'code/src/live_portrait_wrapper.py', 'packages/mediapipe/__init__.py'):
            if not (portrait / path).is_file(): raise ValueError(path)
    except (OSError, ValueError, KeyError):
        raise RuntimeError('Hiányzik a helyi portrémotor. Az AI-motor és erőforrások résznél kattints a Portrémotor letöltése gombra.') from None
    return portrait

def worker_path():
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    for folder in (Path(getattr(sys, '_MEIPASS', home)), home, Path(__file__).resolve().parent):
        path = folder / 'liveportrait_worker.py'
        if path.is_file(): return path
    raise RuntimeError('Hiányzik a portrémotor indítófájlja. Csomagold ki a teljes programot.')

def generate(engine, project, image, index, work, duration):
    from ytmuhely import ffmpeg
    engine.check()
    portrait = engine_files(project.ai_root)
    enhanced=getattr(project,'enhancement','Finom képjavítás')=='Finom képjavítás'
    if not image: raise ValueError('A portrémódhoz adj hozzá egy arcképet.')
    scene = work.parent / 'AI_jelenetek' / ('jelenet_%03d' % (index + 1)); scene.mkdir(parents=True, exist_ok=True)
    output = scene / 'portre.mp4'
    config = dict(vendor=str(portrait / 'code'), models=str(portrait / 'models'), image=str(Path(image).resolve()),
                  output=str(output), ffmpeg=ffmpeg(), duration=duration, fps=30, strength=1.0, half_precision=True,
                  face_detector=str(portrait / 'models/blaze_face_short_range.tflite'), save_debug=True)
    config.update(composite_mode='source_detail' if enhanced else 'model',clean_source=enhanced,intermediate_crf=14 if enhanced else 17)
    path = scene / 'portre_beallitas.json'; path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    engine.log('Finom fejmozgás készítése a helyi portrémotorral…')
    logfile = scene / 'portremotor.log'
    with logfile.open('wb', buffering=0) as capture:
        process = subprocess.Popen([str(portrait / 'python/python.exe'), str(worker_path()), str(path)],
            cwd=portrait, stdin=subprocess.DEVNULL, stdout=capture, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        started = time.monotonic(); last_report = started
        try:
            while process.poll() is None:
                engine.check()
                if time.monotonic() - started > max(600, duration * 40): raise RuntimeError('A portrémotor túllépte az időkorlátot.')
                if time.monotonic() - last_report >= 5:
                    lines=logfile.read_text(encoding='utf-8',errors='replace').splitlines()
                    state=None
                    for line in reversed(lines):
                        try: candidate=json.loads(line)
                        except ValueError: continue
                        if isinstance(candidate,dict) and candidate.get('event') in ('progress','loading','detecting'): state=candidate; break
                    if state and state.get('event')=='progress':
                        frame,total=state['frame'],state['total']
                        engine.log('Portré: %d/%d képkocka (%d%%) · %d mp eltelt.' % (frame,total,round(100*frame/total),time.monotonic()-started))
                        if hasattr(engine,'report_scene_progress'): engine.report_scene_progress(95*frame/total)
                    else:
                        engine.log('A portrémodell előkészítése… %d mp eltelt.' % (time.monotonic()-started))
                    last_report = time.monotonic()
                time.sleep(.15)
        except BaseException:
            process.kill(); process.wait(timeout=15); raise
    if process.returncode or not output.is_file():
        raise RuntimeError('A portré animálása nem sikerült.\n' + logfile.read_text(encoding='utf-8', errors='replace')[-2500:])
    manifest = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    engine.log('Elkészült a portré: %.1f másodperc, %d képkocka.' % (manifest['duration'], manifest['nframes']))
    return output
