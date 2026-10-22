"""Expand a verified GGUF once into RAM, then use normal Comfy CPU offload.

This loader trades system memory for fewer repeated GPU unpacking
operations. It has no effect on existing UnetLoaderGGUF workflows.
"""
import gc
import importlib
import inspect
import logging
import math
import sys
import torch
import comfy.sd
import comfy.model_management
import folder_paths
import nodes
from comfy_extras.nodes_model_advanced import ModelSamplingSD3

def quality_phase(phase,status):
    from comfy_execution.utils import get_executing_context
    import server
    context=get_executing_context()
    if context is not None:
        server.PromptServer.instance.send_sync('ytmuhely_phase',dict(
            prompt_id=context.prompt_id,node=str(context.node_id),phase=phase,status=status))


def plain_tensor(value):
    if type(value) is not torch.Tensor:
        value=value.as_subclass(torch.Tensor)
    return value


def require_finite_latent(latent, phase):
    """Never decode a failed GPU calculation into plausible-looking PNG files."""
    samples=latent.get('samples') if isinstance(latent,dict) else None
    if (not isinstance(samples,torch.Tensor) or samples.numel()==0 or
            not bool(torch.isfinite(samples).all().item())):
        raise RuntimeError('YTMUHELY_INVALID_GPU_RESULT: A videókártya hibás számítási '
            'eredményt adott a(z) %d. szakaszban. A generálás leállt; nem készül '
            'szürke videó. Indítsd újra a videómotort, és használj rövidebb részeket.'%phase)


ATTENTION_SCORE_BYTES = 128 * 1024**2


