"""One coherent generated scene: source preparation, Wan video, interpolation."""
from __future__ import annotations
import json,subprocess,sys,time
from pathlib import Path
import comfy_video

QUALITY_ENGINE='Élethű • arc és fény'
LEGACY_ENGINE='Alap • korábbi motor'
IMAGE_ENGINES=(QUALITY_ENGINE,LEGACY_ENGINE)
LABEL='Élethű jelenet'
EXTRA_NODES=('WanImageToVideo','YouTubeWan14RamSampler','VAEDecodeTiled','SaveLatent')
HIGH='wan2.2_i2v_A14b_high_noise_lightx2v_4step_720p_260412-Q4_K_M.gguf'
LOW='wan2.2_i2v_A14b_low_noise_lightx2v_4step_720p_260412-Q4_K_M.gguf'
MODELS=dict(unet_name=HIGH,clip_name=comfy_video.MODEL_NAMES['clip_name'],vae_name='wan_2.1_vae.safetensors')
PRESETS={'Normál • 832×480':(640,480,16,4),'Részletes • 1280×704':(1280,704,16,4)}

def asset_home():
    home=Path(__file__).resolve().parent
    for candidate in (Path(getattr(sys,'_MEIPASS',home)),home,Path(sys.executable).parent):
        if (candidate/'realism_assets.json').is_file():return candidate
    raise RuntimeError('Hiányzik az élethű motor fájllistája. Csomagold ki újra a programot.')

def engine_files(root):
    from portrait_video import engine_files as portrait_files
    root=Path(root).expanduser().resolve();comfy_video._portable(root)
    assets=json.loads((asset_home()/'realism_assets.json').read_text(encoding='utf-8'))
    assets.append(json.loads((asset_home()/'face_restore_vendor/model_asset.json').read_text(encoding='utf-8')))
    for asset in assets:
        path=root/asset['path']
        if not path.is_file() or path.stat().st_size!=asset['size']:
            raise RuntimeError('Az élethű motor nincs teljesen telepítve. Az AI-motor és erőforrások résznél válaszd az Élethű motor letöltése gombot.')
    clip=root/'models/text_encoders'/MODELS['clip_name']
    custom=root/'ComfyUI_windows_portable/ComfyUI/custom_nodes/YouTubeMuhely_RAM/__init__.py'
    if not clip.is_file() or not custom.is_file():
        raise RuntimeError('Hiányzik az élethű motor egy összetevője. Az Élethű motor letöltése gomb javítja a telepítést.')
    from motion_smooth import engine_files as smooth_files
    smooth_files(root)
    return portrait_files(root)

def workflow(information,prompt,width,height,frames,steps,seed,prefix,reference=None,
             lighting='Eredeti világítás',image_motion=None,model_names=None):
    if not reference:raise ValueError('Az élethű képanimációhoz referenciafotó szükséges.')
    graph=comfy_video._workflow(information,prompt,width,height,frames,4,seed,prefix,
        reference,lighting,image_motion,model_names=MODELS)
    def node(kind,inputs):
        if kind not in information:raise RuntimeError('Hiányzó élethű összetevő: '+kind)
        for key,spec in information[kind]['input'].get('required',{}).items():
            if key not in inputs:
                details=spec[1] if len(spec)>1 and isinstance(spec[1],dict) else {}
                if 'default' in details:inputs[key]=details['default']
                elif isinstance(spec[0],list):inputs[key]=spec[0][0]
                else:raise RuntimeError('Hiányzó beállítás: '+kind+'.'+key)
            elif isinstance(spec[0],list) and inputs[key] not in spec[0]:
                raise RuntimeError('Nem található beállítás: '+str(inputs[key]))
        return dict(class_type=kind,inputs=inputs)
    graph['8']=node('WanImageToVideo',dict(positive=['5',0],negative=['5',0],vae=['3',0],
        width=width,height=height,length=frames,batch_size=1,start_image=['11',0]))
    graph['7']=node('YouTubeWan14RamSampler',dict(high_name=HIGH,low_name=LOW,
        positive=['8',0],negative=['8',1],latent_image=['8',2],seed=seed))
    for key in ('1','4'):graph.pop(key)
    graph['15']=node('SaveLatent',dict(samples=['7',0],filename_prefix=prefix+'_latent'))
    if information['SaveLatent'].get('output')!=['LATENT']:
        raise RuntimeError('A videómotor régebbi latensmentőt használ. Frissítsd a programhoz mellékelt ComfyUI-t.')
    # SaveLatent passes through the original tensor. Make decoding depend on
    # the completed checkpoint, so a decode error cannot lose the long sample.
    graph['9']['inputs']['samples']=['15',0]
    return graph

