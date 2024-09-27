"""Module to handle loading, preprocessing and postprocessing of the data from the simulated world for UFG's dataset made by the AIR-UFG perception team"""

# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/05_2024infufg.ipynb.

# %% auto 0
__all__ = ['UFGSimDataset', 'ProjectionSimTransform', 'ProjectionSimVizTransform', 'plot_projections',
           'ProjectionToTensorTransformSim', 'ProjectionSimToPointCloud', 'SemanticSegmentationSimLDM']

# %% ../nbs/05_2024infufg.ipynb 2
import torch
import re
from torch.utils.data import Dataset, DataLoader, random_split
from torch import nn
import yaml
from pathlib import Path
import numpy as np
from lightning import LightningDataModule
from torchvision.transforms import v2
from .behley2019iccv import SphericalProjection

# %% ../nbs/05_2024infufg.ipynb 4
class UFGSimDataset(Dataset):
    "Load the UFGSim dataset ina pytorch Dataset object."
    def __init__(self, data_path, split='train', transform=None):
        data_path = Path(data_path)
        yaml_path = data_path/'ufg-sim.yaml'
        self.ufgsim_velodyne_path = data_path/'laser_scans'

        with open(yaml_path, 'r') as file:
            metadata = yaml.safe_load(file)

        lasers = metadata['split'][split]
        self.labels_dict = metadata['labels']

        ufgsim_velodyne_fns = []
        for laser in lasers:
            ufgsim_velodyne_fns += list(self.ufgsim_velodyne_path.rglob(f'laser{laser}/*.bin'))
  
        self.frame_ids = [fn.stem for fn in sorted(ufgsim_velodyne_fns)]
        self.frame_lasers = [fn.parts[-2] for fn in sorted(ufgsim_velodyne_fns)]

        self.content = metadata['content']
        max_key = sorted(self.content.keys())[-1]
        self.content_np = np.zeros((max_key+1,), dtype=np.float32)
        for k, v in self.content.items():
            self.content_np[k] = v

        self.learning_map = metadata['learning_map']
        max_key = sorted(self.learning_map.keys())[-1]
        self.learning_map_np = np.zeros((max_key+1,), dtype=int)
        for k, v in self.learning_map.items():
            self.learning_map_np[k] = v
        
        self.learning_map_inv = metadata['learning_map_inv']
        self.learning_map_inv_np = np.zeros((len(self.learning_map_inv),))
        self.content_sum_np = np.zeros_like(self.learning_map_inv_np, dtype=np.float32)
        for k, v in self.learning_map_inv.items():
            self.learning_map_inv_np[k] = v
            self.content_sum_np[k] = self.content_np[self.learning_map_np == k].sum()
        
        self.color_map_bgr = metadata['color_map']
        max_key = sorted(self.color_map_bgr.keys())[-1]
        self.color_map_rgb_np = np.zeros((max_key+1,3))
        for k,v in self.color_map_bgr.items():
            self.color_map_rgb_np[k] = np.array(v[::-1], np.float32)
        
        self.transform = transform
        self.is_test = (split == 'test')


    def learning_remap(self, remapping_rules):
        new_map_np = np.zeros_like(self.learning_map_np, dtype=int)
        max_key = sorted(remapping_rules.values())[-1]
        new_map_inv_np = np.zeros((max_key+1,), dtype=int)
        for k, v in remapping_rules.items():
            new_map_np[self.learning_map_np == k] = v
            if new_map_inv_np[v] == 0:
                new_map_inv_np[v] = self.learning_map_inv_np[k]
        self.learning_map_np = new_map_np
        self.learning_map_inv_np = new_map_inv_np
    
    def __len__(self):
        return len(self.frame_ids)

    def set_transform(self, transform):
        self.transform = transform
        
    def __getitem__(self, idx):
        frame_id = self.frame_ids[idx]
        frame_laser = self.frame_lasers[idx]

        frame_path = self.ufgsim_velodyne_path/frame_laser/(frame_id + '.bin')

        with open(frame_path, 'rb') as f:
            frame = np.fromfile(f, dtype=np.float32).reshape(-1, 4)
            x_frame = frame[:, 0]
            y_frame = frame[:, 1]
            z_frame = frame[:, 2]
            label = frame[:, 3].astype(np.uint32)

        label = self.learning_map_np[label]
        mask = label != 0
        weight = 1./self.content_sum_np[label]

        item = {
            'frame': frame,
            'label': label,
            'mask': mask,
            'weight': weight
        }
        if self.transform:
            item = self.transform(item)
        # return x_frame, y_frame, z_frame, label # in case of separated training 
        return item
        