def _attention_tile_shape(heads, query_tokens, key_tokens,
                          score_budget=ATTENTION_SCORE_BYTES):
    """Bound each FP32 score tensor, including non-divisible final tiles.

    Score, softmax result and a possible mask temporary have a combined
    budget of at most three score buffers. This is independent of WDDM's
    available-memory estimate. Full keys participate in every softmax, so
    tiling changes scheduling only, never the attention calculation.
    """
    if any(type(value) is not int or value <= 0
           for value in (heads, query_tokens, key_tokens, score_budget)):
        raise ValueError('A figyelemszámítás méretei pozitív egész számok legyenek.')
    rows = score_budget // (key_tokens * 4)
    if rows < 1:
        raise ValueError('Egy figyelemsor sem fér a biztonságos memóriakeretbe.')
    head_tile = min(heads, 4, rows)
    query_tile = min(query_tokens, 1024, max(1, rows // head_tile))
    return head_tile, query_tile


def _attention_mask(mask, batch, heads, queries, keys):
    """Normalize broadcastable masks without materializing an attention matrix."""
    if mask is None:
        return None
    if mask.ndim == 2:
        mask = mask.unsqueeze(0).unsqueeze(0)
    elif mask.ndim == 3:
        # Comfy's PyTorch attention treats a 3D mask as [batch, query, key].
        mask = mask.unsqueeze(1)
    elif mask.ndim != 4:
        raise ValueError('A figyelemmaszk két-, három- vagy négydimenziós legyen.')
    if any(size not in (1, target) for size, target
           in zip(mask.shape, (batch, heads, queries, keys))):
        raise ValueError('A figyelemmaszk mérete nem illik a jelenethez.')
    if mask.dtype != torch.bool and not torch.is_floating_point(mask):
        raise ValueError('A figyelemmaszk logikai vagy lebegőpontos legyen.')
    return mask


def bounded_attention(q, k, v, heads, mask=None, attn_precision=None,
                      skip_reshape=False, skip_output_reshape=False, **kwargs):
    """Exact WAN attention with a hard 128 MiB FP32 score-tensor limit.

    Installed on the two models owned by this node only. It does not patch
    Comfy globals, use approximate/local attention, or alter the video canvas.
    The stock split implementation can revert to a full matrix whenever its
    token count is not divisible by its split count. Range-based tiles always
    include a shorter final tile instead. FP32 scores and softmax also avoid
    half-precision accumulation overflow.
    """
    if kwargs.get('is_causal', False) or kwargs.get('dropout_p', 0.0) != 0.0:
        raise ValueError('A helyi WAN-motor nem használ oksági vagy dropout figyelmet.')
    if type(heads) is not int or heads <= 0:
        raise ValueError('A figyelemfejek száma pozitív egész szám legyen.')
    if any(tensor.device != q.device for tensor in (k, v)):
        raise ValueError('A figyelemszámítás tenzorai azonos eszközön legyenek.')
    if any(not torch.is_floating_point(tensor) for tensor in (q, k, v)):
        raise ValueError('A figyelemszámítás lebegőpontos tenzorokat igényel.')
    if skip_reshape:
        if any(tensor.ndim != 4 for tensor in (q, k, v)):
            raise ValueError('A rendezett figyelemtenzorok négydimenziósak legyenek.')
        batch, actual_heads, queries, channels = q.shape
        kv_heads = k.shape[1]
        if actual_heads != heads:
            raise ValueError('A figyelemfejek száma nem egyezik a tenzorral.')
    else:
        if any(tensor.ndim != 3 for tensor in (q, k, v)):
            raise ValueError('A WAN figyelemtenzorok háromdimenziósak legyenek.')
        batch, queries, width = q.shape
        if width % heads:
            raise ValueError('A figyelemtenzor szélessége nem osztható a fejek számával.')
        channels = width // heads
        if channels < 1 or k.shape[-1] % channels:
            raise ValueError('A kulcstenzor szélessége hibás.')
        kv_heads = k.shape[-1] // channels
        if kv_heads < 1 or v.shape[-1] % kv_heads:
            raise ValueError('Az értéktenzor szélessége hibás.')
        q = q.reshape(batch, queries, heads, channels).transpose(1, 2)
        k = k.reshape(k.shape[0], k.shape[1], kv_heads, channels).transpose(1, 2)
        v = v.reshape(v.shape[0], v.shape[1], kv_heads, v.shape[-1] // kv_heads).transpose(1, 2)
    if (min(batch, queries, channels, kv_heads) < 1 or
            k.shape[0] != batch or v.shape[0] != batch or
            k.shape[1] != v.shape[1] or k.shape[2] != v.shape[2] or
            k.shape[-1] != channels or v.shape[-1] < 1):
        raise ValueError('A WAN figyelemtenzorok méretei nem illeszkednek.')
    grouped = kwargs.get('enable_gqa', False)
    if (not grouped and kv_heads != heads) or heads % kv_heads:
        raise ValueError('A lekérdezés- és kulcsfejek száma nem illeszkedik.')
    keys = k.shape[2]
    head_tile, query_tile = _attention_tile_shape(heads, queries, keys)
    mask = _attention_mask(mask, batch, heads, queries, keys)
    if mask is not None and mask.device != q.device:
        raise ValueError('A figyelemmaszk azonos eszközön legyen a tenzorokkal.')
    scale = kwargs.get('scale', channels ** -0.5)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale):
        raise ValueError('A figyelemszámítás skálája véges szám legyen.')
    output = torch.empty((batch, heads, queries, v.shape[-1]),
                         dtype=q.dtype, device=q.device)
    # Prevent an ambient autocast context from converting the bounded FP32
    # matrix multiplication back to half precision.
    with torch.autocast(device_type=q.device.type, enabled=False):
        for batch_index in range(batch):
            for head_start in range(0, heads, head_tile):
                head_stop = min(head_start + head_tile, heads)
                if kv_heads == heads:
                    key = k[batch_index, head_start:head_stop].float()
                    value = v[batch_index, head_start:head_stop].float()
                else:
                    indices = torch.tensor([head // (heads // kv_heads)
                        for head in range(head_start, head_stop)],
                        dtype=torch.long, device=k.device)
                    key = k[batch_index, indices].float()
                    value = v[batch_index, indices].float()
                key = key.transpose(1, 2)
                for query_start in range(0, queries, query_tile):
                    comfy.model_management.throw_exception_if_processing_interrupted()
                    query_stop = min(query_start + query_tile, queries)
                    query = q[batch_index, head_start:head_stop,
                              query_start:query_stop].float()
                    scores = torch.bmm(query, key)
                    scores.mul_(scale)
                    del query
                    fully_masked = None
                    if mask is not None:
                        mask_batch = 0 if mask.shape[0] == 1 else batch_index
                        mask_head = slice(0, 1) if mask.shape[1] == 1 else slice(head_start, head_stop)
                        mask_query = slice(0, 1) if mask.shape[2] == 1 else slice(query_start, query_stop)
                        tile_mask = mask[mask_batch, mask_head, mask_query]
                        if mask.dtype == torch.bool:
                            scores.masked_fill_(~tile_mask, -math.inf)
                        else:
                            scores.add_(tile_mask)
                        fully_masked = torch.isneginf(scores).all(dim=-1, keepdim=True)
                        del tile_mask
                    probabilities = scores.softmax(dim=-1)
                    del scores
                    if fully_masked is not None:
                        # Match SDPA's zero result for a wholly masked row.
                        # Other NaNs remain visible to the finite-latent guard.
                        probabilities.masked_fill_(fully_masked, 0.0)
                    output[batch_index, head_start:head_stop,
                           query_start:query_stop] = torch.bmm(probabilities, value)
                    del probabilities
                del key, value
    if skip_output_reshape:
        return output
    return output.transpose(1, 2).reshape(batch, queries, heads * v.shape[-1])


class YouTubeCpuCLIPLoader:
    """Keep UMT5 GGUF dequantization and text inference entirely on CPU.

    A device selector after a regular loader can be too late for its initial
    load and may retain the GPU-selected inference dtype.  Provide device and
    float32 choices to CLIP construction itself without changing Comfy globals.
    The video diffusion models continue to use their normal GPU/CPU offload.
    """
    @classmethod
    def INPUT_TYPES(cls):
        original=nodes.NODE_CLASS_MAPPINGS.get('CLIPLoaderGGUF')
        if original is None:raise RuntimeError('A ComfyUI-GGUF kiegészítő szükséges.')
        return {'required':{'clip_name':(original.get_filename_list(),),
                            'type':(['wan'],{'default':'wan'})}}
    RETURN_TYPES=('CLIP',)
    FUNCTION='load_clip'
    CATEGORY='YouTube Muhely/quality'

    def load_clip(self,clip_name,type='wan'):
        if type!='wan':raise ValueError('A CPU-s szövegértelmező csak Wan videóhoz használható.')
        original=nodes.NODE_CLASS_MAPPINGS.get('CLIPLoaderGGUF')
        if original is None:raise RuntimeError('A ComfyUI-GGUF kiegészítő szükséges.')
        if clip_name not in original.get_filename_list():
            raise ValueError('Nem található a kiválasztott GGUF-szövegértelmező.')
        gguf_module=importlib.import_module(original.__module__)
        path=folder_paths.get_full_path_or_raise('clip',clip_name)
        data=original().load_data([path])
        cpu=torch.device('cpu')
        logging.info('YouTube text encoder: CPU float32; GPU GGUF dequantization disabled')
        clip=comfy.sd.load_text_encoder_state_dicts(
            clip_type=comfy.sd.CLIPType.WAN,
            state_dicts=data,
            model_options={'custom_operations':gguf_module.GGMLOps,
                'load_device':cpu,'offload_device':cpu,'initial_device':cpu,
                'dtype':torch.float32},
            embedding_directory=folder_paths.get_folder_paths('embeddings'),
            disable_dynamic=True)
        clip.patcher=gguf_module.GGUFModelPatcher.clone(clip.patcher)
        if (clip.patcher.load_device!=cpu or clip.patcher.offload_device!=cpu):
            raise RuntimeError('A szövegértelmező CPU-s betöltése nem igazolható.')
        return (clip,)


class YouTubeGGUFRamLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'unet_name':(folder_paths.get_filename_list('unet_gguf'),),
                            'dtype':(['float16','bfloat16'],{'default':'float16'})}}
    RETURN_TYPES=('MODEL',)
    FUNCTION='load'
    CATEGORY='YouTube Muhely/quality'

    def load(self,unet_name,dtype='float16'):
        original=nodes.NODE_CLASS_MAPPINGS.get('UnetLoaderGGUF')
        if original is None:raise RuntimeError('A ComfyUI-GGUF kiegészítő szükséges.')
        package=original.__module__.rsplit('.',1)[0]
        loader=importlib.import_module(package+'.loader')
        dequant=importlib.import_module(package+'.dequant')
        path=folder_paths.get_full_path_or_raise('unet',unet_name)
        state,extra=loader.gguf_sd_loader(path)
        target=getattr(torch,dtype)
        expected=sum(int(getattr(v,'tensor_shape',v.shape).numel())*2
                     if isinstance(getattr(v,'tensor_shape',None),torch.Size)
                     else int(__import__('math').prod(getattr(v,'tensor_shape',v.shape)))*2
                     for v in state.values())
        available=comfy.model_management.get_free_memory(torch.device('cpu'))
        # Loading also temporarily allocates standard model storage. Preserve
        # headroom for Comfy, the text encoder and Windows rather than paging.
        if available<expected*1.55+6*1024**3:
            raise RuntimeError('A RAM-gyorsításhoz jelenleg nincs elég szabad memória. Zárj be más memóriaigényes programokat.')
        logging.info('YouTube RAM loader: expanding %.1f GB once on CPU',expected/1e9)
        with torch.inference_mode():
            for index,key in enumerate(list(state)):
                state[key]=plain_tensor(dequant.dequantize_tensor(state[key],dtype=target)).contiguous()
                if index%100==0:logging.info('YouTube RAM loader: %d/%d tensors',index,len(state))
        kwargs={}
        if 'metadata' in inspect.signature(comfy.sd.load_diffusion_model_state_dict).parameters:
            kwargs['metadata']=extra.get('metadata',{})
        model=comfy.sd.load_diffusion_model_state_dict(state,model_options={'dtype':target},disable_dynamic=True,**kwargs)
        del state;gc.collect()
        if model is None:raise RuntimeError('A videómodell nem tölthető be.')
        logging.info('YouTube RAM loader: model ready with standard CPU offload')
        return (model,)


