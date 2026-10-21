"""YouTube Műhely — Windows video maker. Python 3.12+."""
import asyncio, base64, io, json, math, os, queue, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, wave
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Queue/resume modules import the shared Project and Cancelled definitions.
# Direct source and frozen launches must reuse this initialized main module.
if __name__=='__main__':sys.modules.setdefault('ytmuhely',sys.modules[__name__])

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
APP_RELEASE = '2026.10.21 • 16. javítás'
class Cancelled(Exception): pass

def queue_file():
    """Per-user queue, independent of the extracted program's version folder."""
    override=os.environ.get('YTMUHELY_QUEUE_FILE','').strip()
    if override:return Path(override).expanduser().resolve()
    base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
    return base/'YouTubeMuhely2'/'munkasor.json'

def run_local_queue(filename):
    """Headless execution uses the same persisted offline queue as the GUI."""
    from render_queue import QueueStore,QueueRunner
    request=Path(filename)
    data=json.loads(request.read_text(encoding='utf-8-sig'))
    path=Path(data['queue_file']).expanduser()
    if not path.is_absolute():path=request.resolve().parent/path
    store=QueueStore(path)
    try:
        runner=QueueRunner(store,Engine)
        runner.start()
        runner.join()
        if runner.last_error:
            raise RuntimeError('A munkasor állapotát nem sikerült biztonságosan menteni: '+str(runner.last_error))
        jobs=store.items()
        result={name:[{'id':job['id'],'result':job.get('result',''),'error':job.get('error','')}
            for job in jobs if job['status']==state]
            for name,state in [('completed','complete'),('failed','failed'),
                ('cancelled','cancelled'),('interrupted','interrupted'),('pending','pending')]}
        request.with_suffix('.result.json').write_text(
            json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:store.close()

@dataclass
class Project:
    title: str = 'Új videó'
    text: str = ''
    images: list = field(default_factory=list)
    videos: list = field(default_factory=list)
    visual_mode: str = 'AI-videó • helyi'
    video_prompt: str = ''
    comfy_url: str = 'http://127.0.0.1:8188'
    comfy_workflow: str = ''
    ai_root: str = ''
    ai_quality: str = 'Normál • 832×480'
    image_engine: str = 'Élethű • arc és fény'
    lighting: str = 'Eredeti világítás'
    image_motion: str = 'Jelenetleírás szerinti mozgás'
    framing: str = 'Teljes fotó'
    face_restore: bool = False
    enhancement: str = 'Finom képjavítás'
    output: str = ''
    format: str = '1080p • 16:9'
    narration: str = 'Angol női LJ • offline'
    encoder: str = 'Automatikus GPU / CPU'
    seconds: float = 5.0
    audio: str = ''
    music: str = ''
    music_volume: float = 12.0
    video_volume: float = 100.0
    use_video_length: bool = True
    subtitles: bool = True
    model: str = 'gemma3:4b'
    grok_resolution: str = '720p'
    grok_max_scenes: int = 1

SIZES = {'1080p • 16:9': (1920,1080), 'Shorts • 9:16': (1080,1920), '720p • 16:9': (1280,720), '4K • 16:9': (3840,2160), 'Fotó eredeti aránya': None}
LOCAL_AI_MODES = ('AI-videó • helyi', 'AI-videó • helyi Wan')
GROK_MODE = 'Grok videó • internet'
AI_MODES = (*LOCAL_AI_MODES, GROK_MODE)
NARRATIONS=('Angol női LJ • offline','Angol női • internet','Angol férfi • internet',
    'Saját hangfájl','Nincs narráció')
def grok_cost_estimate(durations,resolution,image_count=0):
    """Dated public-price estimate, not an account charge or cost ceiling."""
    rate={'720p':.14,'1080p':.25}[resolution]
    return sum(math.ceil(seconds) for seconds in durations)*rate+image_count*.01
def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def pieces(text, limit=24):
    out=[]
    for para in re.split(r'\n\s*\n', text.strip()):
        words=para.split()
        while words:
            end=min(limit,len(words))
            if len(words)>limit:
                for i in range(end, max(6,end//2), -1):
                    if words[i-1][-1:] in '.!?;': end=i; break
            out.append(' '.join(words[:end])); words=words[end:]
    return out

def scene_chunks(project,video_info):
    """Plan the same scene order for a new render or a checked local resume."""
    mode=project.visual_mode
    media=project.videos if mode=='Saját videók' else project.images
    prompts=[part.strip() for part in re.split(r'\n\s*\n',project.video_prompt) if part.strip()]
    clip_lengths=[]
    if mode=='Saját videók':
        words=project.text.split();chunks=[];cursor=0;covered=0
        clip_lengths=[video_info[filename]['duration'] for filename in project.videos]
        full_length=sum(clip_lengths)
        for length in clip_lengths:
            covered+=length;end=round(len(words)*covered/full_length)
            chunks.append(' '.join(words[cursor:end]));cursor=end
    else:
        chunks=pieces(project.text,limit=12) if project.text.strip() else ['']*max(
            1,len(media),len(prompts) if mode in AI_MODES else 0)
    target=max(len(media),len(prompts) if mode in AI_MODES else 0)
    while len(chunks)<target:
        largest=max(range(len(chunks)),key=lambda i:len(chunks[i].split()))
        words=chunks[largest].split()
        if len(words)<2:chunks.append('')
        else:
            mid=len(words)//2;chunks[largest:largest+1]=[' '.join(words[:mid]),' '.join(words[mid:])]
    if len(chunks)>500:raise ValueError('Legfeljebb 500 jelenet készíthető egy videóban.')
    return chunks,clip_lengths

def font(size,bold=False):
    base=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in (['segoeuib.ttf','arialbd.ttf'] if bold else ['segoeui.ttf','arial.ttf']):
        if (base/name).exists(): return ImageFont.truetype(str(base/name),size)
    return ImageFont.load_default(size=size)

def wrap(draw,text,f,max_width):
    lines=[]; line=''
    for word in text.split():
        # Split unusually long tokens instead of allowing text to overflow.
        parts=[]; part=''
        for char in word:
            if part and draw.textlength(part+char,font=f)>max_width: parts.append(part); part=''
            part+=char
        if part: parts.append(part)
        for token in parts:
            trial=(line+' '+token).strip()
            if line and draw.textlength(trial,font=f)>max_width: lines.append(line); line=token
            else: line=trial
    if line: lines.append(line)
    return lines

def poster(path,size,title,text,image=None,index=0,total=1,subs=True):
    w,h=size; margin=int(w*.075)
    if image:
        with Image.open(image) as src:
            im=ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),size,method=Image.Resampling.LANCZOS)
        overlay=Image.new('RGBA',size,(0,0,0,0)); d=ImageDraw.Draw(overlay)
        for y in range(h):
            alpha=int(35+135*(y/h)**3)
            d.line((0,y,w,y),fill=(22,17,12,alpha))
        im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB')
    else:
        im=Image.new('RGB',size); d=ImageDraw.Draw(im)
        for y in range(h):
            k=y/h; d.line((0,y,w,y),fill=(int(30+20*k),int(25+16*k),int(21+12*k)))
        d.ellipse((int(w*.62),-int(h*.1),int(w*1.2),int(h*.48)),fill=(70,50,33))
    d=ImageDraw.Draw(im)
    ft=font(int(w*.031),True); title_lines=wrap(d,title,ft,w-2*margin)[:3]
    d.multiline_text((margin,int(h*.06)),'\n'.join(title_lines),font=ft,fill='#f0d2a3',spacing=int(w*.007))
    if text and subs:
        fs=int(w*(.044 if h>w else .035))
        while True:
            f=font(fs,True); lines=wrap(d,text,f,w-2*margin)
            line_h=int(fs*1.35)
            if len(lines)*line_h<h*.45 or fs<=14: break
            fs-=2
        block_h=len(lines)*line_h+int(w*.045)
        y=int(h*.81)-block_h if image else int(h*.5)-block_h//2
        panel=Image.new('RGBA',size,(0,0,0,0)); pd=ImageDraw.Draw(panel)
        pd.rounded_rectangle((margin//2,y-int(w*.018),w-margin//2,y+block_h),radius=int(w*.018),fill=(23,18,13,200))
        im=Image.alpha_composite(im.convert('RGBA'),panel).convert('RGB'); d=ImageDraw.Draw(im)
        for line in lines:
            d.text(((w-d.textlength(line,font=f))/2,y),line,font=f,fill='white'); y+=line_h
    d.rounded_rectangle((margin,h-int(h*.065),w-margin,h-int(h*.058)),radius=4,fill='#4b4035')
    d.rounded_rectangle((margin,h-int(h*.065),margin+max(8,int((w-2*margin)*(index+1)/total)),h-int(h*.058)),radius=4,fill='#e8b46b')
    im.save(path)

class Engine:
    def __init__(self,log=lambda x:None,progress=lambda x:None):
        self.log=log; self.progress=progress; self.cancel=threading.Event()
        # Runtime-only credential; Project is deliberately safe to serialize.
        self.grok_api_key=''
    def grok_key(self):
        return (self.grok_api_key or os.environ.get('XAI_API_KEY','')).strip()
    def grok_prompt(self,p,text,image,index):
        prompts=[part.strip() for part in re.split(r'\n\s*\n',p.video_prompt) if part.strip()]
        prompt=prompts[min(index,len(prompts)-1)] if prompts else ('' if image else (text.strip() or p.title.strip()))
        from image_motion import image_motion_prompt
        from video_look import lighting_prompt
        if image:prompt=image_motion_prompt(p.image_motion,prompt)
        return lighting_prompt(p.lighting,prompt)
    def check(self):
        if self.cancel.is_set(): raise Cancelled('Megszakítva.')
    def report_scene_progress(self,percent):
        count=max(1,getattr(self,'_scene_count',1)); index=getattr(self,'_scene_index',0)
        self.progress(90*(index+max(0,min(100,percent))/100)/count)
    def run(self,args,cwd=None,timeout=3600,input_bytes=None):
        self.check()
        with tempfile.TemporaryFile() as capture:
            p=subprocess.Popen([str(a) for a in args],cwd=cwd,stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,stdout=capture,stderr=capture,creationflags=CREATE_NO_WINDOW)
            if input_bytes is not None:
                p.stdin.write(input_bytes); p.stdin.close()
            start=time.monotonic()
            try:
                while p.poll() is None:
                    self.check()
                    if time.monotonic()-start>timeout: raise RuntimeError('A művelet túllépte az időkorlátot.')
                    time.sleep(.1)
            except BaseException:
                p.kill(); p.wait(); raise
            capture.seek(0); data=capture.read().decode('utf-8',errors='replace')
            if p.returncode: raise RuntimeError(data[-3500:] or 'A külső eszköz hibát jelzett.')
            return data
    def duration(self,path,work):
        wav=work/'measure.wav'
        try:
            self.run([ffmpeg(),'-y','-i',path,'-vn','-af','aresample=16000:async=1:first_pts=0',
                '-ac','1','-ar','16000',wav])
            with wave.open(str(wav)) as f:value=f.getnframes()/f.getframerate()
        finally:wav.unlink(missing_ok=True)
        if not math.isfinite(value) or value<=0:
            raise ValueError('A narráció hangfájlja üres vagy nem olvasható.')
        return value
    def codec(self,mode,enhancement='Eredeti megjelenés'):
        fine=enhancement=='Finom képjavítás'
        cpu=['-c:v','libx264','-preset','medium' if fine else 'fast','-crf','16' if fine else '20','-threads',str(os.cpu_count() or 4)]
        if mode=='CPU • összes mag': return cpu
        candidates=[('NVIDIA',['-c:v','h264_nvenc','-preset','p4','-cq','21']),('AMD',['-c:v','h264_amf','-quality','balanced','-b:v','8M']),('Intel',['-c:v','h264_qsv','-global_quality','21'])]
        if fine:
            candidates=[('NVIDIA',['-c:v','h264_nvenc','-preset','p6','-cq','17']),
                ('AMD',['-c:v','h264_amf','-quality','quality','-rc','cqp','-qp_i','16','-qp_p','18','-qp_b','20']),
                ('Intel',['-c:v','h264_qsv','-global_quality','17'])]
        for label,args in candidates:
            if mode!='Automatikus GPU / CPU' and not mode.startswith(label): continue
            try:
                self.run([ffmpeg(),'-v','error','-f','lavfi','-i','color=size=128x128:rate=30','-frames:v','3',*args,'-f','null','-'],timeout=30)
                self.log(label+' hardveres kódolás aktív.'); return args
            except Cancelled: raise
            except RuntimeError: pass
        if mode!='Automatikus GPU / CPU': self.log('A választott GPU-kódoló nem elérhető; CPU-ra váltás.')
        self.log(f'CPU-kódolás: {os.cpu_count() or 4} szál.'); return cpu
    def narrate(self,text,path,mode):
        txt=path.with_suffix('.txt'); txt.write_text(text,encoding='utf-8')
        if mode=='Angol női LJ • offline':
            home=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
            motor=home/'hangmotor'; binary=motor/'piper'/'piper.exe'; model=motor/'en_US-ljspeech-medium.onnx'
            if not binary.exists() or not model.exists(): raise RuntimeError('Hiányzik a hangmotor mappa. A teljes programcsomagot csomagold ki.')
            wav=path.with_suffix('.wav')
            self.run([binary,'--model',model,'--output_file',wav],cwd=binary.parent,input_bytes=(text+'\n').encode('utf-8'),timeout=180)
            self.run([ffmpeg(),'-y','-i',wav,path])
        elif mode in ('Angol női • internet','Angol férfi • internet'):
            voice='en-US-JennyNeural' if 'női' in mode else 'en-US-GuyNeural'
            executable=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
            try: self.run([*executable,'--tts',txt,path,voice],timeout=120)
            except Cancelled: raise
            except RuntimeError as e: raise RuntimeError('Az angol internetes felolvasás nem sikerült. Válaszd az Angol női LJ offline hangot, vagy adj meg saját hangfájlt.\n'+str(e))
        else:
            raise ValueError('Válassz egy támogatott angol narrációs hangot.')
    def generate_clip(self,p,text,image,index,work,duration):
        if p.visual_mode==GROK_MODE:
            from grok_video import generate_scene
            folder=Path(work).parent/'AI_jelenetek'/('jelenet_%03d'%(index+1))
            result=generate_scene(self,image,self.grok_prompt(p,text,image,index),
                duration,p.grok_resolution,folder,self.grok_key(),
                aspect_ratio='9:16' if 'Shorts' in p.format else '16:9')
            self.verify_grok_video(result,math.ceil(duration))
            return result
        from portrait_video import PORTRAIT_MOTION
        if image and p.image_motion == PORTRAIT_MOTION:
            from portrait_video import generate
            return generate(self,p,image,index,work,duration)
        if image:
            from realistic_video import QUALITY_ENGINE
            if p.image_engine==QUALITY_ENGINE:
                from realistic_video import generate
                return generate(self,p,text,image,index,work,duration)
        from comfy_video import generate
        return generate(self,p,text,image,index,work,duration)
    def verify_grok_video(self,path,expected_seconds=None):
        # A transport header is not proof of a usable movie. Decode the complete
        # downloaded video before export or a successful resume notification.
        from media_probe import probe_video
        info=probe_video(self,path)
        if not math.isfinite(info['duration']) or info['duration']<=0:
            raise RuntimeError('A Grok nem adott lejátszható videót. A mentett feladat megmaradt.')
        decoded=self.run([ffmpeg(),'-v','error','-xerror','-i',path,'-map','0:V:0','-an',
            '-progress','pipe:1','-f','null','-'],timeout=120)
        counts=[int(value) for value in re.findall(r'(?m)^frame=\s*(\d+)\s*$',decoded)]
        times=[int(value)/1e6 for value in re.findall(r'(?m)^out_time_us=(\d+)\s*$',decoded)]
        if not counts or max(counts)<2 or not times or max(times)<=0:
            raise RuntimeError('A Grok videója nem tartalmaz teljesen lejátszható képsort.')
        if expected_seconds is not None and max(times)+.05<float(expected_seconds):
            raise RuntimeError('A visszakapott Grok-videó rövidebb a kért jelenetnél. A mentett feladat megmaradt; új generálás nem indult.')
    def build(self,p,*,resume_folder=None):
        self.check()
        if p.narration=='Windows-hang • offline':p=replace(p,narration='Angol női LJ • offline')
        if p.narration not in NARRATIONS:raise ValueError('Válassz egy támogatott narrációs beállítást.')
        from image_motion import resolved_motion
        chosen=resolved_motion(p.image_motion,p.video_prompt)
        if p.visual_mode in AI_MODES and chosen!=p.image_motion:
            p=replace(p,image_motion=chosen)
            self.log('A leírás táncot kér: teljes alakos AI-jelenetet készítek.')
        resume_state=None
        if resume_folder is not None:
            from local_resume import prepare,normalize_project
            resume_state=prepare(self,resume_folder)
            if normalize_project(asdict(p))!=resume_state.project:
                raise ValueError('A folytatáshoz az eredeti mentett projektbeállításokat kell használni.')
            if resume_state.library.status=='complete':
                self.last_project_folder=resume_state.root
                self.log('Ez a helyi projekt már elkészült; nem indult új generálás.')
                self.progress(100);return resume_state.root
        from video_look import validate_lighting, lighting_filter
        from image_motion import validate_image_motion
        from video_quality import validate_enhancement,pre_filter,post_filter,source_size,SOURCE_FORMAT
        validate_lighting(p.lighting)
        validate_image_motion(p.image_motion)
        validate_enhancement(p.enhancement)
        from reference_framing import validate_framing,FULL_FRAME
        validate_framing(p.framing)
        if not isinstance(p.face_restore,bool):raise ValueError('Az AI-arcjavítás beállítása érvénytelen.')
        from realistic_video import IMAGE_ENGINES,QUALITY_ENGINE
        if p.image_engine not in IMAGE_ENGINES:raise ValueError('Ismeretlen fotóanimációs motor.')
        color_filter=lighting_filter(p.lighting)
        mode=p.visual_mode
        if mode not in ('Saját videók',*AI_MODES,'Képes diavideó'):
            raise ValueError('Ismeretlen videókészítési mód.')
        if mode=='Saját videók' and not p.videos:
            raise ValueError('Adj hozzá legalább egy valódi videóklipet a Saját videók módhoz.')
        if mode in AI_MODES and not (p.text.strip() or p.images or p.video_prompt.strip()):
            raise ValueError('Adj meg narrációt, videóleírást vagy legalább egy képet az AI-videóhoz.')
        if mode==GROK_MODE:
            from grok_video import validate_options
            if p.grok_resolution not in ('720p','1080p'):
                raise ValueError('A Grok felbontása 720p vagy 1080p lehet.')
            if isinstance(p.seconds,bool) or not isinstance(p.seconds,(int,float)) or not math.isfinite(p.seconds) or not 1<=p.seconds<=15:
                raise ValueError('Grok módban egy jelenet hossza 1–15 másodperc lehet.')
            if type(p.grok_max_scenes) is not int or not 1<=p.grok_max_scenes<=100:
                raise ValueError('A fizetős jelenetek korlátja 1 és 100 közötti egész szám legyen.')
            validate_options(self.grok_prompt(p,p.text,p.images[0] if p.images else None,0),
                p.seconds,p.grok_resolution,self.grok_key())
        if mode=='Képes diavideó' and not p.text.strip() and not p.images:
            raise ValueError('Adj meg szöveget vagy legalább egy képet a diavideóhoz.')
        if p.narration=='Saját hangfájl' and not p.audio: raise ValueError('Válassz narrációs hangfájlt.')
        from background_music import validate_settings,validate_audio,export_video
        music_volume=validate_settings(p.music,p.music_volume)
        from background_music import validate_video_volume,scene_audio
        video_volume=validate_video_volume(p.video_volume)
        if not isinstance(p.use_video_length,bool):raise ValueError('A videóhossz megtartása beállítás érvénytelen.')
        if isinstance(p.seconds,bool) or not isinstance(p.seconds,(int,float)) or not math.isfinite(p.seconds) or not 1<=p.seconds<=120:
            raise ValueError('A jelenethossz 1–120 másodperc lehet.')
        media=p.videos if mode=='Saját videók' else p.images
        for f in media+([p.audio] if p.narration=='Saját hangfájl' else [])+([p.music] if p.music else []):
            if not Path(f).is_file(): raise ValueError('Nem található: '+f)
        # A bad music file must fail before the long AI generation starts.
        validate_audio(self,p.music,music_volume)
        if p.narration=='Saját hangfájl':
            try:validate_audio(self,p.audio,100)
            except ValueError as error:
                raise ValueError('A saját narráció nem tartalmaz lejátszható hangot. Válassz egy ép hangfájlt.') from error
        video_info={}
        if mode=='Saját videók':
            from media_probe import probe_video
            for filename in p.videos:
                self.check();video_info[filename]=probe_video(self,filename)
        else:
            for filename in p.images:
                self.check()
                try:
                    with Image.open(filename) as source:source.load()
                except (OSError,ValueError) as error:
                    raise ValueError('A kép nem olvasható: '+str(filename)) from error
        if mode==GROK_MODE:
            from grok_video import preflight_image
            for filename in p.images:
                self.check();preflight_image(filename)
        if mode in AI_MODES:
            from image_motion import validate_motion_request
            validate_motion_request(p.image_motion,p.video_prompt,has_images=bool(p.images))
            if mode in LOCAL_AI_MODES and p.images and p.framing!=FULL_FRAME and (p.image_engine!=QUALITY_ENGINE or p.image_motion=='Portré • finom fejmozgás'):
                raise ValueError('Az alanyra közelítéshez válaszd az Élethű motort és a teljes jelenet mozgását.')
        chunks,clip_lengths=scene_chunks(p,video_info)
        if resume_state is not None and tuple(chunks)!=resume_state.chunks:
            raise ValueError('A jelenetterv megváltozott; az eredeti feladatot nem folytatjuk eltérő beállításokkal.')
        if mode==GROK_MODE and len(chunks)>p.grok_max_scenes:
            estimate=grok_cost_estimate([p.seconds]*len(chunks),p.grok_resolution,
                len(chunks) if p.images else 0)
            raise ValueError('A terv %d fizetős Grok-jelenetet tartalmaz, a megadott korlát %d. '
                '%g mp-es jelenetekkel számolt becslés: %.2f USD (2026.10.20-i árlista). '
                'A narráció tényleges hossza módosíthatja az árat. Rövidítsd a történetet, '
                'vagy emeld a fizetős jelenetek korlátját. Nem indult generálás.'
                %(len(chunks),p.grok_max_scenes,p.seconds,estimate))
        retained=resume_state.library.scenes if resume_state is not None else ()
        needs_new_scene=len(retained)<len(chunks)
        if mode in LOCAL_AI_MODES and needs_new_scene:
            if p.images and p.image_motion == 'Portré • finom fejmozgás':
                from portrait_video import engine_files
            elif p.images and p.image_engine==QUALITY_ENGINE:
                from realistic_video import engine_files
            else:
                from comfy_video import engine_files
            engine_files(p.ai_root)
            from gpu_resources import probe_gpu_users,describe_gpu_users
            self.log(describe_gpu_users(probe_gpu_users(self.cancel)))
            self.check()
        size=SIZES[p.format]
        if p.format==SOURCE_FORMAT:
            if mode=='Saját videók' or not p.images: raise ValueError('A Fotó eredeti aránya mérethez adj hozzá egy képet.')
            size=source_size(p.images[0])
        if resume_state is not None and tuple(size)!=resume_state.size:
            raise ValueError('A mentett kezdeti képméret nem egyezik a projekt eredeti tervével.')
        root=Path(p.output).expanduser().resolve(); root.mkdir(parents=True,exist_ok=True)
        safe=re.sub(r'[^\w -]','',p.title).strip()[:60] or 'video'
        if resume_state is not None:safe+='_folytatas'
        dest=root/(datetime.now().strftime('%Y%m%d_%H%M%S_')+safe)
        counter=1
        while dest.exists(): dest=root/(datetime.now().strftime('%Y%m%d_%H%M%S_')+safe+f'_{counter}'); counter+=1
        dest.mkdir(); self.last_project_folder=dest
        work=dest/'ideiglenes'; work.mkdir()
        (dest/'projekt.json').write_text(json.dumps(asdict(p),ensure_ascii=False,indent=2),encoding='utf-8')
        primary_error=None
        try:
            from scene_library import create as create_library,add_scene,complete as complete_library
            create_library(self,dest,len(chunks))
            from local_resume import supported,create as save_resume
            if supported(asdict(p)):save_resume(self,dest,asdict(p),chunks,size)
            codec=None; clips=[]; clip_lengths_exact=[]; cues=[]; elapsed=0
            own_duration=self.duration(p.audio,work) if p.narration=='Saját hangfájl' else None
            weights=clip_lengths if mode=='Saját videók' else [max(1,len(t.split())) for t in chunks]
            weight_sum=sum(weights)
            prepared_cloud=[]
            if mode==GROK_MODE:
                # Validate every actual scene length before the first paid POST.
                # Long own narration or a later speech chunk must not fail after
                # earlier scenes have already been billed.
                from grok_video import validate_options
                for i,chunk in enumerate(chunks):
                    self.check();audio=None;spoken_duration=None;duration=p.seconds
                    if chunk and p.narration not in ('Nincs narráció','Saját hangfájl'):
                        audio=work/f'voice_{i:04d}.mp3';self.narrate(chunk,audio,p.narration)
                        spoken_duration=self.duration(audio,work);duration=spoken_duration+.35
                    elif own_duration:duration=own_duration*weights[i]/weight_sum
                    duration=max(1,math.ceil(duration*30))/30
                    image=p.images[i%len(p.images)] if p.images else None
                    validate_options(self.grok_prompt(p,chunk,image,i),duration,p.grok_resolution,self.grok_key())
                    prepared_cloud.append((audio,spoken_duration,duration))
                estimate=grok_cost_estimate([item[2] for item in prepared_cloud],p.grok_resolution,
                    len(chunks) if p.images else 0)
                self.log('Grok: %d jelenet, összesen %d kért másodperc. Becsült alapár: %.2f USD '
                    '(2026.10.20-i árlista; nem ellenőrzött fiókterhelés).'
                    %(len(chunks),sum(math.ceil(item[2]) for item in prepared_cloud),estimate))
                self.log('Grok: a jelenethosszak ellenőrizve. A fizetős generálás az xAI szolgáltatásán indul; a leállítás csak a várakozást szakítja meg.')
            def ass_time(t):
                cs=round(t*100); return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
            def caption_file(path,text,duration):
                # Phrase timings follow narration duration; exact word alignment is not assumed.
                phrases=pieces(text,limit=6)
                weights=[max(1,len(t.split())) for t in phrases]
                total=sum(weights); now=0; local=[]
                for phrase,weight in zip(phrases,weights):
                    end=now+duration*weight/total
                    local.append((now,end,phrase)); now=end
                w,h=size; fs=max(20,round(w*(.038 if h>w else .027)))
                margin=max(24,round(w*.05)); bottom=max(28,round(h*.065))
                header=(f'[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n'
                        '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
                        f'Style: Default,Segoe UI,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,3,1,2,{margin},{margin},{bottom},1\n\n'
                        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
                rows=[]
                for start,end,phrase in local:
                    # Literal user text cannot inject ASS style overrides.
                    shown=phrase.replace('\\','/').replace('{','(').replace('}',')').replace('\n',' ')
                    rows.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{shown}\n')
                path.write_text(header+''.join(rows),encoding='utf-8')
                return local
            for i,text in enumerate(chunks):
                self._scene_count=len(chunks); self._scene_index=i
                if i<len(retained):
                    self.check();scene=retained[i];size=scene.size
                    self.log(f'{i+1}/{len(chunks)} kész jelenet átvétele · AI és felolvasás nélkül…')
                    copied=add_scene(self,dest,scene.video,scene.frames,
                        caption_file=scene.caption,captions_enabled=scene.captions_enabled,
                        cues=scene.cues,label=scene.label,size=scene.size,
                        enhancement=scene.enhancement,lighting=scene.lighting)
                    if i==0:self.run([ffmpeg(),'-y','-i',copied.video,'-frames:v','1',dest/'boritokep.png'])
                    clips.append('../jelenetek/'+copied.video.name)
                    clip_lengths_exact.append(scene.duration)
                    cues.extend((elapsed+a,elapsed+b,t) for a,b,t in scene.cues)
                    elapsed+=scene.duration
                    self.progress(90*(i+1)/len(chunks));continue
                self.check(); self.log(f'{i+1}/{len(chunks)} jelenet készítése…')
                if codec is None:codec=self.codec(p.encoder,p.enhancement)
                img=work/f'scene_{i:04d}.png'; clip=work/f'clip_{i:04d}.mp4'; audio=None
                duration=p.seconds; spoken_duration=None
                if mode==GROK_MODE:
                    audio,spoken_duration,duration=prepared_cloud[i]
                elif text and p.narration not in ('Nincs narráció','Saját hangfájl'):
                    audio=work/f'voice_{i:04d}.mp3'; self.narrate(text,audio,p.narration)
                    spoken_duration=self.duration(audio,work); duration=spoken_duration+.35
                elif own_duration: duration=own_duration*weights[i]/weight_sum
                elif mode=='Saját videók' and p.use_video_length:
                    duration=video_info[p.videos[i%len(p.videos)]]['duration']
                frames=max(1,math.ceil(duration*30)); duration=frames/30
                image=p.images[i%len(p.images)] if p.images else None
                if mode=='Képes diavideó':
                    poster(img,size,p.title,'',image,i,len(chunks),False)
                    inputs=['-loop','1','-i',str(img)]
                    vf=f"zoompan=z='min(zoom+0.00007,1.045)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={size[0]}x{size[1]}:fps=30,setsar=1"
                else:
                    if mode=='Saját videók': visual=Path(p.videos[i%len(p.videos)]).resolve()
                    else: visual=Path(self.generate_clip(p,text,image,i,work,duration)).resolve()
                    self.check()
                    if not visual.is_file(): raise RuntimeError('A jelenethez nem készült videóklip: '+str(visual))
                    if i==0 and mode in LOCAL_AI_MODES and p.format==SOURCE_FORMAT and p.framing!=FULL_FRAME:
                        prepared=dest/'AI_jelenetek'/'jelenet_001'/'elokeszitett_foto.png'
                        if not prepared.is_file():raise RuntimeError('Nem található az alanyra közelítéshez előkészített fotó.')
                        size=source_size(prepared)
                    inputs=(['-stream_loop','-1'] if mode=='Saját videók' else [])+['-i',str(visual)]
                    # Preserve the actual moving picture; fit portrait and landscape clips without distortion.
                    cleaned=(mode==GROK_MODE or mode in LOCAL_AI_MODES and image and (p.image_motion=='Portré • finom fejmozgás' or p.image_engine==QUALITY_ENGINE))
                    before=','.join(f for f in (pre_filter(p.enhancement,cleaned),color_filter) if f)
                    tail='tpad=stop_mode=clone:stop_duration=1,' if mode==GROK_MODE else ''
                    vf=(before+',' if before else '')+f'scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease:flags=lanczos,pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,'+tail+'fps=30'
                    after=post_filter(p.enhancement)
                    if after: vf+=','+after
                caption=work/f'caption_{i:04d}.ass'
                local=caption_file(caption,text,spoken_duration or duration)
                if p.subtitles and local: vf+=f',ass=caption_{i:04d}.ass'
                has_original=(mode=='Saját videók' and video_info[p.videos[i%len(p.videos)]]['has_audio'])
                if audio: inputs+=['-i',str(audio)]
                else: inputs+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
                sound_filters,sound_map=scene_audio(has_original,video_volume,voice=bool(audio))
                self.run([ffmpeg(),'-y',*inputs,*sound_filters,'-map','0:V:0' if mode=='Saját videók' else '0:v:0',
                    '-map',sound_map,'-vf',vf,*codec,'-pix_fmt','yuv420p','-r','30','-c:a','aac','-b:a','192k',
                    '-ar','48000','-ac','2',*(['-af','apad'] if not sound_filters else []),
                    '-t',f'{duration:.6f}',clip],cwd=work)
                add_scene(self,dest,clip,frames,caption_file=caption,
                    captions_enabled=bool(p.subtitles and local),cues=local,label=text,
                    size=size,enhancement=p.enhancement,lighting=p.lighting)
                if i==0:
                    self.run([ffmpeg(),'-y','-i',clip,'-frames:v','1',dest/'boritokep.png'])
                clips.append(clip.name)
                clip_lengths_exact.append(duration)
                cues.extend((elapsed+a,elapsed+b,t) for a,b,t in local)
                elapsed+=duration
                self.progress(90*(i+1)/len(chunks))
            listing=work/'concat.txt'
            listing.write_text(''.join(f"file '{name}'\nduration {seconds:.9f}\n"
                for name,seconds in zip(clips,clip_lengths_exact)),encoding='utf-8')
            merged=work/'merged.mp4'
            self.run([ffmpeg(),'-y','-f','concat','-safe','0','-i',listing.name,'-c','copy',merged.name],cwd=work)
            mix_own_voice=bool(own_duration and mode=='Saját videók')
            from soundtrack_cache import save_source
            self.log('A videó alapjának mentése a későbbi zenecseréhez…')
            save_source(self,merged,dest,elapsed,narration=p.audio if own_duration else '',
                mix_narration=mix_own_voice)
            if p.music:
                self.log('Háttérzene hozzáadása · %g%% hangerő.'%music_volume)
            export_video(self,merged,dest/'video.mp4',elapsed,music=p.music,
                music_volume=music_volume,narration=p.audio if own_duration else '',mix_narration=mix_own_voice)
            def stamp(t):
                ms=round(t*1000); return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
            (dest/'felirat.srt').write_text('\n\n'.join(f'{j+1}\n{stamp(a)} --> {stamp(b)}\n{t}' for j,(a,b,t) in enumerate(cues) if t)+'\n',encoding='utf-8')
            (dest/'youtube_leiras.txt').write_text(p.title+'\n\n'+p.text,encoding='utf-8')
            (dest/'projekt.json').write_text(json.dumps(asdict(p),ensure_ascii=False,indent=2),encoding='utf-8')
            complete_library(self,dest)
            self.progress(100); self.log(f'Elkészült: {dest / "video.mp4"}'); return dest
        except BaseException as error:
            primary_error=error
            # Incomplete videos are never presented as finished exports.
            partial=dest/'video.mp4'
            try:
                if partial.exists():partial.unlink()
            except BaseException as cleanup_error:
                try:self.log('A félkész kimenet eltávolítása nem sikerült: '+str(cleanup_error))
                except BaseException:pass
            raise
        finally:
            cleanup_failure=None
            def cleanup_notice(action,error):
                try:self.log('%s nem sikerült: %s'%(action,error))
                except BaseException:pass
            try:shutil.rmtree(work,ignore_errors=True)
            except BaseException as cleanup_error:
                cleanup_failure=cleanup_error
                cleanup_notice('Az ideiglenes mappa lezárása',cleanup_error)
            if mode in LOCAL_AI_MODES:
                try:
                    from comfy_video import stop_engine
                    stop_engine(self)
                except BaseException as cleanup_error:
                    if cleanup_failure is None:cleanup_failure=cleanup_error
                    cleanup_notice('A saját AI-motor leállítása',cleanup_error)
            if primary_error is None and cleanup_failure is not None:raise cleanup_failure

def ai_script(topic,model,images=(),cancel=None):
    payload={'model':model,'stream':True,'prompt':'Write an English YouTube narration of approximately 150-250 words. Return only the spoken script, without headings, stage directions or markdown. If images are provided, describe only what is visible; do not invent facts. The topic may be given in Hungarian; the output must always be English. Topic: '+topic,'options':{'num_predict':700}}
    if images:
        payload['images']=[]
        for path in images[:8]:
            with Image.open(path) as src:
                src=ImageOps.exif_transpose(src).convert('RGB'); src.thumbnail((1024,1024)); buf=io.BytesIO(); src.save(buf,format='JPEG',quality=85)
            payload['images'].append(base64.b64encode(buf.getvalue()).decode())
    request=urllib.request.Request('http://127.0.0.1:11434/api/generate',json.dumps(payload).encode(),{'Content-Type':'application/json'})
    try:
        result=[]
        with urllib.request.urlopen(request,timeout=180) as response:
            for line in response:
                if cancel and cancel.is_set(): raise Cancelled()
                item=json.loads(line)
                if item.get('error'): raise RuntimeError(item['error'])
                result.append(item.get('response',''))
        text=''.join(result).strip()
        if not text: raise RuntimeError('A modell üres választ adott.')
        return text
    except Cancelled: raise
    except Exception as e: raise RuntimeError('A helyi AI nem érhető el vagy a modell nem megfelelő. Indítsd el az Ollamát, és töltsd le a megadott modellt. Képekhez látásképes modell kell.\n'+str(e))

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from comfy_video import PRESETS,normalized_quality
    from video_look import LIGHTING_STYLES
    from image_motion import (IMAGE_MOTION_STYLES, DEFAULT_IMAGE_MOTION, IMAGE_MOTION_LABELS,
        DANCE_MOTION, SCENE_MOTION, MOTION_EXAMPLES, resolved_motion, validate_motion_request)
    from reference_framing import FRAMING_STYLES, FULL_FRAME, SUBJECT_FRAME, validate_framing
    from video_quality import ENHANCEMENTS,DEFAULT_ENHANCEMENT

    # Warm neutrals keep the editing controls quiet and the main action easy to find.
    colors = {'bg': '#1D1A17', 'card': '#28231F', 'field': '#342E28',
              'border': '#4B4035', 'text': '#F2EADF', 'muted': '#BCAF9C',
              'accent': '#E8B46B', 'accent_hover': '#F1C586', 'ink': '#241B11',
              'button': '#44392E', 'button_hover': '#584733', 'selection': '#795632'}
    app = tk.Tk()
    if '--ui-check' in sys.argv and '--ui-check-hidden' in sys.argv:
        app.attributes('-alpha', 0.0)
    app.title('YouTube Műhely 2 • AI videókészítő • ' + APP_RELEASE)
    window_w = min(1180, max(980, app.winfo_screenwidth() - 80))
    window_h = min(790, max(620, app.winfo_screenheight() - 90))
    app.geometry(f'{window_w}x{window_h}')
    app.minsize(980, 620)
    app.configure(bg=colors['bg'])
    if '--ui-check-geometry' in sys.argv:
        app.geometry(sys.argv[sys.argv.index('--ui-check-geometry') + 1])
    style = ttk.Style()
    style.theme_use('clam')
    style.configure('.', background=colors['card'], foreground=colors['text'], font=('Segoe UI', 10))
    style.configure('TFrame', background=colors['card'])
    style.configure('TLabel', background=colors['card'], foreground=colors['text'])
    style.configure('Muted.TLabel', foreground=colors['muted'], font=('Segoe UI', 9))
    style.configure('TEntry', fieldbackground=colors['field'], foreground=colors['text'],
                    insertcolor=colors['text'], bordercolor=colors['border'],
                    lightcolor=colors['border'], darkcolor=colors['border'], padding=8)
    style.map('TEntry', bordercolor=[('focus', colors['accent'])])
    style.configure('TSpinbox', fieldbackground=colors['field'], foreground=colors['text'],
                    insertcolor=colors['text'], arrowcolor=colors['accent'],
                    bordercolor=colors['border'], lightcolor=colors['border'],
                    darkcolor=colors['border'], padding=7)
    style.map('TSpinbox', bordercolor=[('focus', colors['accent'])])
    style.configure('TCombobox', fieldbackground=colors['field'], foreground=colors['text'],
                    arrowcolor=colors['accent'], bordercolor=colors['border'],
                    lightcolor=colors['border'], darkcolor=colors['border'], padding=7)
    style.map('TCombobox', fieldbackground=[('readonly', colors['field'])],
              foreground=[('readonly', colors['text']), ('disabled', colors['muted'])],
              selectbackground=[('readonly', colors['field'])],
              selectforeground=[('readonly', colors['text'])],
              bordercolor=[('focus', colors['accent'])])
    style.configure('TButton', background=colors['button'], foreground=colors['text'],
                    bordercolor=colors['border'], lightcolor=colors['button'],
                    darkcolor=colors['button'], padding=(12, 7), relief='flat')
    style.map('TButton', background=[('active', colors['button_hover'])],
              foreground=[('disabled', '#8C7D6D')], bordercolor=[('focus', colors['accent'])])
    style.configure('Accent.TButton', background=colors['accent'], foreground=colors['ink'],
                    bordercolor=colors['accent'], lightcolor=colors['accent'],
                    darkcolor=colors['accent'], font=('Segoe UI', 11, 'bold'), padding=(23, 10))
    style.map('Accent.TButton', background=[('active', colors['accent_hover']), ('disabled', '#69523A')],
              foreground=[('disabled', '#BCAF9C')])
    style.configure('Small.TButton', padding=(9, 5), font=('Segoe UI', 9))
    style.configure('TCheckbutton', background=colors['card'], foreground=colors['text'],
                    indicatorbackground=colors['field'], indicatorforeground=colors['accent'],
                    indicatorcolor=colors['field'], bordercolor=colors['border'], padding=3)
    style.map('TCheckbutton', background=[('active', colors['card'])],
              indicatorbackground=[('selected', colors['accent']), ('active', colors['field'])],
              indicatorforeground=[('selected', colors['ink'])])
    style.configure('Warm.Horizontal.TProgressbar', background=colors['accent'],
                    troughcolor=colors['field'], bordercolor=colors['field'],
                    lightcolor=colors['accent'], darkcolor=colors['accent'], thickness=5)
    style.configure('Vertical.TScrollbar', background=colors['button'],
                    troughcolor=colors['bg'], arrowcolor=colors['muted'],
                    bordercolor=colors['bg'], lightcolor=colors['button'], darkcolor=colors['button'])
    style.map('Vertical.TScrollbar', background=[('active', colors['button_hover'])])
    app.option_add('*TCombobox*Listbox.background', colors['field'])
    app.option_add('*TCombobox*Listbox.foreground', colors['text'])
    app.option_add('*TCombobox*Listbox.selectBackground', colors['selection'])
    app.option_add('*TCombobox*Listbox.selectForeground', colors['text'])
    app.option_add('*TCombobox*Listbox.font', ('Segoe UI', 10))

    outer = tk.Frame(app, bg=colors['bg'])
    outer.pack(fill='both', expand=True, padx=22, pady=17)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(1, weight=1)
    header = tk.Frame(outer, bg=colors['bg'])
    header.grid(row=0, column=0, sticky='ew', pady=(0, 17))
    mark = tk.Label(header, text='▶', font=('Segoe UI', 21, 'bold'), bg=colors['accent'],
                    fg=colors['ink'], width=2, padx=5, pady=4)
    mark.pack(side='left', padx=(0, 13))
    identity = tk.Frame(header, bg=colors['bg'])
    identity.pack(side='left', fill='x', expand=True)
    tk.Label(identity, text='YouTube Műhely', font=('Segoe UI', 24, 'bold'),
             bg=colors['bg'], fg=colors['text']).pack(anchor='w')
    tk.Label(identity, text='Az ötletedből mozgó jelenetek, angol hanggal.', font=('Segoe UI', 10),
             bg=colors['bg'], fg=colors['muted']).pack(anchor='w', pady=(1, 0))
    header_mode=tk.StringVar(value='HELYI AI  ·  ANGOL HANG\n'+APP_RELEASE)
    tk.Label(header, textvariable=header_mode, font=('Segoe UI', 9, 'bold'),
             bg=colors['card'], fg=colors['accent'], padx=13, pady=9).pack(side='right')

    body = tk.Frame(outer, bg=colors['bg'])
    body.grid(row=1, column=0, sticky='nsew')
    body.columnconfigure(0, weight=7, uniform='columns')
    body.columnconfigure(1, weight=3, uniform='columns')
    body.rowconfigure(0, weight=1)
    scroll_surfaces = []
    def scroll_column(column, padding):
        host = tk.Frame(body, bg=colors['bg'])
        host.grid(row=0, column=column, sticky='nsew', padx=padding)
        bar = ttk.Scrollbar(host, orient='vertical')
        bar.pack(side='right', fill='y', padx=(5, 0))
        canvas = tk.Canvas(host, bg=colors['bg'], highlightthickness=0,
                           yscrollincrement=24, bd=0)
        canvas.pack(side='left', fill='both', expand=True)
        bar.configure(command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        content = tk.Frame(canvas, bg=colors['bg'])
        window = canvas.create_window((0, 0), window=content, anchor='nw')
        content.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        scroll_surfaces.append((host, canvas, content))
        return content, canvas
    left, left_canvas = scroll_column(0, (0, 8))
    right, canvas = scroll_column(1, (8, 0))
    def wheel(event):
        widget = app.winfo_containing(event.x_root, event.y_root)
        if widget is None: return
        for host, surface, content in scroll_surfaces:
            if str(widget).startswith(str(host)):
                # Text editors keep their usual scrolling when their own text is long.
                if isinstance(widget, (tk.Text, tk.Listbox)) and widget.yview() != (0.0, 1.0): return
                surface.yview_scroll(int(-event.delta / 120), 'units')
                return 'break'
    app.bind_all('<MouseWheel>', wheel)

    cards = []; responsive_labels = []
    def hint(parent, text='', textvariable=None, pady=0):
        options = {'style': 'Muted.TLabel', 'wraplength': 260, 'justify': 'left'}
        if textvariable is not None: options['textvariable'] = textvariable
        else: options['text'] = text
        label = ttk.Label(parent, **options)
        label.pack(anchor='w', fill='x', pady=pady)
        def resize(event):
            # The card/disclosure supplies 15 px padding on either side.
            label.configure(wraplength=max(100, event.width - 34))
        parent.bind('<Configure>', resize, add='+')
        responsive_labels.append(label)
        return label
    def card(parent, title, subtitle=''):
        shell = tk.Frame(parent, bg=colors['card'], highlightbackground=colors['border'],
                         highlightthickness=1, padx=15, pady=13)
        shell.pack(fill='x', pady=(0, 12))
        cards.append((title, shell))
        ttk.Label(shell, text=title, font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        if subtitle: hint(shell, subtitle, pady=(3, 8))
        return shell
    values = {}; images = []; videos = []; events = queue.Queue()
    refresh_plan = [lambda: None]
    current = [None]; last = [None]; last_movie=[None]
    queue_store=[None];queue_runner=[None];queue_window=[None];queue_open_button=[None]
    closing=[False]
    busy = [False]; controls = []; inputs = []
    def button(parent, label, command, **kwargs):
        widget = ttk.Button(parent, text=label, command=command, **kwargs)
        controls.append(widget)
        return widget
    def entry(parent, key, label, default):
        ttk.Label(parent, text=label).pack(anchor='w', pady=(9, 4))
        value = tk.StringVar(value=default); values[key] = value
        widget = ttk.Entry(parent, textvariable=value)
        widget.pack(fill='x'); inputs.append((key, widget))
        return value
    def combo(parent, key, label, options, default=None, labels=None):
        ttk.Label(parent, text=label).pack(anchor='w', pady=(9, 4))
        value = tk.StringVar(value=default or options[0]); values[key] = value
        shown = value
        shown_options = options
        if labels:
            shown = tk.StringVar(value=labels.get(value.get(),value.get()))
            shown_options = [labels.get(option,option) for option in options]
            reverse = {labels.get(option,option): option for option in options}
            def from_display(*args):
                selected=reverse.get(shown.get(),shown.get())
                if value.get()!=selected: value.set(selected)
            def from_value(*args):
                selected=labels.get(value.get(),value.get())
                if shown.get()!=selected: shown.set(selected)
            shown.trace_add('write',from_display); value.trace_add('write',from_value)
        widget = ttk.Combobox(parent, textvariable=shown, values=shown_options, state='readonly')
        widget.pack(fill='x'); inputs.append((key, widget))
        return value
    def editor(parent, height, *, compact=False):
        frame = tk.Frame(parent, bg=colors['field'], highlightbackground=colors['border'], highlightthickness=1)
        frame.pack(fill='x')
        widget = tk.Text(frame, wrap='word', bg=colors['field'], fg=colors['text'],
                         insertbackground=colors['accent'], selectbackground=colors['selection'],
                         selectforeground=colors['text'], relief='flat', bd=0,
                         font=('Segoe UI', 11), height=height, padx=8 if compact else 11,
                         pady=2 if compact else 9, undo=True)
        widget.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
        bar.pack(side='right', fill='y'); widget.configure(yscrollcommand=bar.set)
        widget.bind('<FocusIn>', lambda e: frame.configure(highlightbackground=colors['accent']))
        widget.bind('<FocusOut>', lambda e: frame.configure(highlightbackground=colors['border']))
        return widget

    story = card(left, '01  Az ötleted', 'Add meg a videó címét. Az angol leírásokat a jobb oldalon szerkesztheted.')
    entry(story, 'title', 'Videó címe', 'AI video')
    script_card=card(right,'Angol jelenetleírás','Írd le röviden, mi történjen a képen.')
    prompt = editor(script_card, 2,compact=True)
    hint(script_card, 'Példa: A cat walks through a sunny garden.', pady=(5, 0))
    motion_card=card(left,'Tánc és mozgás','Válassz mozdulatot, majd alkalmazd a jobb oldali jelenetleírásra.')
    hint(motion_card, 'Mozgásminták: az angol leírást szabadon átírhatod.', pady=(0, 4))
    example_row = ttk.Frame(motion_card)
    example_row.pack(fill='x')
    def replace_scene_prompt(content):
        # Tk normally splits delete/insert into two undo operations. Keep a
        # programmatically selected move together as one reversible edit.
        previous=prompt.cget('autoseparators')
        prompt.edit_separator()
        prompt.configure(autoseparators=False)
        try:
            prompt.delete('1.0','end');prompt.insert('1.0',content)
        finally:
            prompt.configure(autoseparators=previous)
            prompt.edit_separator()
    def choose_motion_example(label):
        replace_scene_prompt(MOTION_EXAMPLES[label])
        motion_value.set(DANCE_MOTION if label == 'Tánc' else SCENE_MOTION)
        prompt.focus_set()
    for index, label in enumerate(MOTION_EXAMPLES):
        example_row.columnconfigure(index, weight=1)
        button(example_row, label, lambda label=label: choose_motion_example(label),
               style='Small.TButton').grid(row=0, column=index, sticky='ew',
                   padx=(0 if index == 0 else 5, 0))
    from dance_moves import DANCE_MOVES,DANCE_INTENSITIES,DEFAULT_DANCE,dance_prompt
    dance_panel=ttk.Frame(motion_card);dance_panel.pack(fill='x',pady=(12,0))
    ttk.Label(dance_panel,text='Táncmozdulat').pack(anchor='w',pady=(0,4))
    dance_choice=tk.StringVar(value=DEFAULT_DANCE)
    dance_select=ttk.Combobox(dance_panel,textvariable=dance_choice,
        values=list(DANCE_MOVES),state='readonly')
    dance_select.pack(fill='x')
    dance_description=tk.StringVar(value=DANCE_MOVES[DEFAULT_DANCE]['description'])
    def describe_dance(*args):
        choice=DANCE_MOVES.get(dance_choice.get())
        dance_description.set(choice['description'] if choice else '')
    dance_choice.trace_add('write',describe_dance)
    hint(dance_panel,textvariable=dance_description,pady=(5,0))
    ttk.Label(dance_panel,text='Mozgás erőssége').pack(anchor='w',pady=(8,4))
    dance_intensity=tk.StringVar(value=DANCE_INTENSITIES[0])
    dance_strength=ttk.Combobox(dance_panel,textvariable=dance_intensity,
        values=list(DANCE_INTENSITIES),state='readonly')
    dance_strength.pack(fill='x')
    dance_group=tk.BooleanVar(value=False)
    dance_group_control=ttk.Checkbutton(dance_panel,text='Több szereplő együtt',variable=dance_group)
    dance_group_control.pack(anchor='w',pady=(7,6))
    def apply_dance():
        action=dance_prompt(dance_choice.get(),group=dance_group.get(),intensity=dance_intensity.get())
        replace_scene_prompt(action)
        motion_value.set(DANCE_MOTION)
        if values['visual_mode'].get() not in AI_MODES:values['visual_mode'].set(LOCAL_AI_MODES[0])
        status.set('Táncmozdulat beállítva: '+dance_choice.get()+'. A jelenetleírás szabadon szerkeszthető.')
        prompt.focus_set()
    dance_apply_button=button(dance_panel,'Táncmozdulat alkalmazása',apply_dance,style='Small.TButton')
    dance_apply_button.pack(anchor='w')
    hint(dance_panel,'Az Alkalmazás a jelenetleírást cseréli; a szöveg Ctrl+Z-vel visszaállítható. '
        'Teljes alakos fotóval kezdj. A mozdulatot az AI értelmezi, a pontos koreográfia nem garantált.',pady=(6,0))
    def restore_dance_controls(scene_prompt):
        normalized=' '.join(scene_prompt.split())
        for label in DANCE_MOVES:
            for group in (False,True):
                for intensity in DANCE_INTENSITIES:
                    if normalized==' '.join(dance_prompt(label,group=group,intensity=intensity).split()):
                        dance_choice.set(label);dance_group.set(group);dance_intensity.set(intensity);return True
        return False
    narration_card = card(right, '02  A történet hangja', 'Angol narráció vagy felirat. Hang nélkül hagyd üresen.')
    text = editor(narration_card, 2,compact=True)
    def pick_text():
        filename = filedialog.askopenfilename(filetypes=[('Szöveg', '*.txt')])
        if filename:
            try:
                content = Path(filename).read_text(encoding='utf-8-sig')
                text.delete('1.0', 'end'); text.insert('1.0', content)
            except Exception as error: messagebox.showerror('Szöveg betöltése', str(error))
    button(narration_card, 'Szöveg betöltése', pick_text, style='Small.TButton').pack(anchor='w', pady=(9, 0))

    music_card = card(left, '03  Háttérzene', 'Adj zenét a teljes videóhoz, akár narráció mellé is.')
    entry(music_card, 'music', 'Zenefájl', '')
    music_row=ttk.Frame(music_card); music_row.pack(fill='x',pady=(7,0))
    def pick_music():
        filename=filedialog.askopenfilename(title='Háttérzene kiválasztása',filetypes=[
            ('Zenefájlok','*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma'),('Minden fájl','*.*')])
        if filename: values['music'].set(filename)
    button(music_row,'Zene kiválasztása',pick_music,style='Small.TButton').pack(side='left',padx=(0,7))
    button(music_row,'Zene eltávolítása',lambda: values['music'].set(''),style='Small.TButton').pack(side='left')
    volume_row=ttk.Frame(music_card); volume_row.pack(fill='x',pady=(12,5))
    ttk.Label(volume_row,text='Zene hangereje').pack(side='left',padx=(0,10))
    music_volume_value=tk.StringVar(value='12'); values['music_volume']=music_volume_value
    music_volume_input=ttk.Spinbox(volume_row,from_=0,to=100,increment=1,
        textvariable=music_volume_value,width=6)
    music_volume_input.pack(side='left'); inputs.append(('music_volume',music_volume_input))
    ttk.Label(volume_row,text='%').pack(side='left',padx=(5,0))
    hint(music_card,'Beszéd mellé 12% jó kiindulás. Csak zenéhez válaszd a Nincs narráció lehetőséget, és emeld a zene hangerejét.',pady=(3,0))
    hint(music_card,'A rövidebb zene ismétlődik, a hosszabbat a videó végéig vágjuk. A zene eleje és vége finoman halkul.',pady=(5,0))
    def remix_music():
        from background_music import validate_settings
        try:
            music=values['music'].get().strip()
            volume=validate_settings(music,values['music_volume'].get())
        except Exception as error:
            messagebox.showerror('Háttérzene',str(error));return
        filename=filedialog.askopenfilename(title='Válassz egy kész videót vagy az eredménymappa projektjét',
            initialdir=str(last[0] or values['output'].get()),
            filetypes=[('Kész videó vagy projekt','*.mp4 *.json'),('Videó','*.mp4'),('Projekt','*.json')])
        if not filename:return
        engine=Engine(lambda value:events.put(('log',value)))
        current[0]=engine;set_busy(True);progress.set(0)
        status.set('A kész videó zenéjének újrakeverése…')
        def task():
            try:
                from soundtrack_cache import remix,project_folder_for_selection
                folder=project_folder_for_selection(filename)
                events.put(('remixed',remix(engine,folder,music=music,music_volume=volume)))
            except Cancelled:events.put(('cancel',None))
            except Exception as error:events.put(('error',str(error)))
        threading.Thread(target=task,daemon=True).start()
    button(music_card,'Zene cseréje kész videón…',remix_music,style='Small.TButton').pack(anchor='w',pady=(10,0))
    hint(music_card,'Új zenés változatot ment AI-generálás nélkül. Üres zenefájllal a zene eltávolítható. Mentési hiba után a megmaradt projekt.json is választható.',pady=(5,0))

    scene_card=card(left,'04  Jelenetek és javítás',
        'Nézd vissza a jeleneteket, és cseréld ki a hibás részek képét.')
    scene_window=[None]
    def open_scenes():
        if busy[0]:return
        if scene_window[0] is not None and scene_window[0].winfo_exists():
            scene_window[0].lift();scene_window[0].focus_force();return
        from scene_library_ui import show
        def scene_busy(engine):
            current[0]=engine;set_busy(engine is not None)
            status.set('A jelenetcsere készül…' if engine else 'A jelenetkezelő készen áll.')
        scene_window[0]=show(app,last_movie[0] or last[0],on_busy=scene_busy,
            on_done=lambda video:events.put(('scene_exported',Path(video))))
    button(scene_card,'Elkészült jelenetek…',open_scenes,style='Small.TButton').pack(anchor='w')
    def resume_local_work():
        if busy[0]:return
        filename=filedialog.askopenfilename(title='Megszakadt helyi projekt vagy folytatási napló',
            initialdir=str(last[0] or values['output'].get()),
            filetypes=[('Mentett projekt vagy folytatás','*.json'),('Minden fájl','*.*')])
        if not filename:return
        engine=Engine(lambda value:events.put(('log',value)),lambda value:events.put(('progress',value)))
        current[0]=engine;set_busy(True);progress.set(0)
        status.set('A mentett helyi jelenetek és a forrásfájlok ellenőrzése…')
        def task():
            try:
                from local_resume import resume
                events.put(('done',resume(engine,Path(filename).parent)))
            except Cancelled:events.put(('cancel',None))
            except Exception as error:events.put(('error',str(error)))
            finally:
                if hasattr(engine,'last_project_folder'):
                    events.put(('last_folder',engine.last_project_folder))
        threading.Thread(target=task,daemon=True).start()
    button(scene_card,'Megszakadt helyi munka folytatása…',resume_local_work,
        style='Small.TButton').pack(anchor='w',pady=(8,0))
    hint(scene_card,'A kész jeleneteket megtartja, és csak a hiányzó részeket készíti el. '
        'Helyi videóval és offline angol hanggal működik, új eredménymappába ment.',pady=(6,0))
    hint(scene_card,'Az újonnan készített videók külön jelenetei megmaradnak. '
        'Csereklippel új változat készül, az eredeti angol hanggal és zenével. '
        'A jelenetcsere helyben fut, új AI-generálás nélkül.',pady=(7,0))

    queue_card=card(left,'05  Munkasor',
        'Készíts elő több videót; a program egymás után dolgozza fel őket.')
    queue_summary=tk.StringVar(value='A mentett munkasor külön indítható.')
    def update_queue_summary():
        if queue_store[0] is None:return
        jobs=queue_store[0].items()
        waiting=sum(job['status']=='pending' for job in jobs)
        complete=sum(job['status']=='complete' for job in jobs)
        failed=sum(job['status'] in ('failed','cancelled','interrupted') for job in jobs)
        queue_summary.set(f'{waiting} várakozó  ·  {complete} elkészült  ·  {failed} figyelmet kér')
    def ensure_queue():
        if queue_store[0] is None:
            from render_queue import QueueStore,QueueRunner
            store=QueueStore(queue_file())
            try:
                runner=QueueRunner(store,Engine,
                    on_event=lambda kind,data:events.put(('queue_event',(kind,data))))
            except Exception:
                store.close();raise
            queue_store[0]=store;queue_runner[0]=runner
            update_queue_summary()
        return queue_store[0],queue_runner[0]
    def queue_start_allowed():
        if closing[0]:return False
        if busy[0]:
            messagebox.showinfo('Munkasor','Előbb várd meg a folyamatban lévő művelet végét.')
            return False
        if not any(job['status']=='pending' for job in queue_store[0].items()):return False
        set_busy(True);progress.set(0);status.set('A munkasor indul…')
        return True
    def queue_start_failed():
        if queue_runner[0] is None or not queue_runner[0].is_running:
            current[0]=None;set_busy(False);status.set('A munkasor nem indult el.')
    def open_queue():
        try:
            store,runner=ensure_queue()
            if queue_window[0] is not None and queue_window[0].winfo_exists():
                queue_window[0].lift();queue_window[0].focus_force();return
            from render_queue_ui import show
            queue_window[0]=show(app,store,runner,get_project=snapshot,
                on_start=queue_start_allowed,on_start_failed=queue_start_failed)
        except Exception as error:messagebox.showerror('Munkasor',str(error))
    def enqueue_project():
        try:
            sync_requested_motion()
            project=snapshot()
            store,runner=ensure_queue()
            job=store.add(project)
            update_queue_summary()
            status.set('Munkasorba hozzáadva: '+job['title']+'. A munkasor ablakában indítható.')
        except Exception as error:messagebox.showerror('Munkasor',str(error))
    button(queue_card,'Hozzáadás a munkasorhoz',enqueue_project,
        style='Small.TButton').pack(anchor='w')
    queue_open_button[0]=button(queue_card,'Munkasor megnyitása…',open_queue,
        style='Small.TButton')
    queue_open_button[0].pack(anchor='w',pady=(8,0))
    hint(queue_card,textvariable=queue_summary,pady=(7,0))
    hint(queue_card,'A hozzáadás elmenti az aktuális beállításokat. A forrásképeket, '
        'videókat és zenéket tartsd a helyükön. A munkasor helyi móddal és offline hanggal működik.',
        pady=(6,0))

    def disclosure(parent, label):
        shell = tk.Frame(parent, bg=colors['card'], highlightbackground=colors['border'], highlightthickness=1)
        shell.pack(fill='x', pady=(0, 12))
        inner = ttk.Frame(shell, padding=(15, 1, 15, 13))
        shown = [False]
        def toggle():
            shown[0] = not shown[0]
            trigger.configure(text=('▾  ' if shown[0] else '▸  ') + label)
            if shown[0]: inner.pack(fill='x')
            else: inner.pack_forget()
        trigger = button(shell, '▸  ' + label, toggle, style='Small.TButton')
        trigger.pack(fill='x', padx=9, pady=8)
        return inner
    ai_panel = disclosure(right, 'Segítség a narráció megírásához')
    hint(ai_panel, 'A helyi szövegíróhoz futó Ollama és letöltött modell kell.')
    entry(ai_panel, 'model', 'Ollama-modell', 'gemma3:4b')
    airow = ttk.Frame(ai_panel); airow.pack(fill='x', pady=(10, 0))
    def set_busy(flag):
        busy[0] = flag
        for widget in controls: widget.configure(state='disabled' if flag else 'normal')
        if queue_open_button[0] is not None:queue_open_button[0].configure(state='normal')
        for widget in (dance_select,dance_strength):widget.configure(state='disabled' if flag else 'readonly')
        dance_group_control.configure(state='disabled' if flag else 'normal')
        cancel_button.configure(state='normal' if flag else 'disabled')
        refresh_plan[0]()
    def ai(use_images):
        if use_images and not images:
            messagebox.showinfo('Referenciafotók', 'Először adj hozzá képeket.'); return
        model = values['model'].get().strip(); topic = values['title'].get()
        selected = list(images) if use_images else []
        engine = Engine(); current[0] = engine; set_busy(True)
        status.set('A helyi AI írja az angol narrációt…')
        def task():
            try: events.put(('ai', ai_script(topic, model, selected, engine.cancel)))
            except Cancelled: events.put(('cancel', None))
            except Exception as error: events.put(('error', str(error)))
        threading.Thread(target=task, daemon=True).start()
    button(airow, 'Témából szöveg', lambda: ai(False), style='Small.TButton').pack(fill='x')
    button(airow, 'Képekből szöveg', lambda: ai(True), style='Small.TButton').pack(fill='x',pady=(6,0))

    media_card = card(left, 'Referencia és mozgókép', 'A képeket az AI megmozdítja; saját klipeket is hozzáadhatsz.')
    lb = tk.Listbox(media_card, height=4, bg=colors['field'], fg=colors['text'],
                    selectbackground=colors['selection'], selectforeground=colors['text'],
                    relief='flat', bd=0, highlightthickness=1, highlightbackground=colors['border'],
                    highlightcolor=colors['accent'], activestyle='none', font=('Segoe UI', 10))
    lb.pack(fill='x', pady=(2, 8))
    media_count = tk.StringVar(value='Még nincs hozzáadott kép vagy videó.')
    hint(media_card, textvariable=media_count, pady=(0, 8))
    def refresh():
        lb.delete(0, 'end')
        for path in images: lb.insert('end', 'KÉP   ' + Path(path).name)
        for path in videos: lb.insert('end', 'VIDEÓ   ' + Path(path).name)
        media_count.set(f'{len(images)} kép  ·  {len(videos)} videó' if images or videos else 'Még nincs hozzáadott kép vagy videó.')
        refresh_plan[0]()
    def add_images():
        images.extend(filedialog.askopenfilenames(filetypes=[('Képek', '*.png *.jpg *.jpeg *.webp *.bmp')]))
        refresh()
    def add_videos():
        videos.extend(filedialog.askopenfilenames(filetypes=[('Videók', '*.mp4 *.mov *.mkv *.avi *.webm')]))
        refresh()
    def remove_image():
        for index in reversed(lb.curselection()):
            if index < len(images): images.pop(index)
            else: videos.pop(index - len(images))
        refresh()
    def move_image(step):
        selection = lb.curselection()
        if selection:
            index = selection[0]; collection = images if index < len(images) else videos
            offset = 0 if collection is images else len(images); local = index - offset; target = local + step
            if 0 <= target < len(collection):
                collection[local], collection[target] = collection[target], collection[local]
                refresh(); lb.selection_set(target + offset)
    media_add = ttk.Frame(media_card); media_add.pack(fill='x')
    button(media_add, '+ Kép', add_images, style='Small.TButton').pack(side='left', padx=(0, 7))
    button(media_add, '+ Videó', add_videos, style='Small.TButton').pack(side='left')
    media_edit = ttk.Frame(media_card); media_edit.pack(fill='x', pady=(7, 0))
    button(media_edit, 'Törlés', remove_image, style='Small.TButton').pack(side='left', padx=(0, 7))
    button(media_edit, '↑', lambda: move_image(-1), style='Small.TButton', width=3).pack(side='left', padx=(0, 5))
    button(media_edit, '↓', lambda: move_image(1), style='Small.TButton', width=3).pack(side='left')
    own_panel=ttk.Frame(media_card)
    ttk.Label(own_panel,text='Videók eredeti hangereje').pack(anchor='w',pady=(9,4))
    original_row=ttk.Frame(own_panel);original_row.pack(fill='x')
    video_volume_value=tk.StringVar(value='100');values['video_volume']=video_volume_value
    video_volume_input=ttk.Spinbox(original_row,from_=0,to=100,increment=1,
        textvariable=video_volume_value,width=6)
    video_volume_input.pack(side='left');inputs.append(('video_volume',video_volume_input))
    ttk.Label(original_row,text='%').pack(side='left',padx=(5,0))
    keep_length=tk.BooleanVar(value=True)
    ttk.Checkbutton(own_panel,text='Videók teljes hosszának megtartása',variable=keep_length).pack(anchor='w',pady=(8,0))
    hint(own_panel,'0% némítja a saját videók hangját. Új narráció mellé érdemes halkabbra állítani.',pady=(5,0))
    hint(own_panel,'Új narráció esetén a képsor a felolvasás hosszához igazodik.',pady=(5,0))

    look_card = card(left, 'Kép és fény', 'Válaszd ki a jelenetek hangulatát és részletességét.')
    combo(look_card, 'visual_mode', 'Videó forrása', ['AI-videó • helyi', GROK_MODE, 'Saját videók', 'Képes diavideó'])
    grok_panel=ttk.Frame(look_card)
    grok_panel.pack(fill='x')
    hint(grok_panel,'Fizetős, kísérleti xAI API-kapcsolat. Valódi API-próba még nem történt. '
        'A teljes fotó és az angol leírás elhagyja a PC-t. Külön xAI API-kulcs és egyenleg kell; '
        'a Grok-előfizetés nem biztosít ingyenes API-használatot.',pady=(9,0))
    ttk.Label(grok_panel,text='xAI API-kulcs · csak ebben a futó programban').pack(anchor='w',pady=(9,4))
    grok_key_value=tk.StringVar()
    grok_key_input=ttk.Entry(grok_panel,textvariable=grok_key_value,show='•')
    grok_key_input.pack(fill='x');controls.append(grok_key_input)
    combo(grok_panel,'grok_resolution','Grok generálási felbontás',['720p','1080p'])
    ttk.Label(grok_panel,text='Legfeljebb ennyi fizetős jelenet').pack(anchor='w',pady=(9,4))
    grok_limit_value=tk.StringVar(value='1');values['grok_max_scenes']=grok_limit_value
    grok_limit_input=ttk.Spinbox(grok_panel,from_=1,to=100,increment=1,textvariable=grok_limit_value,width=6)
    grok_limit_input.pack(anchor='w');inputs.append(('grok_max_scenes',grok_limit_input))
    grok_price_hint=tk.StringVar()
    hint(grok_panel,textvariable=grok_price_hint,pady=(6,0))
    hint(grok_panel,'A Megszakítás csak a várakozást állítja le. Az elküldött feladat tovább futhat és számlázható. '
        'Mentett feladatazonosítót új generálás indítása nélkül is le lehet kérni.',pady=(5,0))
    def open_grok_docs():
        import webbrowser
        webbrowser.open('https://docs.x.ai/developers/model-capabilities/video/overview')
    button(grok_panel,'xAI API ismertető és árak ↗',open_grok_docs,style='Small.TButton').pack(anchor='w',pady=(7,0))
    def resume_grok():
        if busy[0]:return
        filename=filedialog.askopenfilename(title='Mentett Grok-feladat kiválasztása',
            filetypes=[('Grok-feladat','grok_job.json'),('JSON','*.json')])
        if not filename:return
        engine=Engine(lambda value:events.put(('log',value)),lambda value:events.put(('progress',value)))
        engine.grok_api_key=grok_key_value.get().strip()
        current[0]=engine;set_busy(True);progress.set(0)
        status.set('A meglévő Grok-feladat lekérése; új generálást nem indít…')
        def task():
            try:
                from grok_video import resume_existing,expected_duration
                result=resume_existing(engine,Path(filename),engine.grok_key())
                engine.verify_grok_video(result,expected_duration(filename))
                events.put(('grok_resumed',result))
            except Cancelled:events.put(('cancel_cloud',None))
            except Exception as error:events.put(('error',str(error)))
            finally:engine.grok_api_key=''
        threading.Thread(target=task,daemon=True).start()
    button(grok_panel,'Grok-feladat eredményének mentése…',resume_grok,style='Small.TButton').pack(anchor='w',pady=(7,0))
    grok_panel.pack_forget()
    from realistic_video import IMAGE_ENGINES,QUALITY_ENGINE
    image_engine_value=combo(look_card,'image_engine','Fotóanimáció minősége',list(IMAGE_ENGINES),QUALITY_ENGINE)
    local_choice_anchor=look_card.winfo_children()[-2]
    motion_value = combo(look_card, 'image_motion', 'Mozgás referenciafotóból',
        [SCENE_MOTION,DANCE_MOTION,DEFAULT_IMAGE_MOTION]+[key for key in IMAGE_MOTION_STYLES if key not in (DEFAULT_IMAGE_MOTION,DANCE_MOTION,SCENE_MOTION)],
        SCENE_MOTION, labels=IMAGE_MOTION_LABELS)
    framing_value = combo(look_card, 'framing', 'Fotó keretezése', list(FRAMING_STYLES), FULL_FRAME)
    framing_hint = tk.StringVar()
    def explain_framing(*args):
        if values['visual_mode'].get()==GROK_MODE:
            framing_hint.set('Grok módban a teljes fotót küldi el. A helyi alanyra közelítés és arcjavítás nem érvényes; az exportkeret később választható.')
            return
        if framing_value.get() == SUBJECT_FRAME:
            framing_hint.set('Széles fotónál az alany körül keskenyebb keretet választ: csak az oldalakból vág, a teljes képmagasság megmarad. Ha nem talál arcot, a teljes fotót használja.')
        else:
            framing_hint.set('A teljes fotót megtartja. Az exportméret a kész videó keretét adja meg; a nagyobb export önmagában nem növeli az AI-részletességet.')
    framing_value.trace_add('write', explain_framing)
    explain_framing()
    hint(look_card, textvariable=framing_hint, pady=(6, 0))
    motion_hint = tk.StringVar()
    def explain_motion(*args):
        if values['visual_mode'].get()==GROK_MODE:
            motion_hint.set('A mozgásválasztó angol utasítást ad a Groknak. A teljes jelenetet együtt generálja; a helyi portrémotor itt nem indul. A kívánt arckinézet és mozgás nem garantálható.')
            return
        if motion_value.get()==DEFAULT_IMAGE_MOTION:
            motion_hint.set('Arcképhez ajánlott. Finom fejmozgás, külön portrémotorral; gyorsabb az általános jeleneteknél. Induláskor a modellek előkészítése is időt kér.')
        elif motion_value.get()==DANCE_MOTION:
            motion_hint.set('Teljes alakos táncot kér a modelltől, a fej, a törzs, a karok és a lábak közös mozgásával. Egész alakos fotót használj, látható lábfejekkel. Az Élethű mód az egész jelenetet együtt generálja; az eredmény eltérhet a leírástól.')
        else:
            motion_hint.set('Az angol leírásból az egész jelenet mozgását generálja. Az Élethű mód együtt készíti el a fejet, a testet és a környezetet. Az eredmény eltérhet a leírástól; a hosszabb és részletesebb jelenet több időt igényel.')
    motion_value.trace_add('write',explain_motion); explain_motion()
    hint(look_card, textvariable=motion_hint, pady=(5, 0))
    lighting_value = combo(look_card, 'lighting', 'Fények és színek', list(LIGHTING_STYLES), 'Eredeti világítás')
    light_hint = tk.StringVar(value=LIGHTING_STYLES[lighting_value.get()]['description'])
    lighting_value.trace_add('write', lambda *args: light_hint.set(
        LIGHTING_STYLES.get(lighting_value.get(), {}).get('description', '')))
    hint(look_card, textvariable=light_hint, pady=(6, 0))
    quality_value=combo(look_card,'enhancement','Képjavítás',list(ENHANCEMENTS),DEFAULT_ENHANCEMENT)
    quality_hint=tk.StringVar(value=ENHANCEMENTS[quality_value.get()])
    quality_value.trace_add('write',lambda *args:quality_hint.set(ENHANCEMENTS.get(quality_value.get(),'')))
    hint(look_card,textvariable=quality_hint,pady=(5,0))
    face_restore=tk.BooleanVar(value=False)
    face_restore_input=ttk.Checkbutton(look_card,
        text='AI-arcjavítás\n(megváltoztathatja az arcrészleteket)',variable=face_restore)
    face_restore_input.pack(anchor='w',pady=(9,0));controls.append(face_restore_input)
    hint(look_card,'Alapból kikapcsolva. Újrarajzolhatja a fotó arcát; bizonytalan szem- vagy szemüvegrészleteknél inkább hagyd kikapcsolva.',pady=(5,0))
    combo(look_card, 'ai_quality', 'AI-jelenet részletessége', list(PRESETS), 'Normál • 832×480',
          labels={'Normál • 832×480':'Normál – kisebb AI-részletesség',
                  'Részletes • 1280×704':'Részletes – jobb AI-részletesség, több idő'})
    engine_hint=tk.StringVar()
    engine_hint_label=hint(look_card,textvariable=engine_hint,pady=(5,0))
    resource_hint=tk.StringVar()
    resource_hint_label=hint(look_card,textvariable=resource_hint,pady=(5,0))
    def sync_requested_motion(*args):
        chosen=resolved_motion(motion_value.get(),prompt.get('1.0','end').strip())
        if chosen!=motion_value.get(): motion_value.set(chosen)
    prompt.bind('<FocusOut>',sync_requested_motion,add='+')
    look_card.pack_configure(after=story)
    media_card.pack_configure(after=look_card)

    export_card = card(left, 'Hang és mentés')
    export_card.pack_configure(after=media_card)
    combo(export_card, 'narration', 'Narráció', list(NARRATIONS))
    hint(export_card, 'Az internetes hang a szöveget külső szolgáltatáshoz küldi.', pady=(5, 0))
    combo(export_card, 'format', 'Videó exportmérete', list(SIZES))
    export_hint=tk.StringVar()
    export_hint_label=hint(export_card,textvariable=export_hint,pady=(5,0))
    entry(export_card, 'seconds', 'Jelenethossz narráció nélkül · másodperc', '5')
    hint(export_card, 'Narráció esetén a jelenet a felolvasás hosszához igazodik.', pady=(5, 0))
    from video_plan import describe_video_plan
    plan_state = [None]
    def explain_plan(*args):
        cloud=values['visual_mode'].get()==GROK_MODE
        header_mode.set(('FIZETŐS GROK API  ·  ANGOL HANG' if cloud else 'HELYI AI  ·  ANGOL HANG')+'\n'+APP_RELEASE)
        if cloud:grok_panel.pack(fill='x',before=local_choice_anchor)
        else:grok_panel.pack_forget()
        for key,widget in inputs:
            if key in ('image_engine','framing','ai_quality'):
                widget.configure(state='disabled' if cloud or busy[0] else 'readonly')
        face_restore_input.configure(state='disabled' if cloud or busy[0] else 'normal')
        grok_key_input.configure(state='disabled' if busy[0] else 'normal')
        try:primary_button.configure(text='Fizetős Grok-videó indítása' if cloud else '▶  Videó készítése')
        except NameError:pass
        explain_framing();explain_motion()
        try:example_seconds=float(values['seconds'].get().replace(',','.'))
        except ValueError:example_seconds=5
        if not math.isfinite(example_seconds) or not 1<=example_seconds<=15:example_seconds=5
        estimate=grok_cost_estimate([example_seconds],values['grok_resolution'].get(),1)
        grok_price_hint.set('1 képes, %g mp-es jelenet becsült alapára: %.2f USD. '
            '2026.10.20-i árlista; az aktuális számla és a narráció miatti hossz eltérhet. '
            'A jelenetkorlát a kérések számát korlátozza, nem pénzösszeget.'%(example_seconds,estimate))
        if values['visual_mode'].get()=='Saját videók':own_panel.pack(fill='x',pady=(6,0))
        else:own_panel.pack_forget()
        plan=describe_video_plan(values['visual_mode'].get(),motion_value.get(),
            image_engine_value.get(),values['ai_quality'].get(),images,
            values['format'].get(),export_sizes=SIZES,framing=framing_value.get(),
            grok_resolution=values['grok_resolution'].get())
        plan_state[0]=plan
        engine_hint.set(plan.generation_hint+' '+plan.time_hint)
        export_hint.set(plan.export_hint)
    refresh_plan[0]=explain_plan
    for key in ('visual_mode','image_motion','image_engine','ai_quality','format','framing','grok_resolution','seconds'):
        values[key].trace_add('write',explain_plan)
    explain_plan()
    sub = tk.BooleanVar(value=True)
    ttk.Checkbutton(export_card, text='Felirat megjelenítése a videóban', variable=sub).pack(anchor='w', pady=(10, 2))
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    entry(export_card, 'output', 'Videók mentési mappája', str(home / 'Videok'))
    def choose_dir():
        folder = filedialog.askdirectory()
        if folder: values['output'].set(folder)
    button(export_card, 'Mentési mappa kiválasztása', choose_dir, style='Small.TButton').pack(anchor='w', pady=(7, 0))

    sound_panel = disclosure(right, 'Saját narráció fájlból')
    for key, label in [('audio', 'Saját narráció')]:
        entry(sound_panel, key, label, '')
        def pick(key=key):
            filename = filedialog.askopenfilename(filetypes=[('Hang', '*.mp3 *.wav *.m4a *.ogg *.flac'), ('Minden fájl', '*.*')])
            if filename: values[key].set(filename)
        button(sound_panel, 'Hangfájl kiválasztása', pick, style='Small.TButton').pack(anchor='w', pady=(6, 0))
    advanced = disclosure(left, 'AI-motor és erőforrások')
    installed = home.parent.parent / 'AI_Motor'
    motor_hint = home / 'AI_MOTOR_HELYE.txt'
    if motor_hint.is_file():
        candidate = Path(motor_hint.read_text(encoding='utf-8-sig').strip())
        if (candidate / 'ComfyUI_windows_portable').is_dir(): installed = candidate
    entry(advanced, 'ai_root', 'AI-motor mappája', str(installed if installed.exists() else home / 'AI_Motor'))
    def pick_ai():
        folder = filedialog.askdirectory()
        if folder: values['ai_root'].set(folder)
    button(advanced, 'AI-motor mappa kiválasztása', pick_ai, style='Small.TButton').pack(anchor='w', pady=(6, 0))
    def setup_ai(portrait=False,realism=False):
        if realism:
            from setup_realism import setup
        elif portrait:
            from setup_portrait import setup
        else:
            from setup_comfy import setup
        target = values['ai_root'].get().strip()
        if not target: messagebox.showerror('AI-motor', 'Válassz AI-motor mappát.'); return
        engine = Engine(); current[0] = engine; set_busy(True)
        status.set('Élethű kiegészítés: kb. 20 GB, a már telepített alapmotor mellé.' if realism else ('Portrémotor letöltése: kb. 0,65 GB.' if portrait else 'Az AMD AI-motor és az alap Wan 5B összetevőinek ellenőrzése és letöltése. A meglévő fájlokat újra használja.'))
        def task():
            try:
                setup(Path(target), lambda value: events.put(('log', value)), engine.cancel)
                events.put(('setup', None))
            except Exception as error:
                events.put(('cancel', None) if engine.cancel.is_set() else ('error', str(error)))
        threading.Thread(target=task, daemon=True).start()
    button(advanced, 'AI-motor letöltése / folytatása', setup_ai, style='Small.TButton').pack(anchor='w', pady=(6, 0))
    button(advanced, 'Portrémotor letöltése / folytatása', lambda: setup_ai(True), style='Small.TButton').pack(anchor='w', pady=(6, 0))
    button(advanced, 'Élethű motor letöltése / folytatása', lambda: setup_ai(realism=True), style='Small.TButton').pack(anchor='w', pady=(6, 0))
    combo(advanced, 'encoder', 'MP4-mentés erőforrásai', ['Automatikus GPU / CPU', 'CPU • összes mag',
          'NVIDIA GPU', 'AMD GPU', 'Intel GPU'])
    def check_gpu_resources():
        engine=Engine();current[0]=engine;set_busy(True)
        status.set('A videomemória foglalásának ellenőrzése…')
        def task():
            try:
                from gpu_resources import probe_gpu_users,describe_gpu_users
                message=describe_gpu_users(probe_gpu_users(engine.cancel))
                engine.check();events.put(('resources',message))
            except Cancelled:events.put(('cancel',None))
            except Exception:events.put(('resources','Videomemória: az ellenőrzés nem érhető el.'))
        threading.Thread(target=task,daemon=True).start()
    button(advanced,'Videomemória ellenőrzése',check_gpu_resources,style='Small.TButton').pack(anchor='w',pady=(9,0))

    bottom = tk.Frame(outer, bg=colors['bg'])
    bottom.grid(row=2, column=0, sticky='ew', pady=(14, 0))
    status = tk.StringVar(value='Készen áll. Adj meg egy jelenetet, majd készítsd el a videót.')
    status_label = tk.Label(bottom, textvariable=status, bg=colors['bg'], fg=colors['muted'],
                            font=('Segoe UI', 9), anchor='w', justify='left', wraplength=window_w - 80, height=2)
    status_label.pack(fill='x')
    bottom.bind('<Configure>', lambda event: status_label.configure(wraplength=max(400, event.width - 12)))
    progress = tk.DoubleVar(value=0)
    ttk.Progressbar(bottom, variable=progress, maximum=100, style='Warm.Horizontal.TProgressbar').pack(fill='x', pady=(7, 12))
    actions = tk.Frame(bottom, bg=colors['bg']); actions.pack(fill='x')
    def snapshot():
        args = {key: value.get() for key, value in values.items()}
        args['seconds'] = float(args['seconds'].replace(',', '.'))
        try:args['grok_max_scenes']=int(args['grok_max_scenes'])
        except ValueError:raise ValueError('A fizetős jelenetek korlátja 1 és 100 közötti egész szám legyen.') from None
        if not 1<=args['grok_max_scenes']<=100:raise ValueError('A fizetős jelenetek korlátja 1 és 100 közötti egész szám legyen.')
        from background_music import validate_settings
        args['music_volume']=validate_settings('',args['music_volume'])
        from background_music import validate_video_volume
        args['video_volume']=validate_video_volume(args['video_volume'])
        if not math.isfinite(args['seconds']) or not 1 <= args['seconds'] <= 120:
            raise ValueError('A jelenethossz 1–120 másodperc lehet.')
        if not args['output'].strip(): raise ValueError('Válassz mentési mappát.')
        return Project(**args, text=text.get('1.0', 'end').strip(), video_prompt=prompt.get('1.0', 'end').strip(),
                       images=list(images), videos=list(videos), subtitles=sub.get(),use_video_length=keep_length.get(),
                       face_restore=face_restore.get())
    def start():
        try:
            sync_requested_motion()
            project = snapshot()
            if not project.text and not project.images and not project.videos and not project.video_prompt:
                raise ValueError('Adj meg jelenetleírást, narrációt vagy képet.')
            if project.visual_mode in AI_MODES:
                validate_motion_request(project.image_motion, project.video_prompt, bool(project.images))
            if project.visual_mode==GROK_MODE:
                from grok_video import validate_options
                validate_options(Engine().grok_prompt(project,project.text,project.images[0] if project.images else None,0),
                    project.seconds,project.grok_resolution,grok_key_value.get().strip() or os.environ.get('XAI_API_KEY',''))
        except Exception as error: messagebox.showerror('Beállítás', str(error)); return
        set_busy(True); progress.set(0); status.set('A videó készítése indul…')
        engine = Engine(lambda value: events.put(('log', value)), lambda value: events.put(('progress', value)))
        engine.grok_api_key=grok_key_value.get().strip()
        current[0] = engine
        def task():
            try: events.put(('done', engine.build(project)))
            except Cancelled: events.put(('cancel_cloud' if project.visual_mode==GROK_MODE else 'cancel', None))
            except Exception as error: events.put(('error', str(error)))
            finally:
                engine.grok_api_key=''
                if hasattr(engine,'last_project_folder'):
                    events.put(('last_folder',engine.last_project_folder))
        threading.Thread(target=task, daemon=True).start()
    primary_button = button(actions, '▶  Videó készítése', start, style='Accent.TButton')
    primary_button.pack(side='left', padx=(0, 9))
    if '--ui-check-grok' in sys.argv:values['visual_mode'].set(GROK_MODE)
    explain_plan()
    def cancel_active():
        if queue_runner[0] is not None and queue_runner[0].is_running:
            queue_runner[0].cancel_current()
        elif current[0]:current[0].cancel.set()
    cancel_button = ttk.Button(actions, text='Megszakítás', state='disabled',command=cancel_active)
    cancel_button.pack(side='left')
    def open_output():
        output = last[0] or Path(values['output'].get())
        if Path(output).exists(): os.startfile(str(output))
        else: messagebox.showinfo('Mentési mappa', 'A mentési mappa az első videó készítésekor jön létre.')
    button(actions, 'Eredmények', open_output).pack(side='right')
    def save_project():
        try:
            project = snapshot()
            filename = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('Projekt', '*.json')])
            if filename: Path(filename).write_text(json.dumps(asdict(project), ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as error: messagebox.showerror('Projekt mentése', str(error))
    def load_project():
        filename = filedialog.askopenfilename(filetypes=[('Projekt', '*.json')])
        if not filename: return
        try:
            data = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            if 'visual_mode' not in data: data['visual_mode'] = 'Képes diavideó'
            project = Project(**data)
            if project.narration == 'Windows-hang • offline': project.narration = 'Angol női LJ • offline'
            if project.narration not in NARRATIONS:raise ValueError('Ismeretlen narrációs beállítás.')
            if project.format not in SIZES: raise ValueError('Ismeretlen képformátum.')
            project.ai_quality=normalized_quality(project.ai_quality)
            if project.image_engine not in IMAGE_ENGINES: raise ValueError('Ismeretlen fotóanimációs motor.')
            if project.lighting not in LIGHTING_STYLES: raise ValueError('Ismeretlen fénybeállítás.')
            if project.image_motion not in IMAGE_MOTION_STYLES: raise ValueError('Ismeretlen fotómozgatási beállítás.')
            validate_framing(project.framing)
            if project.enhancement not in ENHANCEMENTS: raise ValueError('Ismeretlen képjavítási beállítás.')
            if not isinstance(project.face_restore,bool): raise ValueError('Érvénytelen AI-arcjavítási beállítás: bekapcsolva vagy kikapcsolva lehet.')
            if project.visual_mode == 'AI-videó • helyi Wan': project.visual_mode = 'AI-videó • helyi'
            if project.visual_mode not in ('AI-videó • helyi', GROK_MODE, 'Saját videók', 'Képes diavideó'):
                raise ValueError('Ismeretlen képforrás.')
            if project.grok_resolution not in ('720p','1080p'):raise ValueError('A Grok felbontása 720p vagy 1080p lehet.')
            if type(project.grok_max_scenes) is not int or not 1<=project.grok_max_scenes<=100:
                raise ValueError('A fizetős jelenetek korlátja 1 és 100 közötti egész szám legyen.')
            from background_music import validate_settings
            project.music_volume=validate_settings('',project.music_volume)
            from background_music import validate_video_volume
            project.video_volume=validate_video_volume(project.video_volume)
            if not isinstance(project.use_video_length,bool):raise ValueError('Érvénytelen videóhossz-beállítás.')
            for key, value in values.items():
                saved = getattr(project, key)
                if key == 'ai_root' and not saved: continue
                value.set(str(saved))
            text.delete('1.0', 'end'); text.insert('1.0', project.text)
            prompt.delete('1.0', 'end'); prompt.insert('1.0', project.video_prompt)
            restore_dance_controls(project.video_prompt)
            images[:] = project.images; videos[:] = project.videos; refresh(); sub.set(project.subtitles)
            keep_length.set(project.use_video_length)
            face_restore.set(project.face_restore)
            if (Path(filename).parent/'jelenetek.json').is_file():
                last[0]=Path(filename).parent
                chosen=Path(filename)
                candidate=(chosen.with_name(chosen.name[:-len('.projekt.json')]+'.mp4')
                    if chosen.name.endswith('.projekt.json') else chosen.parent/'video.mp4')
                last_movie[0]=candidate
            status.set('A projekt betöltve. Ellenőrizd a jelenetet, majd indítsd a készítést.')
        except Exception as error: messagebox.showerror('Projekt betöltése', str(error))
    button(actions, 'Projekt betöltése', load_project, style='Small.TButton').pack(side='right', padx=(0, 7))
    button(actions, 'Mentés', save_project, style='Small.TButton').pack(side='right', padx=(0, 7))
    menu = tk.Menu(app, bg=colors['card'], fg=colors['text'], activebackground=colors['selection'],
                   activeforeground=colors['text'], tearoff=False)
    app.config(menu=menu)
    menu.add_command(label='Projekt mentése', command=lambda: save_project() if not busy[0] else None)
    menu.add_command(label='Projekt betöltése', command=lambda: load_project() if not busy[0] else None)
    menu.add_command(label='Elkészült jelenetek…',command=open_scenes)
    menu.add_command(label='Munkasor…',command=open_queue)
    pump_timer = [None]
    def pump():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == 'log':
                    status.set(data)
                    if data.startswith('Videomemória:'):resource_hint.set(data)
                elif kind == 'progress': progress.set(data)
                elif kind=='queue_event':
                    event,payload=data
                    update_queue_summary()
                    if event=='job':
                        if payload['status']=='running':
                            current[0]=queue_runner[0].active_engine
                            status.set('Munkasor: '+payload['title']);progress.set(0)
                        elif payload['status']=='complete' and payload.get('result'):
                            last_movie[0]=Path(payload['result']);last[0]=last_movie[0].parent
                    elif event=='progress':progress.set(payload['progress'])
                    elif event=='log':status.set('Munkasor: '+payload['message'])
                    elif event=='error':status.set('Munkasorhiba: '+payload['message'])
                    elif event=='done':
                        current[0]=None;set_busy(False)
                        jobs=queue_store[0].items()
                        attention=sum(job['status'] in ('failed','cancelled','interrupted') for job in jobs)
                        if payload.get('error'):
                            status.set('A munkasor leállt mentési hiba miatt: '+str(payload['error']))
                        else:
                            status.set(('A munkasor leállt. ' if payload.get('stopped') else 'A munkasor elkészült. ')
                                +f'{attention} feladat kér figyelmet. Részletek a munkasor ablakában.')
                elif kind == 'setup': status.set('Az AI-motor telepítve. Indíthatod a videó készítését.'); set_busy(False)
                elif kind == 'resources':resource_hint.set(data);status.set(data);set_busy(False)
                elif kind == 'ai':
                    text.delete('1.0', 'end'); text.insert('1.0', data)
                    status.set('Az angol narráció elkészült. Ellenőrizd a videó készítése előtt.'); set_busy(False)
                elif kind == 'done':
                    last[0] = data;last_movie[0]=data/'video.mp4'
                    status.set('Elkészült: ' + str(data / 'video.mp4')); set_busy(False)
                    messagebox.showinfo('Elkészült', 'A video.mp4 feltölthető a YouTube-ra.\n\n' + str(data))
                elif kind=='last_folder':
                    if last[0]!=Path(data):last_movie[0]=None
                    last[0]=Path(data)
                elif kind=='scene_exported':
                    last[0]=data.parent;last_movie[0]=data;progress.set(100);set_busy(False)
                    status.set('A javított jelenetekkel készült változat elkészült: '+str(data))
                elif kind == 'remixed':
                    last[0]=data.parent;last_movie[0]=data;progress.set(100);set_busy(False)
                    status.set('Az új zenés változat elkészült: '+str(data))
                    messagebox.showinfo('Háttérzene','Az új változat elkészült. Az eredeti videó megmaradt.\n\n'+str(data))
                elif kind=='grok_resumed':
                    last[0]=data.parent;last_movie[0]=None;progress.set(100);set_busy(False)
                    status.set('A meglévő Grok-feladat videója mentve: '+str(data))
                    messagebox.showinfo('Grok-videó mentve','A meglévő feladat videója mentve. Új generálás nem indult. '
                        'A videó saját klipként betölthető angol narráció és zene hozzáadásához.\n\n'+str(data))
                elif kind == 'cancel': status.set('Megszakítva.'); set_busy(False)
                elif kind=='cancel_cloud':
                    status.set('A várakozás leállt. Az elküldött Grok-feladat tovább futhat és számlázható; a mentett feladatból később lekérhető.');set_busy(False)
                elif kind == 'error': status.set('A művelet nem sikerült.'); set_busy(False); messagebox.showerror('Hiba', data)
        except queue.Empty: pass
        pump_timer[0] = app.after(100, pump)
    def close():
        closing[0]=True
        editor_open=scene_window[0] is not None and scene_window[0].winfo_exists()
        if editor_open:scene_window[0].scene_library_view.close()
        if queue_runner[0] is not None and queue_runner[0].is_running:
            queue_runner[0].cancel_current()
            status.set('A munkasor megszakítása és mentése…');app.after(200,finish_close);return
        if busy[0]:
            if current[0] is not None:current[0].cancel.set()
            status.set('Megszakítás folyamatban…'); app.after(200, finish_close)
        elif editor_open:app.after(200,finish_close)
        else: destroy_app()
    def destroy_app():
        if queue_store[0] is not None:queue_store[0].close()
        app.destroy()
    def finish_close():
        editor_open=scene_window[0] is not None and scene_window[0].winfo_exists()
        queue_running=queue_runner[0] is not None and queue_runner[0].is_running
        if busy[0] or editor_open or queue_running: app.after(200, finish_close)
        else: destroy_app()
    app.protocol('WM_DELETE_WINDOW', close); pump()
    ui_diagnostic_failed=[False]
    if '--ui-check' in sys.argv or '--ui-preview-data' in sys.argv:
        gui_context=locals()
        def check_ui():
            queue_flow=None
            dance_flow=None
            if '--ui-check-queue-flow' in sys.argv:
                from render_queue_ui import diagnose_main
                report_arg=sys.argv[sys.argv.index('--ui-check')+1]
                queue_flow=diagnose_main(gui_context,Path(report_arg).with_suffix('.queue-flow.json'))
            if '--ui-check-dance-flow' in sys.argv:
                import uuid
                from render_queue import QueueStore
                report_path=Path(sys.argv[sys.argv.index('--ui-check')+1]).resolve()
                fixture=report_path.parent/('dance_flow_'+uuid.uuid4().hex[:8]);fixture.mkdir(parents=True)
                saved_values={key:value.get() for key,value in values.items()}
                saved_prompt=prompt.get('1.0','end-1c');saved_images=list(images)
                saved_choice=(dance_choice.get(),dance_intensity.get(),dance_group.get())
                old_build=Engine.build;calls=[];errors=[]
                old_error=messagebox.showerror
                old_picker=filedialog.askopenfilename
                def reject_generation(*args,**kwargs):
                    calls.append(True);raise RuntimeError('A táncminta alkalmazása nem indíthat generálást.')
                Engine.build=reject_generation
                messagebox.showerror=lambda *args:errors.append(str(args))
                try:
                    photo=fixture/'reference.png';Image.new('RGB',(96,160),'tan').save(photo)
                    images[:]=[str(photo)];values['output'].set(str(fixture/'videos'))
                    values['narration'].set('Nincs narráció');values['visual_mode'].set('Képes diavideó')
                    variants=[]
                    for label in DANCE_MOVES:
                        for group in (False,True):
                            for intensity in DANCE_INTENSITIES:
                                dance_choice.set(label);dance_group.set(group);dance_intensity.set(intensity)
                                dance_apply_button.invoke();project=snapshot()
                                expected=dance_prompt(label,group=group,intensity=intensity)
                                assert project.video_prompt==expected and project.image_motion==DANCE_MOTION
                                assert project.visual_mode==LOCAL_AI_MODES[0]
                                variants.append(dict(label=label,group=group,intensity=intensity,
                                    prompt=project.video_prompt,image_motion=project.image_motion))
                    custom='The original character looks towards the camera.'
                    prompt.delete('1.0','end');prompt.insert('1.0',custom);prompt.edit_reset()
                    dance_apply_button.invoke();prompt.edit_undo()
                    undo_restores=prompt.get('1.0','end-1c')==custom
                    dance_apply_button.invoke();project=snapshot()
                    queue_path=fixture/'munkasor.json'
                    with QueueStore(queue_path) as store:store.add(project)
                    with QueueStore(queue_path) as store:
                        queued=store.items()[0]['project']
                        queue_preserved=queued['video_prompt']==project.video_prompt
                    queue_json=json.loads(queue_path.read_text(encoding='utf-8'))
                    schema_compatible=queue_json['schema']==1 and set(queued)==set(asdict(Project()))
                    project_file=fixture/'projekt.json'
                    project_file.write_text(json.dumps(asdict(project),ensure_ascii=False),encoding='utf-8')
                    expected_choice=(dance_choice.get(),dance_intensity.get(),dance_group.get())
                    dance_choice.set(DEFAULT_DANCE);dance_intensity.set('Finom');dance_group.set(False)
                    prompt.delete('1.0','end');prompt.insert('1.0',custom)
                    filedialog.askopenfilename=lambda **kwargs:str(project_file)
                    load_project()
                    loaded=snapshot()
                    project_preserved=loaded.video_prompt==project.video_prompt and (
                        dance_choice.get(),dance_intensity.get(),dance_group.get())==expected_choice
                    set_busy(True)
                    busy_blocks_dance=dance_apply_button.instate(['disabled']) and dance_select.instate(['disabled'])
                    set_busy(False)
                    dance_flow=dict(status='PASS',variants=variants,real_hidden_tk=True,
                        undo_restores_previous=undo_restores,applying_does_not_generate=not calls,
                        selected_prompt_persisted_in_queue=queue_preserved,
                        selected_prompt_survives_project_reload=project_preserved,
                        v13_queue_schema_compatible=schema_compatible,busy_blocks_dance=busy_blocks_dance,
                        errors=errors,ai_generation_used=False,network_or_paid_requests=False)
                    assert all((undo_restores,not calls,queue_preserved,project_preserved,
                        schema_compatible,busy_blocks_dance)) and not errors,dance_flow
                finally:
                    Engine.build=old_build;messagebox.showerror=old_error;filedialog.askopenfilename=old_picker
                    for key,value in saved_values.items():values[key].set(value)
                    images[:]=saved_images
                    prompt.delete('1.0','end');prompt.insert('1.0',saved_prompt)
                    dance_choice.set(saved_choice[0]);dance_intensity.set(saved_choice[1]);dance_group.set(saved_choice[2])
                    set_busy(False)
                app.update_idletasks()
                previous_scroll=left_canvas.yview()[0]
                reached=[]
                for control in (dance_select,dance_strength,dance_group_control,dance_apply_button):
                    left_canvas.yview_moveto(0);app.update_idletasks()
                    offset=max(0,control.winfo_rooty()-left.winfo_rooty()-12)
                    left_canvas.yview_moveto(offset/max(1,left.winfo_height()))
                    app.update_idletasks()
                    x=control.winfo_rootx()-left_canvas.winfo_rootx()
                    y=control.winfo_rooty()-left_canvas.winfo_rooty()
                    width,height=control.winfo_width(),control.winfo_height()
                    reachable=(control.winfo_ismapped() and x>=-2 and y>=-2
                        and x+width<=left_canvas.winfo_width()+2 and y+height<=left_canvas.winfo_height()+2)
                    reached.append(dict(kind=control.winfo_class(),bounds=[x,y,width,height],
                        viewport=[left_canvas.winfo_width(),left_canvas.winfo_height()],reachable=bool(reachable)))
                left_canvas.yview_moveto(previous_scroll)
                dance_flow['controls_reachable_by_scroll']=all(item['reachable'] for item in reached)
                dance_flow['scrolled_controls']=reached
                assert dance_flow['controls_reachable_by_scroll'],reached
            app.update_idletasks()
            def box(widget):
                return [widget.winfo_rootx() - app.winfo_rootx(), widget.winfo_rooty() - app.winfo_rooty(),
                        widget.winfo_width(), widget.winfo_height()]
            action_box = box(actions); primary_box = box(primary_button)
            def widget_data(widget):
                kind = widget.winfo_class()
                def option(name, fallback=''):
                    try: return widget.cget(name)
                    except tk.TclError: return fallback
                style_name = str(option('style') or kind)
                if isinstance(widget, tk.Text): content = widget.get('1.0', 'end-1c')
                elif isinstance(widget, tk.Listbox): content = '\n'.join(widget.get(0, 'end'))
                elif isinstance(widget, ttk.Entry): content = '[rejtett]' if option('show') else widget.get()
                else:
                    content = str(option('text'))
                    variable = option('textvariable')
                    if variable:
                        try: content = str(app.getvar(str(variable)))
                        except tk.TclError: pass
                bg = option('background') or style.lookup(style_name, 'fieldbackground' if kind in ('TEntry', 'TCombobox') else 'background')
                fg = option('foreground') or style.lookup(style_name, 'foreground')
                return {'id': str(widget), 'parent': str(widget.master) if widget.master else '',
                        'kind': kind, 'bounds': box(widget), 'mapped': bool(widget.winfo_ismapped()),
                        'height_rows': int(widget.cget('height')) if isinstance(widget,tk.Text) else None,
                        'text': content, 'background': str(bg), 'foreground': str(fg),
                        'font': str(option('font') or style.lookup(style_name, 'font')),
                        'style': style_name, 'wraplength': str(option('wraplength'))}
            widgets = []
            def visit(widget):
                for child in widget.winfo_children():
                    widgets.append(widget_data(child)); visit(child)
            visit(outer)
            report = {'window': [app.winfo_width(), app.winfo_height()], 'palette': colors,
                      'queue_flow': queue_flow,
                      'dance_flow':dance_flow,
                      'scroll_content': right.winfo_reqheight(), 'viewport': canvas.winfo_height(),
                      'left_scroll_content': left.winfo_reqheight(), 'left_viewport': left_canvas.winfo_height(),
                      'actions_visible': action_box[1] >= 0 and action_box[1] + action_box[3] <= app.winfo_height(),
                      'primary_button_visible': primary_box[0] >= 0 and primary_box[1] >= 0 and
                          primary_box[0] + primary_box[2] <= app.winfo_width() and
                          primary_box[1] + primary_box[3] <= app.winfo_height(),
                      'primary_button_bounds': primary_box, 'actions_bounds': action_box,
                      'cards': {name: box(widget) for name, widget in cards},
                      'fields': list(values), 'lighting': values['lighting'].get(),
                      'image_motion': values['image_motion'].get(),
                      'framing': values['framing'].get(),
                      'face_restore':face_restore.get(),
                      'face_restore_control':widget_data(face_restore_input),
                      'use_video_length': keep_length.get(),
                      'ai_root': values['ai_root'].get(),
                      'video_plan': asdict(plan_state[0]),
                      'video_plan_hints': {'generation': widget_data(engine_hint_label),
                          'export': widget_data(export_hint_label)},
                      'gpu_resource_hint':widget_data(resource_hint_label),
                      'field_widgets': {key: {'bounds': box(widget), 'mapped': bool(widget.winfo_ismapped()),
                          'value': values[key].get()} for key, widget in inputs},
                      'editors': {'scene_prompt': widget_data(prompt), 'narration': widget_data(text),
                          'media': widget_data(lb)},
                      'scroll_columns': [{'viewport_bounds': box(surface), 'content_bounds': box(content)}
                          for host, surface, content in scroll_surfaces],
                      'responsive_hints': [widget_data(widget) for widget in responsive_labels],
                      'preview_data': {'kind': 'widget-layout-data', 'is_screenshot': False,
                          'description': 'Tk widget content, colors and measured bounds for a layout preview.',
                          'widgets': widgets}}
            for flag in ('--ui-check', '--ui-preview-data'):
                if flag in sys.argv:
                    Path(sys.argv[sys.argv.index(flag) + 1]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if pump_timer[0]: app.after_cancel(pump_timer[0])
            app.destroy()
        def checked_ui():
            try:check_ui()
            except Exception:
                import traceback
                ui_diagnostic_failed[0]=True
                for flag in ('--ui-check','--ui-preview-data'):
                    if flag in sys.argv:
                        Path(sys.argv[sys.argv.index(flag)+1]).with_suffix('.error.txt').write_text(
                            traceback.format_exc(),encoding='utf-8')
                app.destroy()
        app.after(1000, checked_ui)
    app.mainloop()
    if ui_diagnostic_failed[0]:sys.exit(1)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--tts':
        import edge_tts
        try: asyncio.run(edge_tts.Communicate(Path(sys.argv[2]).read_text(encoding='utf-8'),sys.argv[4]).save(sys.argv[3]))
        except Exception:
            # Avoid a blocking Windows error dialog in the background worker.
            sys.exit(1)
    elif len(sys.argv)>1 and sys.argv[1] in ('--run-local-queue','--queue-ui-check'):
        try:
            if sys.argv[1]=='--run-local-queue':run_local_queue(sys.argv[2])
            else:
                from render_queue_ui import ui_check
                ui_check(sys.argv[2])
        except Exception:
            import traceback
            Path(sys.argv[2]).with_suffix('.error.txt').write_text(traceback.format_exc(),encoding='utf-8');sys.exit(1)
    elif len(sys.argv)>1 and sys.argv[1] in ('--render','--remix','--scene-edit','--scene-ui-check','--resume-local'):
        try:
            data=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
            engine=Engine(print,lambda n:print(f'{n:.0f}%',flush=True))
            if sys.argv[1]=='--render':engine.build(Project(**data))
            elif sys.argv[1]=='--resume-local':
                from local_resume import resume
                folder=resume(engine,data['project_folder'])
                Path(sys.argv[2]).with_suffix('.result.json').write_text(
                    json.dumps({'video':str(folder/'video.mp4')},ensure_ascii=False,indent=2),encoding='utf-8')
            elif sys.argv[1]=='--scene-ui-check':
                from scene_library_ui import diagnose
                report=diagnose(data['project_folder'],data['report'])
                if report['status']!='PASS':raise RuntimeError('A jelenetkezelő felületi ellenőrzése nem sikerült.')
            elif sys.argv[1]=='--scene-edit':
                from scene_library import export_variant
                if not isinstance(data.get('replacements'),dict) or any(
                        not isinstance(key,str) or not re.fullmatch(r'0|[1-9]\d{0,2}',key)
                        for key in data['replacements']):
                    raise ValueError('A jelenetcserék sorszáma érvénytelen.')
                replacements={int(key):value for key,value in data['replacements'].items()}
                video=export_variant(engine,data['project_folder'],replacements,soundtrack=data.get('soundtrack'))
                Path(sys.argv[2]).with_suffix('.result.json').write_text(
                    json.dumps({'video':str(video)},ensure_ascii=False,indent=2),encoding='utf-8')
            else:
                from soundtrack_cache import remix
                video=remix(engine,data['project_folder'],music=data.get('music',''),
                    music_volume=data.get('music_volume',12))
                Path(sys.argv[2]).with_suffix('.result.json').write_text(
                    json.dumps({'video':str(video)},ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:
            import traceback
            Path(sys.argv[2]).with_suffix('.error.txt').write_text(traceback.format_exc(),encoding='utf-8'); sys.exit(1)
    else: gui()
