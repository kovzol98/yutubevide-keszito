"""Local Wan2.2 motion generation through stable-diffusion.cpp Vulkan."""
import json, math, os, re, subprocess, time
from pathlib import Path
from PIL import Image, ImageOps

PRESETS = {
    'Gyors próba • 832×480': (832,480,33,20),
    'Normál • 832×480': (832,480,97,20),
    'Részletes • 1280×704': (1280,704,121,30),
}

def engine_files(root):
    root=Path(root).expanduser().resolve()
    bins=list(root.rglob('sd-cli.exe')) if root.exists() else []
    if not bins: raise RuntimeError('Nincs telepítve az AI-videómotor. Kattints az AI-motor letöltése gombra, vagy válaszd ki a telepített motor mappáját.')
    names=['diffusion_models/Wan2.2-TI2V-5B-Q5_K_M.gguf','text_encoders/umt5-xxl-encoder-Q5_K_M.gguf','vae/wan2.2_vae.safetensors']
    files=[root/'models'/n for n in names]
    if any(not p.is_file() for p in files): raise RuntimeError('Hiányzik valamelyik videómodell. Az AI-motor letöltése gomb folytatja a telepítést.')
    return bins[0],files

def generate(engine,p,text,image,index,work,duration):
    if p.ai_quality not in PRESETS: raise ValueError('Válassz érvényes AI-részletességet.')
    binary,models=engine_files(p.ai_root)
    prompts=[part.strip() for part in re.split(r'\n\s*\n',p.video_prompt.strip()) if part.strip()]
    prompt=prompts[min(index,len(prompts)-1)] if prompts else (text.strip() or p.title.strip())
    prompt+=' Natural continuous movement of the subjects, realistic motion, cinematic video, no text or captions.'
    width,height,frames,steps=PRESETS[p.ai_quality]
    if 'Shorts' in p.format: width,height=height,width
    target=work.parent/'AI_jelenetek'; target.mkdir(exist_ok=True)
    scene=target/f'jelenet_{index+1:03d}'; scene.mkdir(exist_ok=True)
    promptfile=scene/'prompt.txt'; promptfile.write_text(prompt,encoding='utf-8')
    output=scene/'raw.avi'; log_path=scene/'generalas.log'
    backend='diffusion=vulkan0,te=cpu,vae=cpu' if getattr(p,'ai_cpu_aux',True) else 'vulkan0'
    args=[str(binary),'-M','vid_gen','--diffusion-model',str(models[0]),'--t5xxl',str(models[1]),'--vae',str(models[2]),'--prompt-file',str(promptfile),'-n','still image, frozen motion, text, subtitles, watermark, blurry, distorted anatomy','-W',str(width),'-H',str(height),'--video-frames',str(frames),'--fps','24','--steps',str(steps),'--cfg-scale','5','--sampling-method','euler','--scheduler','simple','--flow-shift','8','--backend',backend,'--params-backend','te=cpu,vae=cpu','--vae-tiling','--temporal-tiling','--offload-to-cpu','-t',str(os.cpu_count() or 4),'-s',str(42+index),'-o',str(output)]
    if image:
        ref=scene/'reference.png'
        with Image.open(image) as src: ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),(width,height),method=Image.Resampling.LANCZOS).save(ref)
        args+=['-i',str(ref)]
    engine.log(f'{index+1}. AI-jelenet generálása ({width}×{height}, {frames} képkocka). Ez több percig tarthat…')
    started=time.monotonic(); last=started
    with log_path.open('wb') as log:
        proc=subprocess.Popen(args,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=0x08000000 if os.name=='nt' else 0)
        try:
            while proc.poll() is None:
                engine.check()
                if time.monotonic()-started>7200: raise RuntimeError('Az AI-generálás túllépte a kétórás jelenetenkénti időkorlátot.')
                if time.monotonic()-last>=20:
                    engine.log(f'{index+1}. AI-jelenet készül… {int(time.monotonic()-started)} másodperc eltelt.'); last=time.monotonic()
                time.sleep(.2)
        except BaseException: proc.kill(); proc.wait(); raise
    if proc.returncode or not output.exists():
        tail=log_path.read_text(encoding='utf-8',errors='replace')[-2200:]
        raise RuntimeError('Az AI-videógenerálás hibát jelzett. A részletek a jelenet generalas.log fájljában találhatók.\n'+tail)
    from imageio_ffmpeg import get_ffmpeg_exe
    result=scene/'video.mp4'
    partial=scene/'video.partial.mp4'
    try:
        engine.run([get_ffmpeg_exe(),'-y','-i',output,'-c:v','libx264','-crf','18','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',partial])
        partial.replace(result)
    except BaseException:
        partial.unlink(missing_ok=True); raise
    engine.log(f'{index+1}. AI-jelenet elkészült. A narrációhoz igazítom.')
    return result
