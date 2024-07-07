# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_behley2019iccv.ipynb.

# %% auto 0
__all__ = ['SemanticKITTIDataset', 'SphericalProjection', 'UnfoldingProjection', 'ProjectionTransform', 'ProjectionVizTransform',
           'plot_projections', 'ProjectionToTensorTransform', 'SemanticSegmentationLDM']

# %% ../nbs/00_behley2019iccv.ipynb 2
import torch
from torch.utils.data import Dataset, DataLoader, random_split
from torch import nn
import yaml
from pathlib import Path
import numpy as np
from lightning import LightningDataModule
from torchvision.transforms import v2

# %% ../nbs/00_behley2019iccv.ipynb 4
class SemanticKITTIDataset(Dataset):
    "Load the SemanticKITTI data in a pytorch Dataset object."
    def __init__(self, data_path, split='train', transform=None):
        data_path = Path(data_path)
        yaml_path = data_path/'semantic-kitti.yaml'
        self.velodyne_path = data_path/'data_odometry_velodyne/dataset/sequences'
        self.labels_path = data_path/'data_odometry_labels/dataset/sequences'

        with open(yaml_path, 'r') as file:
            metadata = yaml.safe_load(file)
        
        sequences = metadata['split'][split]
        velodyne_fns = []
        for seq in sequences:
            velodyne_fns += list(self.velodyne_path.rglob(f'*{seq:02}/velodyne/*.bin'))
        
        self.frame_ids = [fn.stem for fn in velodyne_fns]
        self.frame_sequences = [fn.parts[-3] for fn in velodyne_fns]
        
        self.labels_dict = metadata['labels']
        
        self.learning_map = metadata['learning_map']
        max_key = sorted(self.learning_map.keys())[-1]
        self.learning_map_np = np.zeros((max_key+1,), dtype=int)
        for k, v in self.learning_map.items():
            self.learning_map_np[k] = v
        
        self.learning_map_inv = metadata['learning_map_inv']
        self.learning_map_inv_np = np.zeros((len(self.learning_map_inv),))
        for k, v in self.learning_map_inv.items():
            self.learning_map_inv_np[k] = v
        
        self.color_map_bgr = metadata['color_map']
        max_key = sorted(self.color_map_bgr.keys())[-1]
        self.color_map_rgb_np = np.zeros((max_key+1, 3))
        for k, v in self.color_map_bgr.items():
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
    
    def set_transform(self, transform):
        self.transform = transform
    
    def __len__(self):
        return len(self.frame_ids)

    def __getitem__(self, idx):
        frame_id = self.frame_ids[idx]
        frame_sequence = self.frame_sequences[idx]
        
        frame_path = self.velodyne_path/frame_sequence/'velodyne'/(frame_id + '.bin')
        with open(frame_path, 'rb') as f:
            frame = np.fromfile(f, dtype=np.float32).reshape(-1, 4)
        
        label = None
        mask = None
        if not self.is_test:
            label_path = self.labels_path/frame_sequence/'labels'/(frame_id + '.label')
            with open(label_path, 'rb') as f:
                label = np.fromfile(f, dtype=np.uint32)
                label = label & 0xFFFF
            label = self.learning_map_np[label]
            mask = label != 0   # see the field *learning_ignore* in the yaml file
        
        if self.transform:
            frame, label, mask = self.transform(frame, label, mask)
        
        return frame, label, mask

# %% ../nbs/00_behley2019iccv.ipynb 12
class SphericalProjection:
    "Calculate yaw and pitch angles for each point and quantize these angles into image grid."
    def __init__(self, fov_up_deg, fov_down_deg, W, H):
        self.fov_up_rad = (fov_up_deg/180.)*np.pi
        self.fov_down_rad = (fov_down_deg/180.)*np.pi
        self.fov_rad = self.fov_up_rad - self.fov_down_rad
        self.W = W
        self.H = H
    
    def get_xy_projections(self, scan_xyz, depth):
        # get angles of all points
        yaw = np.arctan2(scan_xyz[:,1], scan_xyz[:,0])
        pitch = np.arcsin(scan_xyz[:,2] / depth)
        
        # get projections in image coords (between [0.0, 1.0])
        proj_x = 0.5*(1. + yaw/np.pi)
        proj_y = (self.fov_up_rad - pitch)/self.fov_rad

        # just making sure nothing wierd happened with the np.arctan2 function
        assert proj_x.min() >= 0.
        assert proj_x.max() <= 1.
        # filter points outside the fov as outliers
        outliers = (proj_y < 0.)|(proj_y >= 1.)
        
        # scale to image size using angular resolution (between [0.0, W/H])
        proj_x *= self.W
        proj_y *= self.H
        
        # round and clamp to use as indices (between [0, W/H - 1])
        proj_x = np.floor(proj_x)
        proj_x = np.clip(proj_x, 0, self.W - 1).astype(int)
        
        proj_y = np.floor(proj_y)
        proj_y = np.clip(proj_y, 0, self.H - 1).astype(int)
        
        return proj_x, proj_y, outliers