def _worker(engine,portrait,config,scene,label,lower,upper):
    engine.check()
    home=asset_home();config=dict(config,runtime=str(portrait),bundle=str(home))
    config_path=scene/(config['action']+'_beallitas.json')
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    logfile=scene/(config['action']+'_motor.log')
    engine.log(label)
    with logfile.open('wb',buffering=0) as output:
        process=subprocess.Popen([str(portrait/'python/python.exe'),str(home/'quality_face_worker.py'),str(config_path)],
            cwd=portrait,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        start=time.monotonic();last=start
        try:
            while process.poll() is None:
                engine.check()
                if time.monotonic()-start>max(600,float(config.get('duration',5))*45):raise RuntimeError('Az arcfeldolgozás túllépte az időkorlátot.')
                if time.monotonic()-last>=5:
                    state=None
                    for line in reversed(logfile.read_text(encoding='utf-8',errors='replace').splitlines()):
                        try:value=json.loads(line)
                        except ValueError:continue
                        if isinstance(value,dict) and value.get('event')=='progress':state=value;break
                    if state:
                        ratio=state['frame']/state['total']
                        engine.log('%s %d/%d képkocka.'%(label,state['frame'],state['total']))
                        if hasattr(engine,'report_scene_progress'):engine.report_scene_progress(lower+(upper-lower)*ratio)
                    last=time.monotonic()
                time.sleep(.15)
        except BaseException:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            raise
    if process.returncode:
        raise RuntimeError('A fotó előkészítése nem sikerült. A részletek a jelenet naplójában találhatók.\n'+logfile.read_text(encoding='utf-8',errors='replace')[-2500:])
    engine.check()
    return json.loads(Path(config['manifest']).read_text(encoding='utf-8'))

class _CoreProgress:
    def __init__(self,delegate):object.__setattr__(self,'delegate',delegate)
    def __getattr__(self,name):return getattr(self.delegate,name)
    def __setattr__(self,name,value):setattr(self.delegate,name,value)
    def report_scene_progress(self,value):
        if hasattr(self.delegate,'report_scene_progress'):self.delegate.report_scene_progress(5+.90*value)

def _release_for_interpolation(engine):
    """Permit Vulkan only after our idle Comfy child has actually exited.

    /free acknowledges queued flags before model memory is released. Reuse
    the existing ownership-aware shutdown instead; a changed or unreadable
    queue must preserve that server and use CPU interpolation directly.
    """
    from ytmuhely import Cancelled
    engine.check()
    process=getattr(engine,'_comfy_process',None)
    base=getattr(engine,'_comfy_url',None)
    if process is not None and base:
        try:
            queue=comfy_video._request(base,'/queue',timeout=5)
            running,pending=comfy_video._queue_prompt_ids(queue)
            engine.check()
            if (not running and not pending and
                getattr(engine,'_comfy_process',None) is process and
                getattr(engine,'_comfy_url',None)==base):
                # stop_engine rechecks the queue before touching the child.
                comfy_video.stop_engine(engine)
                engine.check()
                if process.poll() is not None and getattr(engine,'_comfy_process',None) is None:
                    engine.log('A saját AI-videómotor leállt; a mozgássimítás használhatja a videókártyát.')
                    return True
        except Cancelled:
            raise
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
            pass
    engine.check()
    engine.log('A videókártya felszabadítása nem igazolható; a kész jelenet mozgássimítása CPU-val folytatódik.')
    return False

def generate(engine,p,text,image,index,work,duration):
    portrait=engine_files(p.ai_root)
    scene=Path(work).parent/'AI_jelenetek'/('jelenet_%03d'%(index+1));scene.mkdir(parents=True,exist_ok=True)
    enhanced=getattr(p,'face_restore',False)
    if not isinstance(enhanced,bool):raise ValueError('Az AI-arcjavítás beállítása érvénytelen.')
    source=scene/'elokeszitett_foto.png';source_manifest=scene/'forras_arc.json'
    prepared=_worker(engine,portrait,dict(action='prepare',source=str(Path(image).resolve()),
        output=str(source),manifest=str(source_manifest),enhanced=enhanced,
        framing=getattr(p,'framing','Teljes fotó')),scene,'A fotó és az arc előkészítése…',0,5)
    comfy_video.generate(_CoreProgress(engine),p,text,source,index,work,duration,backend=sys.modules[__name__])
    gpu_ready=_release_for_interpolation(engine)
    settings=json.loads((scene/'video_settings.json').read_text(encoding='utf-8'))
    (scene/'mozgas_egyseg.json').write_text(json.dumps(dict(pipeline='coherent-scene-v2',
        source_face_prepared=prepared['face_found'],head_replacement=False,static_background=False,
        native_frames=settings['frames'],decoder=settings.get('decoder','VAEDecode'),
        description='The generated head and body are never composited with separate source-photo layers.'),indent=2),encoding='utf-8')
    engine.log('A fej és a test közös mozgása elkészült; a mozgás folyamatosságát véglegesítem.')
    if hasattr(engine,'report_scene_progress'):engine.report_scene_progress(95)
    from motion_smooth import generate as smooth
    return smooth(engine,p.ai_root,scene/'frames',settings['fps'],scene,force_cpu=not gpu_ready)
