"""Module that implements the model from [RIU-Net: Embarrassingly simple semantic segmentation of 3D LiDAR point cloud](https://arxiv.org/abs/1905.08748)."""

# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_biasutti2019riu.ipynb.

# %% auto 0
__all__ = ['Block', 'Encoder', 'UpConv', 'Decoder', 'RIUNet', 'WeightedMaskedCELoss', 'SegmentationIoU']

# %% ../nbs/01_biasutti2019riu.ipynb 3
import torch
import torch.nn.functional as F
from torch.nn import Module, Sequential, Conv2d, BatchNorm2d, ReLU, ModuleList, ConvTranspose2d, Upsample, CrossEntropyLoss
from torch.nn.init import kaiming_normal_, constant_, zeros_, normal_
from torch.nn.modules.module import register_module_forward_hook
from torch.optim import AdamW
from torch.optim.lr_scheduler import OneCycleLR
from collections import OrderedDict
import re
import numpy as np

# %% ../nbs/01_biasutti2019riu.ipynb 6
class Block(Sequential):
    "Convolutional block repeatedly used in the RIU-Net encoder and decoder."
    def __init__(self, in_channels, out_channels):
        super().__init__(OrderedDict([
            (f'conv1', Conv2d(in_channels, out_channels, 3, 1, 1, bias=False, padding_mode='circular')),
            (f'bn1', BatchNorm2d(out_channels, momentum=0.01)),
            (f'relu1', ReLU()),
            (f'conv2', Conv2d(out_channels, out_channels, 3, 1, 1, bias=False, padding_mode='circular')),
            (f'bn2', BatchNorm2d(out_channels, momentum=0.01)),
            (f'relu2', ReLU()),
        ]))
        self.init_params()
    
    def init_params(self):
        for n, p in self.named_parameters():
            if re.search('conv\d\.weight', n):
                kaiming_normal_(p, nonlinearity='relu')

# %% ../nbs/01_biasutti2019riu.ipynb 14
class Encoder(Module):
    "RIU-Net encoder architecture."
    def __init__(self, channels=(5, 64, 128, 256, 512, 1024)):
        super().__init__()
        self.blocks = ModuleList(
            [Block(channels[i], channels[i+1]) for i in range(len(channels)-1)]
        )
    
    def forward(self, x):
        enc_features = []
        for block in self.blocks:
            x = block(x)
            enc_features.append(x)
            x = F.max_pool2d(x, 2)
        return enc_features

# %% ../nbs/01_biasutti2019riu.ipynb 18
class UpConv(Sequential):
    "Up-convolution operation adapted from [U-Net](https://arxiv.org/abs/1505.04597)."
    def __init__(self, in_channels, out_channels):
        super().__init__(OrderedDict([
            (f'upsample', Upsample(scale_factor=2)),
            (f'conv', Conv2d(in_channels, out_channels, 3, 1, 1, bias=False, padding_mode='circular')),
            (f'bn', BatchNorm2d(out_channels, momentum=0.01)),
        ]))
        self.init_params()
    
    def init_params(self):
        for n, p in self.named_parameters():
            if re.search('conv.weight', n):
                kaiming_normal_(p, nonlinearity='linear')

# %% ../nbs/01_biasutti2019riu.ipynb 21
class Decoder(Module):
    "RIU-Net decoder architecture."
    def __init__(self, channels=(1024, 512, 256, 128, 64)):
        super().__init__()
        self.upconvs = ModuleList(
            [UpConv(channels[i], channels[i+1]) for i in range(len(channels)-1)]
        )
        self.blocks = ModuleList(
            [Block(channels[i], channels[i+1]) for i in range(len(channels)-1)]
        )
    
    def forward(self, enc_features):
        x = enc_features[-1]
        for i, (upconv, block) in enumerate(zip(self.upconvs, self.blocks)):
            x = upconv(x)
            x = torch.cat([x, enc_features[-(i+2)]], dim=1)
            x = block(x)
        return x

# %% ../nbs/01_biasutti2019riu.ipynb 27
class RIUNet(Module):
    "RIU-Net complete architecture."
    def __init__(self, in_channels=5, hidden_channels=(64, 128, 256, 512, 1024), n_classes=20):
        super().__init__()
        self.n_classes = n_classes
        self.input_norm = BatchNorm2d(in_channels, affine=False, momentum=None)
        self.backbone = Sequential(OrderedDict([
            (f'enc', Encoder((in_channels, *hidden_channels))),
            (f'dec', Decoder(hidden_channels[::-1]))
        ]))
        self.head = Conv2d(hidden_channels[0], n_classes, 1)
        self.init_params()

    def init_params(self):
        for n, p in self.named_parameters():
            if re.search('head\.weight', n):
                normal_(p, std=1e-2)
            if re.search('head\.bias', n):
                zeros_(p)
    
    def forward(self, x):
        x = self.input_norm(x)
        features = self.backbone(x)
        prediction = self.head(features)
        
        return prediction

# %% ../nbs/01_biasutti2019riu.ipynb 35
class WeightedMaskedCELoss(Module):
    "Convenient wrapper for the CrossEntropyLoss module with a `weight` and `ignore_index` paremeters already set."
    def __init__(self, weight, device):
        super().__init__()
        self.ignore_index = -1
        self.wmCE = CrossEntropyLoss(weight=torch.from_numpy(weight).to(device), ignore_index=self.ignore_index)

    def forward(self, pred, label, mask):
        label = label.clone()
        label[~mask] = self.ignore_index
        loss = self.wmCE(pred, label)
        return loss

# %% ../nbs/01_biasutti2019riu.ipynb 38
class SegmentationIoU(Module):
    def __init__(self, num_classes, reduction='mean'):
        assert reduction in ['mean', 'none']
        super().__init__()
        self.num_classes = num_classes
        self.reduction = reduction

    def forward(self, pred, label, mask):
        label = label.clone()
        pred[~mask] *= 0
        label[~mask] *= 0
        oh_pred = F.one_hot(pred, num_classes=self.num_classes)
        oh_label = F.one_hot(label, num_classes=self.num_classes)
        intersect = (oh_pred*oh_label).sum(dim=(1, 2))
        union = ((oh_pred + oh_label).clamp(max=1)).sum(dim=(1,2))
        intersect[union == 0] = 1
        union[union == 0] = 1
        iou = (intersect/union)
        if self.reduction == 'mean':
            iou = iou[:,1:].mean()
        return iou
