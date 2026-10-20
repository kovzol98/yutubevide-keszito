"""One conservative decode-only recovery from this job's saved Wan latent."""
from __future__ import annotations
import copy,hashlib,math,re
from pathlib import Path


def decode_recovery(engine,base,prompt_id,result,graph,prefix,frames,output_root,information,request):
    """Return a proven local checkpoint and smaller decoder graph, or None.

    No requests mutate server state here. Cancellation propagates. Only the
    caller may submit the returned graph, at most once for its quality job.
    """
    engine.check()
    try:
        process=getattr(engine,'_comfy_process',None)
        if (process is None or process.poll() is not None or
            getattr(engine,'_comfy_url',None)!=base or
            getattr(engine,'_comfy_prompt_id',None)!=prompt_id):return None
        if graph.get('7',{}).get('class_type')!='YouTubeWan14RamSampler':return None
        decoder=graph['9'];checkpoint=graph['15']
        if (decoder['class_type'] not in ('VAEDecode','VAEDecodeTiled') or
            decoder['inputs']['samples']!=['15',0] or
            checkpoint['class_type']!='SaveLatent' or
            checkpoint['inputs']['samples']!=['7',0] or
            checkpoint['inputs']['filename_prefix']!=prefix+'_latent'):return None
        if decoder['class_type']=='VAEDecodeTiled' and decoder['inputs'].get('tile_size',0)<=256:return None
        status=result['status']
        if status.get('status_str')!='error':return None
        messages=status.get('messages',[])
        if any(item[0]=='execution_interrupted' for item in messages):return None
        errors=[item[1] for item in messages if item[0]=='execution_error']
        if len(errors)!=1:return None
        error=errors[0]
        kind=error.get('exception_type','').rsplit('.',1)[-1]
        detail=error.get('exception_message','').lower()
        if (error.get('prompt_id')!=prompt_id or str(error.get('node_id'))!='9' or
            error.get('node_type')!=decoder['class_type'] or
            '15' not in [str(node) for node in error.get('executed',[])]):return None
        if any(text in detail for text in ('illegal memory access','device-side assert')):return None
        if not (kind=='OutOfMemoryError' or (kind=='RuntimeError' and 'out of memory' in detail)):return None
        if not re.fullmatch(r'YouTubeMuhely/jelenet_\d{3}_[0-9a-f]{10}',prefix):return None
        saved=result.get('outputs',{}).get('15',{}).get('latents',[])
        if len(saved)!=1:return None
        locator=saved[0]
        name=locator.get('filename','')
        if (locator.get('type')!='output' or locator.get('subfolder')!='YouTubeMuhely' or
            not re.fullmatch(re.escape(prefix.split('/')[-1])+r'_latent_\d{5,}_[.]latent',name)):return None
        output_root=Path(output_root).absolute()
        path=output_root/'YouTubeMuhely'/name
        if not path.is_file() or path.stat().st_size<=8:return None
        for ancestor in (path,*path.parents):
            if ancestor.is_symlink() or (hasattr(ancestor,'is_junction') and ancestor.is_junction()):return None
        if path.resolve(strict=True)!=path.absolute():return None
        temporal_size=max(64,4*math.ceil(frames/4))
        if temporal_size>4096:return None
        if any(node not in information for node in ('LoadLatent','VAELoader','VAEDecodeTiled','SaveImage')):return None
        fields=information['VAEDecodeTiled']['input']
        if not {'tile_size','overlap','temporal_size','temporal_overlap'}<=set(fields.get('required',{}))|set(fields.get('optional',{})):return None
        if graph['3']['class_type']!='VAELoader' or decoder['inputs']['vae']!=['3',0]:return None
        # An uncertain/nonempty queue never triggers automatic recovery. No
        # /free, global interruption or process restart is necessary here.
        queue=request(base,'/queue',timeout=5)
        if queue.get('queue_running')!=[] or queue.get('queue_pending')!=[]:return None
        engine.check()
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                engine.check();digest.update(block)
        if (process.poll() is not None or getattr(engine,'_comfy_process',None) is not process or
            getattr(engine,'_comfy_url',None)!=base or getattr(engine,'_comfy_prompt_id',None)!=prompt_id):return None
        retry={
            '3':copy.deepcopy(graph['3']),
            '16':dict(class_type='LoadLatent',inputs=dict(latent='YouTubeMuhely/'+name+' [output]')),
            '9':dict(class_type='VAEDecodeTiled',inputs=dict(samples=['16',0],vae=['3',0],
                tile_size=256,overlap=64,temporal_size=temporal_size,temporal_overlap=4)),
            '10':dict(class_type='SaveImage',inputs=dict(images=['9',0],filename_prefix=prefix+'_decode_retry'))}
        return dict(graph=retry,checkpoint=dict(path=str(path),sha256=digest.hexdigest(),
            locator=locator,original_prompt_id=prompt_id),original_error=error)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,IndexError,AttributeError):
        # Missing proof preserves the original failure and checkpoint; it must
        # never turn into a new sampling request or obscure cancellation.
        return None
