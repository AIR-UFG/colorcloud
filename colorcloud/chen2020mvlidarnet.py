# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/02_chen2020mvlidarnet.ipynb.

# %% auto 0
__all__ = ['ConvBNReLU', 'InceptionV2', 'InceptionBlock', 'Encoder', 'Decoder', 'MVLidarNet']

# %% ../nbs/02_chen2020mvlidarnet.ipynb 4
import torch
from torch import nn

# %% ../nbs/02_chen2020mvlidarnet.ipynb 5
class ConvBNReLU(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding),
            nn.BatchNorm2d(out_channels),
            nn.ReLU()
        )
    
    def forward(self, x):
        return self.net(x)

# %% ../nbs/02_chen2020mvlidarnet.ipynb 7
class InceptionV2(nn.Module):
    "InceptionV2 Block"
    def __init__(self, in_channels, out_channels):
        super().__init__()
        out_channels = out_channels//4
        
        # 1x1
        self.b1 = ConvBNReLU(in_channels, out_channels, kernel_size=1, stride=1, padding=0)
        
        # pool -> 1x1
        self.b2 = nn.Sequential(
            nn.MaxPool2d(kernel_size=3 , stride=1 , padding=1),
            ConvBNReLU(in_channels, out_channels, kernel_size=1, stride=1, padding=0)
        )
        
        # 1x1 -> 3x3
        self.b3 = nn.Sequential(
            ConvBNReLU(in_channels, out_channels, kernel_size=1, stride=1, padding=0),
            ConvBNReLU(out_channels, out_channels, kernel_size=3, stride=1, padding=1)
        )
        
        # 1x1 -> 3x3 -> 3x3
        self.b4 = nn.Sequential(
            ConvBNReLU(in_channels, out_channels, kernel_size=1, stride=1, padding=0),
            ConvBNReLU(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            ConvBNReLU(out_channels, out_channels, kernel_size=3, stride=1, padding=1)
        )
    
    def forward(self, x):
        branch1 = self.b1(x)
        branch2 = self.b2(x)
        branch3 = self.b3(x)
        branch4 = self.b4(x)
        return torch.cat((branch1, branch2, branch3, branch4), dim=1)

# %% ../nbs/02_chen2020mvlidarnet.ipynb 9
class InceptionBlock(nn.Module):
    def __init__(self, in_channels, out_channels, n_modules):
        super().__init__()
        modules_in_block = []
        for i in range(n_modules):
            modules_in_block += [InceptionV2(in_channels, out_channels)]
            in_channels = out_channels
        self.net = nn.Sequential(*modules_in_block)
    
    def forward(self, x):
        return self.net(x)

# %% ../nbs/02_chen2020mvlidarnet.ipynb 11
class Encoder(nn.Module):
    def __init__(self, in_channels=5):
        super().__init__()

        self.trunk1 = ConvBNReLU(in_channels, 64, kernel_size=3, stride=1, padding=1)
        self.trunk2 = ConvBNReLU(64, 64, kernel_size=3, stride=1, padding=1)
        self.trunk3 = nn.Sequential(
            ConvBNReLU(64, 128, kernel_size=3, stride=1, padding=1),
            nn.MaxPool2d(2)
        )
        
        self.block1 = InceptionBlock(in_channels=128, out_channels=64, n_modules=2)
        self.block2 = nn.Sequential(
            InceptionBlock(in_channels=64, out_channels=64, n_modules=2),
            nn.MaxPool2d(2)
        )
        self.block3 = nn.Sequential(
            InceptionBlock(in_channels=64, out_channels=128, n_modules=3),
            nn.MaxPool2d(2)
        )
    
    def forward(self, x):
        enc_features = []
        
        x = self.trunk1(x)
        x = self.trunk2(x)
        x = self.trunk3(x)
        x = self.block1(x)
        enc_features.append(x)
        
        x = self.block2(x)
        enc_features.append(x)
        
        x = self.block3(x)
        enc_features.append(x)
        
        return enc_features

# %% ../nbs/02_chen2020mvlidarnet.ipynb 13
class Decoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.up1a = nn.ConvTranspose2d(128, 256, kernel_size=6, stride=2, padding=2)
        self.up1c = ConvBNReLU(256+64, 256, kernel_size=1, stride=1, padding=0)
        self.up1d = ConvBNReLU(256, 256, kernel_size=3, stride=1, padding=1)
        
        self.up2a = nn.ConvTranspose2d(256, 128, kernel_size=6, stride=2, padding=2)
        self.up2c = ConvBNReLU(128+64, 128, kernel_size=1, stride=1, padding=0)
        self.up2d = ConvBNReLU(128, 128, kernel_size=3, stride=1, padding=1)

        self.up3a = nn.ConvTranspose2d(128, 64, kernel_size=6, stride=2, padding=2)
        self.up3b = ConvBNReLU(64, 64, kernel_size=1, stride=1, padding=0)
        self.up3c = ConvBNReLU(64, 64, kernel_size=3, stride=1, padding=1)
    
    def forward(self, enc_features):
        x = enc_features[-1]
        
        x = self.up1a(x)
        up1b = torch.cat((x, enc_features[-2]), dim=1)
        x = self.up1c(up1b)
        x = self.up1d(x)
        
        x = self.up2a(x)
        up2b = torch.cat((x, enc_features[-3]), dim=1)
        x = self.up2c(up2b)
        x = self.up2d(x)
        
        x = self.up3a(x)
        x = self.up3b(x)
        x = self.up3c(x)
        
        return x

# %% ../nbs/02_chen2020mvlidarnet.ipynb 15
class MVLidarNet(nn.Module):
    def __init__(self, n_classes=7):
        super().__init__()
        self.backbone = nn.Sequential(
            Encoder(5),
            Decoder()
        )
        
        self.classhead1 = ConvBNReLU(64, 64, kernel_size=3, stride=1, padding=1)
        self.classhead2 = ConvBNReLU(64, n_classes, kernel_size=1, stride=1, padding=0)
    
    def forward(self, x):
        features = self.backbone(x)
        x = self.classhead1(features)
        prediction = self.classhead2(x)
        return prediction
