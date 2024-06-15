# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/03_Geiger2012CVPR.ipynb.

# %% auto 0
__all__ = ['ObjectKITTIDataset', 'BEVProjection']

# %% ../nbs/03_Geiger2012CVPR.ipynb 2
import torch
from PIL import Image
from torch.utils.data import Dataset, DataLoader, random_split
from torch import nn
import yaml
from pathlib import Path
import numpy as np
from lightning import LightningDataModule
from torchvision.transforms import v2

# %% ../nbs/03_Geiger2012CVPR.ipynb 3
class ObjectKITTIDataset(Dataset):
    "load the objectKITTI data in a pytorch Dataset object."
    def __init__(self, data_path, is_train=True, transform=None):
        data_path = Path(data_path)
        self.object_velodyne_path = data_path/'data_object_velodyne'
        self.object_labels_path = data_path/'data_object_label_2'

        object_velodyne_fns = []
        if is_train:
            query = '*training/velodyne/*.bin'
            object_velodyne_fns += list(self.object_velodyne_path.rglob(query))
            self.instance = 'training'
        else:
            query = '*testing/velodyne/*.bin'
            object_velodyne_fns += list(self.object_velodyne_path.rglob(query))
            self.instance = 'testing'
            
        self.object_frame_ids = [fn.stem for fn in object_velodyne_fns]

    def set_transform(self, transform):
        self.transform = transform
        
    def __len__(self):
        return len(self.object_frame_ids)

    def __getitem__(self, idx):
        object_frame_id = self.object_frame_ids[idx]
        object_frame_instance = self.instance
        
        object_frame_path = self.object_velodyne_path/object_frame_instance/'velodyne'/(object_frame_id + '.bin')
        with open(object_frame_path, 'rb') as f:
            object_frame = np.fromfile(f, dtype=np.float32).reshape(-1, 4)

        #TODO: label part

        return object_frame
        

# %% ../nbs/03_Geiger2012CVPR.ipynb 9
class BEVProjection:
    def __init__(self, res=0.2, side_range=(-102.3, 102.3), fwd_range=(-102.3, 102.3), height_range=(-2, 2) ):
        self.res = res
        self.side_range = side_range
        self.fwd_range = fwd_range
        self.height_range = height_range

    def scale_to_255(self, pixel_values, min, max, dtype=np.uint8):
        return (((pixel_values - min) / float(max - min)) * 255).astype(dtype)

    def get_BEV_projection(self, point_cloud, label=False):
        x_coord = point_cloud[:, 0]
        y_coord = point_cloud[:, 1]
        z_coord = point_cloud[:, 2]
        intensity = point_cloud[:, 3]
        
        # Three filters for idx points: Front-to-back, side-to-side, and height ranges
        f2b_filter = np.logical_and((x_coord > self.fwd_range[0]), (x_coord < self.fwd_range[1]))
        s2s_filter = np.logical_and((y_coord > -self.side_range[1]), (y_coord < -self.side_range[0]))
        filter = np.logical_and(f2b_filter, s2s_filter)
        idx = np.argwhere(filter).flatten()

        x_coord = x_coord[idx]
        y_coord = y_coord[idx]
        z_coord = z_coord[idx]
        intensity = intensity[idx]

        # Pixel positions based on resolution
        x_bev = (-y_coord / self.res).astype(np.int32)
        y_bev = (-x_coord / self.res).astype(np.int32)

        # Minimum (0,0) after shift
        x_bev -= int(np.floor(self.side_range[0] / self.res))
        y_bev += int(np.ceil(self.fwd_range[1] / self.res))

        x_bev_max = 2 + int((self.side_range[1] - self.side_range[0]) / self.res)
        y_bev_max = 2 + int((self.fwd_range[1] - self.fwd_range[0]) / self.res)

        if label:
            pixel_values = point_cloud[:, 3:][idx]
            bev_img = np.zeros([y_bev_max, x_bev_max, 3], dtype=np.uint8)
            bev_img[y_bev, x_bev] = pixel_values
        else:
            bev_img_min = np.full((y_bev_max, x_bev_max), np.inf, dtype=np.float32)
            bev_img_max = np.full((y_bev_max, x_bev_max), self.height_range[0], dtype=np.float32)
            bev_img_mean_intensity = np.zeros((y_bev_max, x_bev_max), dtype=np.float32)
            count = np.zeros((y_bev_max, x_bev_max), dtype=np.int32)

            for i in range(len(x_bev)):
                y, x = y_bev[i], x_bev[i]
                z, intensity_val = z_coord[i], intensity[i]

                if z < bev_img_min[y, x]:
                    bev_img_min[y, x] = z

                if z > bev_img_max[y, x]:
                    bev_img_max[y, x] = z

                bev_img_mean_intensity[y, x] += intensity_val
                count[y, x] += 1

            count = np.maximum(count, 1)
            bev_img_mean_intensity /= count

            bev_img_min[bev_img_min == np.inf] = 0
            bev_img_min = self.scale_to_255(bev_img_min, self.height_range[0], self.height_range[1])
            bev_img_max = self.scale_to_255(bev_img_max, self.height_range[0], self.height_range[1])
            bev_img_mean_intensity = self.scale_to_255(bev_img_mean_intensity, np.min(intensity), np.max(intensity))

            bev_img = np.stack((bev_img_min, bev_img_max, bev_img_mean_intensity), axis=-1)

        return bev_img
        
