# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_biasutti2019riu.ipynb.

# %% auto 0
__all__ = ['Block', 'Encoder', 'Decoder', 'RIUNet']

# %% ../nbs/01_biasutti2019riu.ipynb 3
import torch
from torch.nn import Module, Sequential, Conv2d, BatchNorm2d, ReLU, ModuleList, MaxPool2d, ConvTranspose2d

# %% ../nbs/01_biasutti2019riu.ipynb 4
class Block(Module):
    "Convolutional block repeatedly used in the RIU-Net encoder and decoder."
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.net = Sequential(
            Conv2d(in_channels, out_channels, 3, 1, 1, bias=False, padding_mode='circular'), 
            BatchNorm2d(out_channels), 
            ReLU(),
            Conv2d(out_channels, out_channels, 3, 1, 1, bias=False, padding_mode='circular'), 
            BatchNorm2d(out_channels), 
            ReLU(),
        )
    
    def forward(self, x):
        return self.net(x)

# %% ../nbs/01_biasutti2019riu.ipynb 7
class Encoder(Module):
    "RIU-Net encoder architecture."
    def __init__(self, channels=(5, 64, 128, 256, 512, 1024)):
        super().__init__()
        self.blocks = ModuleList(
            [Block(channels[i], channels[i+1]) for i in range(len(channels)-1)]
        )
        self.pool = MaxPool2d(2)
    
    def forward(self, x):
        enc_features = []
        for block in self.blocks:
            x = block(x)
            enc_features.append(x)
            x = self.pool(x)
        return enc_features

# %% ../nbs/01_biasutti2019riu.ipynb 10
class Decoder(Module):
    "RIU-Net decoder architecture."
    def __init__(self, channels=(1024, 512, 256, 128, 64)):
        super().__init__()
        self.upconvs = ModuleList(
            [ConvTranspose2d(channels[i], channels[i+1], 6, 2, 2) for i in range(len(channels)-1)]
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

# %% ../nbs/01_biasutti2019riu.ipynb 13
class RIUNet(Module):
    "RIU-Net complete architecture."
    def __init__(self, in_channels=5, hidden_channels=(64, 128, 256, 512, 1024), n_classes=20):
        super().__init__()
        self.backbone = Sequential(
            Encoder((in_channels, *hidden_channels)),
            Decoder(hidden_channels[::-1])
        )
        self.head = Conv2d(hidden_channels[0], n_classes, 1)
    
    def forward(self, x):
        features = self.backbone(x)
        prediction = self.head(features)
        
        return prediction