# %% ../nbs/00_behley2019iccv.ipynb 14
class UnfoldingProjection:
    "Assume the points are sorted in increasing yaw order and line number."
    def __init__(self, W, H):
        self.W = W
        self.H = H
    
    def get_xy_projections(self, scan_xyz, depth):
        # get yaw angles of all points
        yaw = np.arctan2(scan_xyz[:,1], scan_xyz[:,0])
        
        # rectify yaw value to be between ]0, 2*pi[
        yaw[yaw < 0] += 2.*np.pi
        
        # scale to image size
        proj_x  = np.floor(self.W*0.5*yaw/np.pi).astype(int)
        
        # just making sure nothing wierd happened with the np.arctan2 or the np.floor functions
        assert proj_x.min() >= 0
        assert proj_x.max() < self.W
        
        # find discontinuities ("jumps") from scan completing cycle
        jump = yaw[1:] - yaw[:-1] < -np.pi
        jump = np.concatenate((np.zeros(1), jump))
        
        # every jump indicates a new scan row
        proj_y = jump.cumsum().astype(int)
        
        # for debugging only
        if proj_y.max() > self.H - 1:
            print(proj_y.max())
        assert proj_y.max() <= self.H - 1
        
        return proj_x, proj_y, None

# %% ../nbs/00_behley2019iccv.ipynb 16
from matplotlib import pyplot as plt

# %% ../nbs/00_behley2019iccv.ipynb 21
class ProjectionTransform(nn.Module):
    "Pytorch transform that turns a point cloud frame and its respective label into images in given projection style."
    def __init__(self, projection):
        super().__init__()
        self.projection = projection
        self.W = projection.W
        self.H = projection.H
        
    def forward(self, frame, label, mask):
        # get point_cloud components
        scan_xyz = frame[:,:3]
        reflectance = frame[:, 3]

        assert reflectance.max() <= 1.
        assert reflectance.min() >= 0.

        # get depths of all points
        depth = np.linalg.norm(scan_xyz, 2, axis=1)

        # get projections and outliers
        proj_x, proj_y, outliers = self.projection.get_xy_projections(scan_xyz, depth)

        # filter outliers
        if outliers is not None:
            proj_x = proj_x[~outliers]
            proj_y = proj_y[~outliers]
            scan_xyz = scan_xyz[~outliers]
            reflectance = reflectance[~outliers]
            depth = depth[~outliers]
            if label is not None:
                label = label[~outliers]
                mask = mask[~outliers]
        
        # order in decreasing depth
        order = np.argsort(depth)[::-1]
        info_list = [
            scan_xyz,
            reflectance[..., np.newaxis],
            depth[..., np.newaxis]
        ]
        if label is not None:
            info_list += [mask[..., np.newaxis]]
            info_list += [label[..., np.newaxis]]
            
        scan_info = np.concatenate(info_list, axis=-1)
        scan_info = scan_info[order]
        proj_y = proj_y[order]
        proj_x = proj_x[order]
        
        # setup the image tensor
        projections_img = np.zeros((self.H, self.W, 2+len(info_list)))
        projections_img[:,:,-1] -= 1 # this helps to identify points in the projection with no LiDAR readings
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
        
        return frame_img, label_img, mask_img