# %% ../nbs/05_2024infufg.ipynb 11
class ProjectionSimTransform(nn.Module):
    def __init__(self, projection):
        super().__init__()
        self.projection = projection
        self.W = projection.W
        self.H = projection.H

    def forward(self, item):
        frame = item['frame']
        label = item['label']
        mask = item['mask']

        scan_xyz = frame[:,:3]
        depth = np.linalg.norm(scan_xyz, 2, axis=1)

        proj_x, proj_y, outliers = self.projection.get_xy_projections(scan_xyz, depth)

        # filter outliers
        if outliers is not None:
            proj_x = proj_x[~outliers]
            proj_y = proj_y[~outliers]
            scan_xyz = scan_xyz[~outliers]
            depth = depth[~outliers]
            if label is not None:
                label = label[~outliers]
                mask = mask[~outliers]

        order = np.argsort(depth)[::-1]
        info_list = [scan_xyz, depth[..., np.newaxis]]
        if label is not None:
            info_list += [mask[..., np.newaxis]]
            info_list += [label[..., np.newaxis]]

        scan_info = np.concatenate(info_list, axis=-1)
        scan_info = scan_info[order]
        proj_y = proj_y[order]
        proj_x = proj_x[order]


        projections_img = np.zeros((self.H, self.W, 2+len(info_list)), dtype=np.float32)
        projections_img[:,:,-1] -= 1
        projections_img[proj_y, proj_x] = scan_info

        if label is not None:
            frame_img = projections_img[:,:,:-2]
            label_img = projections_img[:,:,-1].astype(int)
            mask_img = projections_img[:,:,-2].astype(bool)
            mask_img = mask_img & (label_img > -1)

        else:
            frame_img = projections_img
            label_img = None
            mask_img = projections_img[:,:,-1] >= 0

        item = {
            'frame': frame_img,
            'label': label_img,
            'mask': mask_img,
        }
        
        return item

# %% ../nbs/05_2024infufg.ipynb 18
class ProjectionSimVizTransform(nn.Module):
    def __init__(self, color_map_rgb_np, learning_map_inv_np):
        super().__init__()
        self.color_map_rgb_np = color_map_rgb_np
        self.learning_map_inv_np = learning_map_inv_np

    def scale(self, img, min_value, max_value):
        img = img.clip(min_value, max_value)
        return (255.*(img - min_value)/(max_value - min_value)).astype(int)

    def forward(self, item):
        frame_img = item['frame']
        label_img = item['label']
        mask_img = item['mask']
        
        normalized_frame_img = None
        if frame_img is not None:
            x = self.scale(frame_img[:,:,0], -100., 100.)
            y = self.scale(frame_img[:,:,1], -100., 100.)
            z = self.scale(frame_img[:,:,2], -31., 5.)
            d = self.scale(frame_img[:,:,3], 0., 100.)
            normalized_frame_img = np.stack((x, y, z, d), axis=-1)
            normalized_frame_img[mask_img == False] *= 0

        colored_label_img = None
        if label_img is not None:
            label_img[mask_img] = self.learning_map_inv_np[label_img[mask_img]]
            colored_label_img = np.zeros(label_img.shape + (3,))
            colored_label_img[mask_img] = self.color_map_rgb_np[label_img[mask_img]]
            colored_label_img = colored_label_img.astype(int)

        item = {
            'frame': normalized_frame_img,
            'label': colored_label_img,
            'mask': mask_img
        }
        return item

# %% ../nbs/05_2024infufg.ipynb 21
def plot_projections(img, label, channels=['x', 'y', 'z', 'r', 'd'], channels_map = {"x": 0, "y": 1, "z": 2, "r": 3, "d": 4}):
    num_channels = len(channels)
    fig_size_vertical = 2*num_channels
    fig, axs = plt.subplots(num_channels + 1, 1, figsize=(20, fig_size_vertical))
    
    for i, (ax, title) in enumerate(zip(axs, channels + ['label'])):
        if i < num_channels:
            j = channels_map[channels[i]]
            ax.imshow(img[:, :, j])
        else:
            ax.imshow(label)
        ax.set_title(title)
        ax.axis('off')
    
    plt.tight_layout()
    plt.show()

