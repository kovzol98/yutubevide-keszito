"""Local Wan 2.2 motion generation through the bundled AMD ComfyUI server.

The application supplies an engine with log(), check(), and run() methods.
Only this module's own queue entry is removed on cancellation. Existing servers
are never interrupted or terminated. cleanup(engine) stops an owned server.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageOps

PRESETS = {
    "Normál • 832×480": (832, 480, 24, 20),
    "Részletes • 1280×704": (1280, 704, 24, 30),
}

def normalized_quality(value):
    if value in PRESETS: return value
    if value.startswith('Gyors próba •'): return 'Normál • 832×480'
    prefix=value.split(' •',1)[0]
    for name in PRESETS:
        if name.startswith(prefix+' •'): return name
    raise ValueError('Válassz érvényes AI-részletességet.')
MODEL_NAMES = {
    "unet_name": "Wan2.2-TI2V-5B-Q5_K_M.gguf",
    "clip_name": "umt5-xxl-encoder-Q5_K_M.gguf",
    "vae_name": "wan2.2_vae.safetensors",
}
TURBO_MODEL_NAME = "Wan2_2-TI2V-5B-Turbo-Q5_K_M.gguf"


def selected_models(root):
    # A local community variant must not silently replace the default model.
    # Explicit internal workflows can still provide their own model_names.
    return dict(MODEL_NAMES)
FALLBACK_URL = "http://127.0.0.1:8190"
EXISTING_SERVER_URLS = (
    "http://127.0.0.1:8000",  # Comfy Desktop
    "http://127.0.0.1:8188",  # Standard ComfyUI
    FALLBACK_URL,
)


def _portable(root: Path) -> Path:
    for candidate in (root / "ComfyUI_windows_portable", root):
        if (candidate / "ComfyUI/main.py").is_file() and (candidate / "python_embeded/python.exe").is_file():
            return candidate
    raise RuntimeError("Hiányzik a helyi ComfyUI videómotor. Az AI-motor telepítése gombbal telepítheted.")


def engine_files(root):
    root = Path(root).expanduser().resolve()
    portable = _portable(root)
    names = selected_models(root)
    models = [root / "models/diffusion_models" / names["unet_name"],
              root / "models/text_encoders" / names["clip_name"],
              root / "models/vae" / names["vae_name"]]
    if any(not model.is_file() or model.stat().st_size == 0 for model in models):
        raise RuntimeError("Hiányzik valamelyik AI-videómodell. Az AI-motor telepítése gomb folytatja a letöltést.")
    return portable / "python_embeded/python.exe", models


def _request(base, route, payload=None, timeout=15):
    request = urllib.request.Request(base + route,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"} if payload is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            # ComfyUI's /free acknowledges its flags with an empty HTTP 200.
            # Queue and history responses still require valid JSON.
            if route == "/free" and not body:
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError("A helyi AI-motor hibát jelzett (%s): %s" % (error.code, detail)) from error


def _queue_prompt_ids(queue):
    """Require a complete queue response before changing shared server state."""
    if not isinstance(queue, dict):
        raise ValueError('Invalid AI server queue response')
    groups = []
    for field in ('queue_running', 'queue_pending'):
        entries = queue.get(field)
        if not isinstance(entries, list):
            raise ValueError('Missing or invalid AI server queue list: ' + field)
        ids = set()
        for entry in entries:
            if (not isinstance(entry, list) or len(entry) < 2 or
                not isinstance(entry[1], str) or not entry[1].strip()):
                raise ValueError('Invalid AI server queue entry: ' + field)
            ids.add(entry[1])
        groups.append(ids)
    return tuple(groups)


def _probe(base):
    try:
        result = _request(base, "/system_stats", timeout=2)
        return isinstance(result, dict) and "system" in result
    except (OSError, ValueError, RuntimeError):
        return False


def _compatible_server(base, model_names=None, required_extra=()):
    """Inspect an existing server without changing its models or queue."""
    model_names = model_names or MODEL_NAMES
    if not _probe(base):
        return False
    try:
        information = _request(base, "/object_info", timeout=15)
        required_nodes = ("UnetLoaderGGUF", "CLIPLoaderGGUF", "VAELoader",
                          "ModelSamplingSD3", "CLIPTextEncode", "KSampler",
                          "Wan22ImageToVideoLatent", "SaveImage", "LoadImage")
        required_nodes += tuple(required_extra)
        if not isinstance(information, dict) or any(name not in information for name in required_nodes):
            return False
        if not any(name in information for name in ("VAEDecodeTiled", "VAEDecode")):
            return False
        for kind, field in (("UnetLoaderGGUF", "unet_name"),
                            ("CLIPLoaderGGUF", "clip_name"), ("VAELoader", "vae_name")):
            inputs = information[kind]["input"]
            schema = inputs.get("required", {}).get(field) or inputs.get("optional", {}).get(field)
            if not schema or not isinstance(schema[0], list) or model_names[field] not in schema[0]:
                return False
        # Also check the node schemas and Wan/sampler choices used by this program.
        _workflow(information, "Compatibility check", 832, 480, 33, 20, 42, "YouTubeMuhely/check", model_names=model_names)
        return True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
        return False


def _free_port():
    for number in range(8190, 8220):
        with socket.socket() as connection:
            try:
                connection.bind(("127.0.0.1", number))
                return number
            except OSError:
                continue
    raise RuntimeError("Nem található szabad helyi csatlakozási port az AI-motornak.")


def cleanup(engine, terminate=True):
    """Close the server owned by this engine; never close an existing server."""
    process = getattr(engine, "_comfy_process", None)
    if terminate and process is not None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)
        engine._comfy_process = None
        engine._comfy_url = None
        engine._comfy_root = None
        engine._comfy_prompt_id = None
    handle = getattr(engine, "_comfy_log_handle", None)
    if handle is not None and (terminate or process is None or process.poll() is not None):
        handle.close()
        engine._comfy_log_handle = None


def stop_engine(engine):
    """Application-finally cleanup; only stop the process created by this engine."""
    process=getattr(engine,'_comfy_process',None)
    base=getattr(engine,'_comfy_url',None)
    if process is not None and process.poll() is None:
        try:
            queue=_request(base,'/queue',timeout=5)
            running,pending=_queue_prompt_ids(queue)
            queued=running|pending
            leave_running=bool(queued-{getattr(engine,'_comfy_prompt_id',None)})
        except (OSError,ValueError,RuntimeError):
            # When queue ownership cannot be proved, keep other clients safe.
            leave_running=True
        if (getattr(engine,'_comfy_process',None) is not process or
                getattr(engine,'_comfy_url',None)!=base):
            engine.log('A helyi AI-motor állapota megváltozott; az új folyamatot nem állítottam le.')
            return
        if leave_running:
            handle=getattr(engine,'_comfy_log_handle',None)
            if handle is not None:handle.close()
            for name in ('_comfy_process','_comfy_log_handle','_comfy_url','_comfy_root','_comfy_prompt_id'):
                setattr(engine,name,None)
            engine.log('A helyi AI-motort futva hagytam, mert másik feladatot is végezhet.')
            return
    cleanup(engine, terminate=True)


def _cleanup_log(log, action, error):
    """Best-effort diagnostics must never replace the triggering exception."""
    try:log('%s nem sikerült: %s'%(action,error))
    except BaseException:pass


def _ensure_server(engine, root, model_names=None, required_extra=(), force_owned=False):
    engine.check()
    cached = getattr(engine, "_comfy_url", None)
    if cached and getattr(engine, "_comfy_root", None) == str(root):
        owned = getattr(engine, "_comfy_process", None)
        if owned is not None:
            if owned.poll() is None and _probe(cached):
                return cached
        elif not force_owned and _compatible_server(cached, model_names, required_extra):
            return cached
    configured = getattr(engine, "comfy_url", None) or FALLBACK_URL
    # The production integration accepts loopback endpoints only.
    parsed = urllib.parse.urlparse(configured)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Az AI-motor címe csak helyi számítógépes kapcsolat lehet.")
    if getattr(engine, "_comfy_process", None) is not None:
        cleanup(engine)
    candidates = dict.fromkeys((*EXISTING_SERVER_URLS, configured.rstrip("/")))
    for base in candidates:
        engine.check()
        if not force_owned and _compatible_server(base, model_names, required_extra):
            engine._comfy_process = None
            engine._comfy_url, engine._comfy_root = base, str(root)
            engine.log("A már futó ComfyUI-t használom.")
            return base
    portable = _portable(root)
    model_root = str(root / "models").replace("\\", "/")
    configuration = "youtube_muhely_shared:\n  base_path: %s\n  diffusion_models: diffusion_models\n  unet: diffusion_models\n  text_encoders: text_encoders\n  clip: text_encoders\n  vae: vae\n" % json.dumps(model_root)
    yaml = root / "youtube_muhely_models.yaml"
    yaml.write_text(configuration, encoding="utf-8")
    port = _free_port()
    base = "http://127.0.0.1:%d" % port
    log_path = root / "comfy_motor.log"
    handle = log_path.open("ab", buffering=0)
    command = [str(portable / "python_embeded/python.exe"), "-s", str(portable / "ComfyUI/main.py"),
               "--windows-standalone-build", "--listen", "127.0.0.1", "--port", str(port),
               "--use-split-cross-attention", "--disable-async-offload", "--disable-pinned-memory",
               "--extra-model-paths-config", str(yaml)]
    try:
        process = subprocess.Popen(command, cwd=str(portable), stdout=handle, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except BaseException:
        handle.close()
        raise
    engine._comfy_process, engine._comfy_log_handle = process, handle
    engine._comfy_url, engine._comfy_root = base, str(root)
    engine._comfy_server_log = log_path
    engine.log("A helyi AI-videómotor indul: a szöveg CPU-n, az animáció és a képfeldolgozás GPU-val készül…")
    started, last_report = time.monotonic(), 0.0
    try:
        while time.monotonic() - started < 180:
            engine.check()
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-3500:]
                raise RuntimeError("A helyi AI-videómotor nem indult el.\n" + tail)
            if _probe(base):
                return base
            if time.monotonic() - last_report > 20:
                engine.log("Az AI-motor betöltése folyamatban…")
                last_report = time.monotonic()
            time.sleep(0.3)
        raise RuntimeError("Az AI-motor három percen belül nem indult el. Részletek: " + str(log_path))
    except BaseException:
        cleanup(engine)
        raise


def _upload_image(base, image: Path):
    boundary = "YouTubeMuhely" + uuid.uuid4().hex
    filename = "reference_" + uuid.uuid4().hex + ".png"
    segments = []
    for name, value in (("type", "input"), ("overwrite", "false")):
        segments.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" %
                         (boundary, name, value)).encode())
    segments.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" %
                     (boundary, filename)).encode())
    segments.append(image.read_bytes())
    segments.append(("\r\n--%s--\r\n" % boundary).encode())
    request = urllib.request.Request(base + "/upload/image", data=b"".join(segments),
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(request, timeout=30) as response:
        uploaded = json.load(response)
    return "/".join(part for part in (uploaded.get("subfolder", ""), uploaded["name"]) if part)


def _workflow(information, prompt, width, height, frames, steps, seed, prefix, reference=None, lighting='Eredeti világítás', image_motion=None, model_names=None):
    from video_look import lighting_negative
    from image_motion import image_motion_negative
    names = model_names or MODEL_NAMES
    turbo = names['unet_name'] == TURBO_MODEL_NAME
    negative=(image_motion_negative(image_motion) if reference else lighting_negative(lighting, "still image, frozen motion, text, subtitles, watermark, blurry, low quality, deformed anatomy"))
    def node(kind, values):
        if kind not in information:
            raise RuntimeError("Hiányzó AI-motor összetevő: %s. Telepítsd újra az AI-motort." % kind)
        required = information[kind]["input"].get("required", {})
        inputs = dict(values)
        for name, schema in required.items():
            if name in inputs:
                if isinstance(schema[0], list) and inputs[name] not in schema[0]:
                    raise RuntimeError("Az AI-motorból hiányzik a kiválasztott modell vagy beállítás: %s" % inputs[name])
                continue
            details = schema[1] if len(schema) > 1 and isinstance(schema[1], dict) else {}
            if "default" in details:
                inputs[name] = details["default"]
            elif isinstance(schema[0], list) and schema[0]:
                inputs[name] = schema[0][0]
            else:
                raise RuntimeError("Hiányzó AI-motor beállítás: %s.%s" % (kind, name))
        return {"class_type": kind, "inputs": inputs}
    latent = {"vae": ["3", 0], "width": width, "height": height, "length": frames, "batch_size": 1}
    if reference:
        latent["start_image"] = ["11", 0]
    # Every temporal tile restarts the causal Wan VAE's cache. Short windows
    # produced a double silhouette every twelve frames. Keep the entire scene
    # in one temporal window; only split spatially when resolution needs it.
    # VAEDecode can silently shorten temporal windows on OOM. Explicit tiled
    # decoding keeps the full clip even under pressure; recovery only reduces
    # spatial tile size, never time context.
    decoder = "VAEDecodeTiled"
    if decoder not in information:
        raise RuntimeError('A videómotor nem támogatja az egybefüggő jelenetfeldolgozást. Frissítsd a programhoz mellékelt ComfyUI-t.')
    decoder_inputs = {"samples": ["7", 0], "vae": ["3", 0]}
    if decoder == "VAEDecodeTiled":
        supported = {**information[decoder]["input"].get("required", {}),
                     **information[decoder]["input"].get("optional", {})}
        if not {'temporal_size','temporal_overlap'}<=set(supported):
            raise RuntimeError('A videómotor időbeli dekódolási beállításai hiányoznak. Frissítsd a ComfyUI-t.')
        temporal_size=max(64,4*math.ceil(frames/4))
        if temporal_size>4096:raise ValueError('A jelenet túl hosszú az egybefüggő videófeldolgozáshoz. Rövidebb jeleneteket használj.')
        tile = {"tile_size": 512, "overlap": 64, "temporal_size": temporal_size, "temporal_overlap": 4}
        for name, value in tile.items():
            if name in supported:
                decoder_inputs[name] = value
    graph = {
        "1": node("UnetLoaderGGUF", {"unet_name": names["unet_name"]}),
        "2": node("CLIPLoaderGGUF", {"clip_name": names["clip_name"], "type": "wan"}),
        "3": node("VAELoader", {"vae_name": names["vae_name"]}),
        "4": node("ModelSamplingSD3", {"model": ["1", 0], "shift": 8.0}),
        "5": node("CLIPTextEncode", {"clip": ["2", 0], "text": prompt}),
        "6": node("CLIPTextEncode", {"clip": ["2", 0], "text": negative}),
        "7": node("KSampler", {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                                  "latent_image": ["8", 0], "seed": seed, "steps": 4 if turbo else steps, "cfg": 1.0 if turbo else 5.0,
                                  "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}),
        "8": node("Wan22ImageToVideoLatent", latent),
        "9": node(decoder, decoder_inputs),
        "10": node("SaveImage", {"images": ["9", 0], "filename_prefix": prefix}),
    }
    if reference:
        graph["11"] = node("LoadImage", {"image": reference})
    return graph


def _cancel_prompt(engine, base, prompt_id, log):
    if not prompt_id:
        return
    try:
        _request(base, "/queue", {"delete": [prompt_id]}, timeout=3)
        queue = _request(base, "/queue", timeout=3)
        running, pending = _queue_prompt_ids(queue)
        process = getattr(engine, "_comfy_process", None)
        if process is not None and getattr(engine, "_comfy_url", None) == base:
            # Terminating an exclusive owned server cancels its GPU computation.
            # If another client has added work, leave that server/job untouched.
            if not ((running | pending) - {prompt_id}):
                cleanup(engine)
                log("A saját AI-generálást és a saját videómotort leállítottam.")
        elif prompt_id in running:
            log("A videókészítés megszakadt. A külön futó AI-motor már elindított jelenete a háttérben befejeződhet.")
    except (OSError, ValueError, RuntimeError):
        pass


def generate(engine, p, text, image, index, work, duration, *, backend=None, scene_dir=None, canvas=None):
    root = Path(p.ai_root).expanduser().resolve()
    (backend.engine_files if backend else engine_files)(root)
    models = dict(backend.MODELS) if backend else selected_models(root)
    turbo = models['unet_name'] == TURBO_MODEL_NAME
    quality = normalized_quality(p.ai_quality)
    width, height, fps, steps = (backend.PRESETS if backend else PRESETS)[quality]
    if turbo: steps = 4
    from image_motion import source_dimensions, prepare_reference, image_motion_prompt, DEFAULT_IMAGE_MOTION
    selected_motion=getattr(p,'image_motion',DEFAULT_IMAGE_MOTION)
    if canvas is not None:
        width,height=canvas
    elif image:
        width,height=source_dimensions(image,width,height)
    elif "Shorts" in p.format:
        width, height = height, width
    if not math.isfinite(duration) or duration<=0:
        raise ValueError('A jelenet hosszának pozitív értéknek kell lennie.')
    # Generate enough real frames for the entire scene; AI footage never loops.
    frames=1+4*math.ceil(max(0,fps*duration-1)/4)
    if backend:
        from video_workload import validate_segment
        validate_segment(width,height,frames,quality)
    prompts = [part.strip() for part in re.split(r"\n\s*\n", p.video_prompt.strip()) if part.strip()]
    prompt = prompts[min(index, len(prompts) - 1)] if prompts else ('' if image else (text.strip() or p.title.strip()))
    from video_look import lighting_prompt, DEFAULT_LIGHTING
    selected_lighting=getattr(p,'lighting',DEFAULT_LIGHTING)
    if image:
        prompt=image_motion_prompt(selected_motion,prompt)
    else:
        prompt += " Natural continuous movement of the subjects, realistic motion, cinematic video, no text or captions."
        prompt=lighting_prompt(selected_lighting,prompt)
    target = Path(work).parent / "AI_jelenetek"
    target.mkdir(parents=True, exist_ok=True)
    scene = Path(scene_dir) if scene_dir is not None else target / ("jelenet_%03d" % (index + 1))
    scene.mkdir(parents=True,exist_ok=True)
    (scene / "prompt.txt").write_text(prompt, encoding="utf-8")
    log_path = scene / "generalas.log"
    def log(message):
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
        engine.log(message)
    if backend and hasattr(backend,'prepare_runtime'):backend.prepare_runtime(engine,root)
    base = _ensure_server(engine, root, models, getattr(backend,'EXTRA_NODES',()), force_owned=bool(backend))
    information = _request(base, "/object_info", timeout=30)
    uploaded = None
    if image:
        reference = scene / "reference.png"
        reference_info=prepare_reference(image,reference,width,height)
        (scene/'reference_info.json').write_text(json.dumps(reference_info,ensure_ascii=False,indent=2),encoding='utf-8')
        engine.check()
        uploaded = _upload_image(base, reference)
        # LoadImage's enum changes after upload; refresh it before local validation.
        information.update(_request(base, "/object_info/LoadImage", timeout=15))
    prefix = "YouTubeMuhely/jelenet_%03d_%s" % (index + 1, uuid.uuid4().hex[:10])
    builder=backend.workflow if backend else _workflow
    graph = builder(information, prompt, width, height, frames, steps, 42 + index, prefix, uploaded, selected_lighting, selected_motion, model_names=models)
    (scene / "workflow_api.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    (scene/'video_settings.json').write_text(json.dumps(dict(width=width,height=height,frames=frames,fps=fps,steps=steps,quality=quality,model=models['unet_name'],decoder=graph['9']['class_type'],decoder_options={k:v for k,v in graph['9']['inputs'].items() if k not in ('samples','vae')}),indent=2),encoding='utf-8')
    prompt_id = None
    monitor = None
    recovery = None
    health = None
    primary_error = None
    try:
        engine.check()
        from gpu_health import begin_monitor,GpuResultError
        health=begin_monitor(cancel=engine.check,report_path=scene/'gpu_health.json')
        engine.check()
        client_id = uuid.uuid4().hex
        accepted = _request(base, "/prompt", {"prompt": graph, "client_id": client_id}, timeout=30)
        if accepted.get("node_errors"):
            raise RuntimeError("Az AI-motor jelenetbeállításai hibásak: " + json.dumps(accepted["node_errors"]))
        prompt_id = accepted["prompt_id"]
        engine._comfy_prompt_id=prompt_id
        from comfy_progress import JobProgress
        monitor = JobProgress(base, client_id, prompt_id); monitor.start()
        (scene / "prompt_id.txt").write_text(prompt_id, encoding="utf-8")
        log("%d. %s jelenet (%d×%d, %d képkocka, %d lépés). %s" % (index + 1,
            backend.LABEL if backend else ('Gyorsított Wan Turbo' if turbo else 'Wan'), width, height, frames, steps,
            'Alacsony felbontású mozgáspróba.' if width*height<=512*288 else
            ('A képkockák véglegesítése külön időt kér.' if backend or turbo else 'Normál 5 másodperc ezen a gépen 30 percnél is tovább tarthat.')))
        started, last_report, last_clock_report, last_detail = time.monotonic(), 0.0, 0.0, ''
        while True:
            engine.check()
            health.check()
            history = _request(base, "/history/" + urllib.parse.quote(prompt_id), timeout=15)
            if prompt_id in history:
                result = history[prompt_id]
                status = result.get("status", {})
                if status.get("status_str") == "error":
                    (scene / "history.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    health.check(force=True)
                    if 'YTMUHELY_INVALID_GPU_RESULT' in json.dumps(status,ensure_ascii=False):
                        raise GpuResultError()
                    if backend and recovery is None:
                        from comfy_recovery import decode_recovery
                        original_process=getattr(engine,'_comfy_process',None)
                        plan=decode_recovery(engine,base,prompt_id,result,graph,prefix,frames,
                            _portable(root)/'ComfyUI/output',information,_request)
                        if plan is not None:
                            engine.check()
                            recovery=dict(attempt=1,checkpoint=plan['checkpoint'],original_error=plan['original_error'],
                                decoder=plan['graph']['9'],resampled=False,decoded=False,
                                frames_published=False,encoded=False,completed=False,submitted=False)
                            (scene/'history_before_decode_retry.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                            (scene/'decode_retry_workflow.json').write_text(json.dumps(plan['graph'],ensure_ascii=False,indent=2),encoding='utf-8')
                            (scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
                            log('A jelenet számítása elkészült; a képkockák feldolgozását kisebb memóriaigénnyel egyszer újrapróbálom.')
                            monitor.close();monitor=None
                            engine.check()
                            if (original_process is None or original_process.poll() is not None or
                                getattr(engine,'_comfy_process',None) is not original_process or
                                getattr(engine,'_comfy_url',None)!=base or
                                getattr(engine,'_comfy_prompt_id',None)!=prompt_id):
                                raise RuntimeError('A videómotor állapota megváltozott. A mentett jelenet megmaradt; új számítást nem indítottam.')
                            try:
                                queue=_request(base,'/queue',timeout=5)
                            except (OSError,ValueError,RuntimeError) as error:
                                raise RuntimeError('A videómotor állapota nem ellenőrizhető. A mentett jelenet megmaradt; új számítást nem indítottam.') from error
                            if queue.get('queue_running')!=[] or queue.get('queue_pending')!=[]:
                                raise RuntimeError('A videómotor közben másik feladatot kapott. A mentett jelenet megmaradt; új számítást nem indítottam.')
                            engine.check()
                            if (original_process.poll() is not None or
                                getattr(engine,'_comfy_process',None) is not original_process or
                                getattr(engine,'_comfy_url',None)!=base or
                                getattr(engine,'_comfy_prompt_id',None)!=prompt_id):
                                raise RuntimeError('A videómotor állapota megváltozott. A mentett jelenet megmaradt; új számítást nem indítottam.')
                            client_id=uuid.uuid4().hex
                            try:
                                accepted=_request(base,'/prompt',dict(prompt=plan['graph'],client_id=client_id),timeout=30)
                            except (OSError,ValueError,RuntimeError) as error:
                                raise RuntimeError('A képkockák helyreállítását nem tudtam elindítani. A mentett jelenet megmaradt. Részletek: '+str(scene/'decode_retry.json')) from error
                            if accepted.get('node_errors'):
                                raise RuntimeError('A képkockák helyreállításának beállításai hibásak: '+json.dumps(accepted['node_errors']))
                            prompt_id=accepted['prompt_id'];engine._comfy_prompt_id=prompt_id
                            recovery.update(retry_prompt_id=prompt_id,submitted=True)
                            (scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
                            monitor=JobProgress(base,client_id,prompt_id);monitor.start()
                            continue
                    if recovery is not None:
                        raise RuntimeError('A képkockák egyszeri helyreállítása nem sikerült. A mentett jelenet megmaradt; új AI-generálás nem indult. Részletek: '+str(scene/'history.json'))
                    raise RuntimeError("Az AI-motor nem tudta elkészíteni a jelenetet: " + json.dumps(status, ensure_ascii=False)[-3500:])
                if status.get("completed") or result.get("outputs"):
                    health.check(force=True)
                    break
            owned = getattr(engine, "_comfy_process", None)
            if owned is not None and owned.poll() is not None:
                raise RuntimeError("A helyi AI-videómotor generálás közben leállt. Részletek: " + str(root / "comfy_motor.log"))
            if time.monotonic() - started > 7200:
                raise RuntimeError("Az AI-generálás túllépte a kétórás jelenetenkénti időkorlátot.")
            if time.monotonic() - last_report >= 2:
                detail, percent = monitor.describe()
                if detail and (detail != last_detail or time.monotonic() - last_clock_report >= 20):
                    log('%d. %s · %d mp eltelt.' % (index + 1, detail, time.monotonic() - started))
                    if percent is not None and hasattr(engine,'report_scene_progress'): engine.report_scene_progress(percent)
                    last_detail = detail; last_clock_report = time.monotonic()
                elif not detail and time.monotonic() - last_clock_report >= 20:
                    queue = _request(base, "/queue", timeout=5)
                    pending = any(len(entry) > 1 and entry[1] == prompt_id for entry in queue.get("queue_pending", []))
                    log("%d. Wan-jelenet %s… %d másodperc eltelt." %
                        (index + 1, "várakozik" if pending else "készül", int(time.monotonic() - started)))
                    last_clock_report = time.monotonic()
                last_report = time.monotonic()
            time.sleep(0.25)
        (scene / "history.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = result.get("outputs", {}).get("10", {}).get("images", [])
        if not saved or len(saved) < frames:
            raise RuntimeError("Az AI-motor nem adott vissza minden szükséges képkockát.")
        if recovery is not None:
            recovery['decoded']=True
            (scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
            settings=json.loads((scene/'video_settings.json').read_text(encoding='utf-8'))
            settings.update(decoder=recovery['decoder']['class_type'],decoder_options={
                key:value for key,value in recovery['decoder']['inputs'].items() if key not in ('samples','vae')})
            (scene/'video_settings.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        from frame_storage import staging_folder,publish_folder
        # An interrupted/repeated run cannot append to a previous sequence.
        frame_folder = staging_folder(scene,'frames')
        for number, frame in enumerate(saved):
            engine.check()
            if number%12==0:
                log('Képkockák átvétele: %d/%d' % (number+1,len(saved)))
                if hasattr(engine,'report_scene_progress'): engine.report_scene_progress(90+8*(number+1)/len(saved))
            query = urllib.parse.urlencode({"filename": frame["filename"], "subfolder": frame.get("subfolder", ""),
                                           "type": frame.get("type", "output")})
            destination = frame_folder / ("frame_%05d.png" % number)
            temporary = destination.with_suffix(".part")
            with urllib.request.urlopen(base + "/view?" + query, timeout=30) as response:
                with temporary.open("wb") as stream:
                    while True:
                        engine.check()
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
            os.replace(temporary, destination)
        from frame_integrity import validate_sequence
        log('Az AI-képkockák épségének ellenőrzése…')
        validate_sequence(engine,frame_folder,frames,(width,height),
                          reference=scene/'reference.png' if image else None,
                          report_path=scene/'frame_integrity.json')
        health.check(force=True)
        frame_folder=publish_folder(frame_folder,scene/'frames')
        if recovery is not None:
            recovery['frames_published']=True
            (scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
        from imageio_ffmpeg import get_ffmpeg_exe
        video = scene / "video.mp4"
        partial = scene / "video.partial.mp4"
        engine.run([get_ffmpeg_exe(), "-y", "-framerate", str(fps), "-start_number", "0", "-i",
                    frame_folder / "frame_%05d.png", "-frames:v", str(len(saved)), "-c:v", "libx264",
                    "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", partial])
        os.replace(partial, video)
        if recovery is not None:
            recovery.update(encoded=True,completed=True)
            (scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
        log("%d. AI-jelenet elkészült. A narrációhoz igazítom." % (index + 1))
        return video
    except BaseException as error:
        primary_error=error
        terminal=error
        if health is not None and type(error).__name__!='Cancelled':
            try:health.check(force=True)
            except Exception as health_error:
                if getattr(health_error,'halt_queue',False):terminal=health_error
        if recovery is not None:
            recovery.update(completed=False,error_type=type(error).__name__,error=str(error))
            try:(scene/'decode_retry.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError:pass
        try:_cancel_prompt(engine, base, prompt_id, log)
        except BaseException as cleanup_error:
            _cleanup_log(log,'A hibás AI-feladat leállítása',cleanup_error)
        try:(scene / "video.partial.mp4").unlink(missing_ok=True)
        except BaseException as cleanup_error:
            _cleanup_log(log,'A félkész videófájl eltávolítása',cleanup_error)
        if terminal is not error:raise terminal from error
        raise
    finally:
        if monitor is not None:
            try:monitor.close()
            except BaseException as cleanup_error:
                if primary_error is None:raise
                _cleanup_log(log,'Az AI-folyamat figyelésének lezárása',cleanup_error)
