"""One coherent generated scene: source preparation, Wan video, interpolation."""
from __future__ import annotations
import json,shutil,subprocess,sys,time,uuid
from pathlib import Path
import comfy_video

QUALITY_ENGINE='Élethű • arc és fény'
LEGACY_ENGINE='Alap • korábbi motor'
IMAGE_ENGINES=(QUALITY_ENGINE,LEGACY_ENGINE)
LABEL='Élethű jelenet'
EXTRA_NODES=('WanImageToVideo','YouTubeCpuCLIPLoader','YouTubeWan14RamSampler','VAEDecodeTiled','SaveLatent')
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


def prepare_runtime(engine,root):
    """Update our small bundled safety node before starting an owned server."""
    source=asset_home()/'tools/quality_nodes/__init__.py'
    destination=Path(root)/'ComfyUI_windows_portable/ComfyUI/custom_nodes/YouTubeMuhely_RAM/__init__.py'
    if destination.is_file() and destination.read_bytes()==source.read_bytes():return
    process=getattr(engine,'_comfy_process',None)
    if process is not None and process.poll() is None:
        base=getattr(engine,'_comfy_url',None)
        queue=comfy_video._request(base,'/queue',timeout=5)
        running,pending=comfy_video._queue_prompt_ids(queue)
        engine.check()
        if (getattr(engine,'_comfy_process',None) is not process or
                getattr(engine,'_comfy_url',None)!=base):
            raise RuntimeError('Az AI-motor állapota megváltozott; a biztonsági frissítés nem indult el.')
        if running or pending:
            raise RuntimeError('Az AI-motor biztonsági frissítése előtt fejeződjön be a futó feladat.')
        # A second queue check inside stop_engine protects a client that
        # submitted work after our first idle response. Never rewrite a module
        # until the captured child is confirmed stopped and ownership cleared.
        comfy_video.stop_engine(engine)
        engine.check()
        if process.poll() is None or getattr(engine,'_comfy_process',None) is not None:
            raise RuntimeError('Az AI-motor leállása nem igazolható; a biztonsági frissítés későbbre maradt.')
    engine.check()
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name('__init__.py.'+uuid.uuid4().hex+'.tmp')
    try:
        shutil.copyfile(source,temporary);temporary.replace(destination)
    except BaseException:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
        raise
    else:temporary.unlink(missing_ok=True)
    engine.log('Az AI-motor számításellenőrzése frissült; a nagy modelleket nem kell újra letölteni.')

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
    graph['2']=node('YouTubeCpuCLIPLoader',dict(clip_name=MODELS['clip_name'],type='wan'))
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
    def __init__(self,delegate,start=5,span=90):
        object.__setattr__(self,'delegate',delegate)
        object.__setattr__(self,'start',start);object.__setattr__(self,'span',span)
    def __getattr__(self,name):return getattr(self.delegate,name)
    def __setattr__(self,name,value):setattr(self.delegate,name,value)
    def report_scene_progress(self,value):
        if hasattr(self.delegate,'report_scene_progress'):self.delegate.report_scene_progress(self.start+self.span*value/100)

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
    from image_motion import source_dimensions
    from video_workload import plan_workload
    from dataclasses import asdict
    quality=comfy_video.normalized_quality(p.ai_quality)
    width,height,fps,_=PRESETS[quality]
    width,height=source_dimensions(source,width,height)
    plan=plan_workload(width,height,fps,duration,quality)
    manifest=dict(asdict(plan),status='running',completed_segments=0,
                  continuation='last accepted native frame',looped=False)
    manifest_path=scene/'reszterv.json'
    def save_plan():manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    save_plan();engine.log(plan.user_hint)
    from frame_storage import staging_folder,publish_folder
    merged=staging_folder(scene,'frames')
    reference=source;last_settings=None
    try:
        for part in plan.segments:
            engine.check()
            part_scene=scene/'reszek'/('resz_%03d'%(part.index+1))
            engine.log('AI-rész %d/%d: %d képkocka számítása…'%(
                part.index+1,len(plan.segments),part.generated_frames))
            comfy_video.generate(_CoreProgress(engine,5+90*part.output_start/plan.output_frames,
                90*part.output_frames/plan.output_frames),p,text,reference,index,work,
                part.generated_frames/fps,backend=sys.modules[__name__],
                scene_dir=part_scene,canvas=(width,height))
            last_settings=json.loads((part_scene/'video_settings.json').read_text(encoding='utf-8'))
            for offset,number in enumerate(range(part.trim_start,part.trim_stop)):
                engine.check()
                origin=part_scene/'frames'/('frame_%05d.png'%number)
                destination=merged/('frame_%05d.png'%(part.output_start+offset))
                shutil.copyfile(origin,destination)
            reference=merged/('frame_%05d.png'%(part.output_stop-1))
            manifest['completed_segments']=part.index+1;save_plan()
        from frame_integrity import validate_sequence
        validate_sequence(engine,merged,plan.output_frames,(width,height),reference=source,
            report_path=scene/'frame_integrity.json')
        engine.check();publish_folder(merged,scene/'frames')
        settings=dict(last_settings,width=width,height=height,fps=fps,frames=plan.output_frames,
            segmented=True,segment_count=len(plan.segments),max_segment_frames=plan.max_generated_frames,
            requested_duration=duration,workload_policy=plan.policy)
        (scene/'video_settings.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        manifest['status']='complete';save_plan()
    except BaseException as error:
        manifest.update(status='failed',error_type=type(error).__name__,error=str(error))
        try:save_plan()
        except OSError:pass
        raise
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