class YouTubeWan14RamSampler:
    @classmethod
    def INPUT_TYPES(cls):
        models=folder_paths.get_filename_list('unet_gguf')
        return {'required':{'high_name':(models,),'low_name':(models,),
            'positive':('CONDITIONING',),'negative':('CONDITIONING',),'latent_image':('LATENT',),
            'seed':('INT',{'default':42,'min':0,'max':0xffffffffffffffff})}}
    RETURN_TYPES=('LATENT',)
    FUNCTION='sample'
    CATEGORY='YouTube Muhely/quality'

    def sample(self,high_name,low_name,positive,negative,latent_image,seed):
        current=latent_image
        for phase,name in enumerate((high_name,low_name)):
            quality_phase(phase+1,'loading')
            logging.info('YouTube quality stage %d/2: loading %s',phase+1,name)
            model=YouTubeGGUFRamLoader().load(name,'float16')[0]
            shifted=ModelSamplingSD3().patch(model,5.0)[0]
            shifted.set_model_optimized_attention(bounded_attention)
            try:
                quality_phase(phase+1,'sampling')
                current=nodes.common_ksampler(shifted,seed,4,1.0,'euler','simple',positive,negative,current,
                    disable_noise=phase==1,start_step=phase*2,last_step=(phase+1)*2,
                    force_full_denoise=phase==1)[0]
                require_finite_latent(current,phase+1)
            finally:
                # Both models remain internal to this node. No other workflow
                # branch can retain or accidentally reuse a released model.
                comfy.model_management.unload_all_models()
                del shifted,model
                gc.collect();comfy.model_management.soft_empty_cache()
            logging.info('YouTube quality stage %d/2 complete',phase+1)
            quality_phase(phase+1,'complete')
        return (current,)


NODE_CLASS_MAPPINGS={'YouTubeCpuCLIPLoader':YouTubeCpuCLIPLoader,'YouTubeGGUFRamLoader':YouTubeGGUFRamLoader,'YouTubeWan14RamSampler':YouTubeWan14RamSampler}
NODE_DISPLAY_NAME_MAPPINGS={'YouTubeCpuCLIPLoader':'YouTube • CPU text encoder','YouTubeGGUFRamLoader':'YouTube • RAM cached GGUF','YouTubeWan14RamSampler':'YouTube • two-stage quality sampler'}