# %% ../nbs/00_behley2019iccv.ipynb 28
class ProjectionVizTransform(nn.Module):
    "Pytorch transform to preprocess projection images for proper visualization."
    def __init__(self, color_map_rgb_np, learning_map_inv_np):
        super().__init__()
        self.color_map_rgb_np = color_map_rgb_np
        self.learning_map_inv_np = learning_map_inv_np
    
    def scale(self, img, min_value, max_value):
        assert img.max() <= max_value
        assert img.min() >= min_value
        assert max_value > min_value
        
        img = img.clip(min_value, max_value)
        return (255.*(img - min_value)/(max_value - min_value)).astype(int)
    
    def forward(self, frame_img, label_img, mask_img):
        normalized_frame_img = None
        if frame_img is not None:
            x = self.scale(frame_img[:,:,0], -100., 100.)
            y = self.scale(frame_img[:,:,1], -100., 100.)
            z = self.scale(frame_img[:,:,2], -31., 5.)
            r = self.scale(frame_img[:,:,3], 0., 1.)
            d = self.scale(frame_img[:,:,4], 0., 100.)
            normalized_frame_img = np.stack((x, y, z, r, d), axis=-1)
            normalized_frame_img[mask_img == False] *= 0

        colored_label_img = None
        if label_img is not None:
            label_img[mask_img] = self.learning_map_inv_np[label_img[mask_img]]
            colored_label_img = np.zeros(label_img.shape + (3,))
            colored_label_img[mask_img] = self.color_map_rgb_np[label_img[mask_img]]
            colored_label_img = colored_label_img.astype(int)
        
        return normalized_frame_img, colored_label_img, mask_img

# %% ../nbs/00_behley2019iccv.ipynb 30
def plot_projections(img, label):
    fig, axs = plt.subplots(6, 1, figsize=(20,10), layout='compressed')
    for i, (ax, title) in enumerate(zip(axs, ['x', 'y', 'z', 'r', 'd', 'label'])):
        if i < 5:
            ax.imshow(img[:,:,i])
        else:
            ax.imshow(label)
        ax.set_title(title)
        ax.axis('off')

# %% ../nbs/00_behley2019iccv.ipynb 35
class ProjectionToTensorTransform(nn.Module):
    "Pytorch transform that converts the projections from np.array to torch.tensor. It also changes the frame image format from (H, W, C) to (C, H, W)."
    def forward(self, frame_img, label_img, mask_img):
        frame_img = np.transpose(frame_img, (2, 0, 1))
        frame_img = torch.from_numpy(frame_img).float()
        label_img = torch.from_numpy(label_img)
        mask_img = torch.from_numpy(mask_img)
        return frame_img, label_img, mask_img

# %% ../nbs/00_behley2019iccv.ipynb 52
class SemanticSegmentationLDM(LightningDataModule):
    "Lightning DataModule to facilitate reproducibility of experiments."
    def __init__(self, 
                 proj_style='unfold',
                 proj_kargs={'W': 512, 'H': 64},
                 remapping_rules=None,
                 train_batch_size=8, 
                 eval_batch_size=16,
                 num_workers=8,
                 tfms = None
                ):
        super().__init__()

        proj_class = {
        'unfold': UnfoldingProjection,
        'spherical': SphericalProjection
        }
        assert proj_style in proj_class.keys()
        self.proj = proj_class[proj_style](**proj_kargs)
        self.remapping_rules = remapping_rules
        self.train_batch_size = train_batch_size
        self.eval_batch_size = eval_batch_size
        self.num_workers = num_workers
        self.tfms = tfms
    
    def setup(self, stage: str):
        data_path = '/workspace/data'
        if not self.tfms:
            self.tfms = v2.Compose([
                ProjectionTransform(self.proj),
                ProjectionToTensorTransform(),
            ])
        split = stage
        if stage == 'fit':
            split = 'train'
        ds = SemanticKITTIDataset(data_path, split, transform=self.tfms)
        if self.remapping_rules:
            ds.learning_remap(self.remapping_rules)
        if not hasattr(self, 'viz_tfm'):
            self.viz_tfm = ProjectionVizTransform(ds.color_map_rgb_np, ds.learning_map_inv_np)
        
        if stage == "fit":
            self.ds_train = ds
            self.ds_val = SemanticKITTIDataset(data_path, 'valid', tfms)
            if self.remapping_rules:
                self.ds_val.learning_remap(self.remapping_rules)
        
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
