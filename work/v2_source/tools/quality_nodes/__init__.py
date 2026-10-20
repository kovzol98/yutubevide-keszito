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
            finally:
                # Both models remain internal to this node. No other workflow
                # branch can retain or accidentally reuse a released model.
                comfy.model_management.unload_all_models()
                del shifted,model
                gc.collect();comfy.model_management.soft_empty_cache()
            logging.info('YouTube quality stage %d/2 complete',phase+1)
            quality_phase(phase+1,'complete')
        return (current,)


NODE_CLASS_MAPPINGS={'YouTubeGGUFRamLoader':YouTubeGGUFRamLoader,'YouTubeWan14RamSampler':YouTubeWan14RamSampler}
NODE_DISPLAY_NAME_MAPPINGS={'YouTubeGGUFRamLoader':'YouTube • RAM cached GGUF','YouTubeWan14RamSampler':'YouTube • two-stage quality sampler'}
