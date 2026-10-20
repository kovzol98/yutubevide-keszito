"""Minimal inference-only utilities; pretrained weights are loaded strictly."""
from torch import nn

class Registry:
    def register(self):
        return lambda cls:cls

ARCH_REGISTRY=Registry()

def default_init_weights(module_list,scale=1,bias_fill=0,**kwargs):
    if not isinstance(module_list,list):module_list=[module_list]
    for module in module_list:
        for item in module.modules():
            if isinstance(item,(nn.Conv2d,nn.Linear)):
                nn.init.kaiming_normal_(item.weight,**kwargs);item.weight.data*=scale
                if item.bias is not None:item.bias.data.fill_(bias_fill)
            elif isinstance(item,nn.BatchNorm2d):
                nn.init.constant_(item.weight,1)
                if item.bias is not None:item.bias.data.fill_(bias_fill)
