"""RIFE optical interpolation, preserving native clip time and source frames."""
from __future__ import annotations
import json,math,time
from pathlib import Path

def engine_files(root):
    folder=Path(root).expanduser().resolve()/'interpolation'
    for name in ('rife-ncnn-vulkan.exe','rife-v4.6/flownet.bin','rife-v4.6/flownet.param'):
        if not (folder/name).is_file():raise RuntimeError('Hiányzik a mozgássimító. Az Élethű motor letöltése gomb telepíti.')
    return folder

def generate(engine,root,frames,fps,scene,*,force_cpu=False):
    from ytmuhely import Cancelled,ffmpeg
    from frame_storage import staging_folder,publish_folder
    engine.check()
    if not isinstance(force_cpu,bool):raise ValueError('A mozgássimító CPU-beállítása érvénytelen.')
    folder=engine_files(root);frames=Path(frames).resolve();scene=Path(scene).resolve()
    files=sorted(frames.glob('frame_*.png'));count=len(files)
    if count<2:raise RuntimeError('A mozgássimításhoz hiányoznak a képkockák.')
    if not math.isfinite(float(fps)) or float(fps)<=0:raise ValueError('Érvénytelen képkockasebesség.')
    if any(p.name!='frame_%05d.png'%i for i,p in enumerate(files)):
        raise RuntimeError('A mozgássimító bemeneti képkockái hiányosak.')
    target=math.ceil(count*30/float(fps));start=time.monotonic()
    engine.log('Folyamatos mozgás: köztes képkockák számítása…')
    def interpolate(cpu=False):
        output_frames=staging_folder(scene,'rife_cpu' if cpu else 'rife_gpu')
        args=[folder/'rife-ncnn-vulkan.exe','-i',frames,'-o',output_frames,
            '-n',str(target),'-m',folder/'rife-v4.6','-j','2:2:2','-f','%08d.png']
        if cpu:args+=['-g','-1']
        log=engine.run(args,cwd=folder,timeout=max(600,count*10) if cpu else max(300,count*4))
        engine.check()
        files=sorted(output_frames.glob('*.png'))
        if len(files)!=target:raise RuntimeError('A mozgássimító nem készített el minden köztes képkockát.')
        if any(p.name!='%08d.png'%(i+1) for i,p in enumerate(files)):
            raise RuntimeError('A mozgássimító képkockaszámozása hibás.')
        return publish_folder(output_frames,scene/'folyamatos_kockak'),log
    device='cpu' if force_cpu else 'gpu'
    if force_cpu:
        output_frames,log=interpolate(cpu=True)
    else:
        try:output_frames,log=interpolate()
        except Cancelled:raise
        except RuntimeError:
            engine.log('A mozgássimítás CPU-val folytatódik…')
            output_frames,log=interpolate(cpu=True)
            device='cpu'
    (scene/'mozgas_simito.log').write_text(log,encoding='utf-8')
    output=scene/'folyamatos_mozgas.mp4';temporary=scene/'folyamatos_mozgas.partial.mp4'
    engine.run([ffmpeg(),'-y','-framerate','30','-start_number','1','-i',output_frames/'%08d.png','-frames:v',str(target),
        '-c:v','libx264','-preset','fast','-crf','14','-pix_fmt','yuv420p','-movflags','+faststart',temporary],cwd=scene)
    temporary.replace(output)
    (scene/'mozgas_simito.json').write_text(json.dumps(dict(model='RIFE v4.6 '+('CPU' if device=='cpu' else 'Vulkan'),
        device=device,native_fps=fps,
        output_fps=30,native_frames=count,output_frames=target,seconds=time.monotonic()-start,
        native_duration=count/float(fps),output_duration=target/30),indent=2),encoding='utf-8')
    return output
