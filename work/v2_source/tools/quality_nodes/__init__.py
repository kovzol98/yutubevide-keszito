"""Expand a verified GGUF once into RAM, then use normal Comfy CPU offload.

This loader trades system memory for fewer repeated GPU unpacking
operations. It has no effect on existing UnetLoaderGGUF workflows.
"""
import gc
import importlib
import inspect
import logging
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