# %% ../nbs/05_2024infufg.ipynb 25
class ProjectionToTensorTransformSim(nn.Module):
    "Pytorch transform that converts the projections from np.array to torch.tensor. It also changes the frame image format from (H, W, C) to (C, H, W)."
    def forward(self, item):
        frame_img = item['frame']
        label_img = item['label']
        mask_img = item['mask']
        
        frame_img = np.transpose(frame_img, (2, 0, 1))
        frame_img = torch.from_numpy(frame_img).float()
        if label_img is not None:
            label_img = torch.from_numpy(label_img.astype(np.int64))
        if mask_img is not None:
            mask_img = torch.from_numpy(mask_img)
        
        item = {
            'frame': frame_img,
            'label': label_img,
            'mask': mask_img,
        }
        return item

# %% ../nbs/05_2024infufg.ipynb 37
class ProjectionSimToPointCloud(nn.Module):
    def __init__(self, proj_fov_up=15.0, proj_fov_down=-15.0):
        super().__init__()
        self.proj_fov_up = proj_fov_up
        self.proj_fov_down = proj_fov_down
        self.fov_up = self.proj_fov_up / 180.0 * np.pi
        self.fov_down = self.proj_fov_down / 180.0 * np.pi

    def forward(self, image):
        h, w = image[:,:,3].shape
        points = []

        for i in range(h):
            for j in range(w):
                pitch = (1 - i/h) * (self.fov_up + abs(self.fov_down)) - abs(self.fov_down)
                yaw = ((j / w) * np.pi * 2) - np.pi
                depth = image[:,:,3][i][j]

                x = depth * np.cos(yaw)
                y = -depth * np.sin(yaw)
                z = depth * np.sin(pitch)
                points.append([x, y, z])

        points = np.array(points)
        return torch.tensor(points, dtype=torch.float32)

# %% ../nbs/05_2024infufg.ipynb 41
class SemanticSegmentationSimLDM(LightningDataModule):
    "Lightning DataModule to facilitate reproducibility of experiments."
    def __init__(self, 
                 proj_style='spherical',
                 proj_kargs={'fov_up_deg': 15.,'fov_down_deg': -15., 'W': 440, 'H': 16,},
                 train_batch_size=8, 
                 eval_batch_size=16,
                 num_workers=8
                ):
        super().__init__()

        proj_class = {
        #'unfold': UnfoldingProjection,
        'spherical': SphericalProjection
        }
        assert proj_style in proj_class.keys()
        self.proj = proj_class[proj_style](**proj_kargs)
        self.train_batch_size = train_batch_size
        self.eval_batch_size = eval_batch_size
        self.num_workers = num_workers
    
    def setup(self, stage: str):
        data_path = '/workspace/data'
        tfms = v2.Compose([
            ProjectionSimTransform(self.proj),
            ProjectionToTensorTransformSim(),
        ])
        split = stage
        if stage == 'fit':
            split = 'train'
        else:
            split = 'test'
        
        ds = UFGSimDataset(data_path, split, transform=tfms)
        if not hasattr(self, 'viz_tfm'):
            self.viz_tfm = ProjectionSimVizTransform(ds.color_map_rgb_np, ds.learning_map_inv_np)
        
        if stage == "fit":
            self.ds_train = ds
            self.ds_val = UFGSimDataset(data_path, 'valid', tfms)
        
        if stage == "test":
            self.ds_test = ds
        if stage == "predict":
            self.ds_predict = ds
            

    def train_dataloader(self):
        return DataLoader(self.ds_train, batch_size=self.train_batch_size, num_workers=self.num_workers, shuffle=True, drop_last=True)

    def val_dataloader(self):
        return DataLoader(self.ds_val, batch_size=self.eval_batch_size, num_workers=self.num_workers)

    def test_dataloader(self):
        return DataLoader(self.ds_test, batch_size=self.eval_batch_size, num_workers=self.num_workers)

    def predict_dataloader(self):
        return DataLoader(self.ds_predict, batch_size=self.eval_batch_size, num_workers=self.num_workers)