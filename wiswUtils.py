import torch
import numpy as np
#import matplotlib.pyplot as plt
#import seaborn as sns
import os
import csv
import sys
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
from time import time
import os
import math
from HardNet import HardNet
from PIL import Image
import pandas as pd

def greedy_iterative_snns_slow(dmv1, do_mutual=False):
    dmv = dmv1.detach().clone().cpu()
    MAXVAL = dmv1.max() +100
    h,w = dmv.size()
    min_size = min(h,w)
    out = torch.zeros(min_size,3)
    min_dist_r2, idxs_in_1 = torch.min(dmv, 0)
    min_dist_c2, idxs_in_2 = torch.min(dmv, 1)    
    idxs_c_in1 = torch.arange(h)
    idxs_r_in2 = torch.arange(w)
    #vals1 = []
    if h < w:
        mutual_mask = idxs_in_1[idxs_in_2[:]] == idxs_c_in1#.cuda()
        for i in range(h):
            #dmv = dmv.double()
            #min_dist_r2, idxs_in_1 = torch.min(dmv, 0)
            min_dist_c2, idxs_in_2 = torch.min(dmv, 1)
            if do_mutual:
                min_dist_c2 = min_dist_c2.view(-1) - 1000*mutual_mask.float()
                #That is a hack for first having mutual neighbors, and then the rest
            val, min_dist_c2_idx = torch.min(min_dist_c2.view(-1),0)
            assert val == dmv.min()
            y = min_dist_c2_idx.item()
            row = dmv[y,:].clone()
            val1, x = torch.min(row.view(-1),0)
            col = dmv[:,x].clone()
            dmv[y,x] = MAXVAL
            col[y]=MAXVAL
            row[x]=MAXVAL
            out[i,0] = y#idx in img1
            out[i,1] = x#idx in img2
            out[i,2] = -(row.min()+ col.min())/ (2.0*val.float())#iSSN ratio
            dmv[:,x] = MAXVAL
            dmv[y,:] = MAXVAL
    else:
        mutual_mask = idxs_in_2[idxs_in_1[:]] == idxs_r_in2#.cuda()
        #dmv = dmv.double()
        for i in range(w):
            min_dist_r2, idxs_in_1 = torch.min(dmv, 0)
            #min_dist_c2, idxs_in_2 = torch.min(dmv, 1)    
            if do_mutual:
                min_dist_r2 = min_dist_r2.view(-1)-1000*mutual_mask.float()
                #That is a hack for first having mutual neighbors, and then the rest
            #print (min_dist_r2.view(-1).topk(2,0, False)[0])
            val, min_dist_r2_idx = torch.min(min_dist_r2.view(-1).float(),0)
            #vals1.append(val.item())
            x = min_dist_r2_idx.item()
            col = dmv[:,x].clone()
            val1, y = torch.min(col.view(-1),0)
            row = dmv[y,:].clone()
            #col[col<val.float()] = MAXVAL
            #row[row<val.float()] = MAXVAL
            col[y]=MAXVAL
            row[x]=MAXVAL
            dmv[y,x] = MAXVAL
            out[i,0] = y#idx in img1
            out[i,1] = x#idx in img2
            out[i,2] = -(row.min()+ col.min())/(2.0*val)#iSSN ratio
            #print (row.min().item(), col.min().item())
            dmv[:,x] = MAXVAL
            dmv[y,:] = MAXVAL
    out[i,2] = -1   
    out[:,0] += 1
    out[:,1] += 1
    vals, idxs = torch.sort(out[:,2])
    out2 = out[idxs].detach().cpu()
    return out2.float().cpu()

def read_circle_patches(fname, rot_ang = 0):
    #patches = np.loadtxt(fname, delimiter=',') #24 sec to read
    patches = pd.read_csv(fname,header=None, sep =',', dtype=np.uint8).values #6 sec to read. Still huge :(
    num, area = patches.shape
    PS = int(np.sqrt(area))
    assert PS == 137
    patches = np.reshape(patches, (num,1, PS, PS))
    return patches

def crop_round_patches(circle_patches, cropsize=97):
    num,ch,h,w = circle_patches.shape
    assert h == 137
    assert w == 137
    PS = h
    PS_crop = cropsize
    pad = (PS - PS_crop)//2
    crop_patches = circle_patches[:,:,pad:pad+PS_crop,pad:pad+PS_crop]
    return crop_patches

def rotate_circle_patches(cp, rot_angles):
    ropatches = np.ndarray(cp.shape, dtype=np.uint8)
    for i in range(len(cp)):
        ropatches[i,0,:,:] = np.array(Image.fromarray(cp[i,0,:,:]).rotate(-rot_angles[i], resample=Image.BILINEAR))
    return ropatches

def resize_patches(rp, PS=32):
    num,ch,h,w = rp.shape
    out_patches = np.ndarray((num,ch, PS,PS), dtype=np.uint8)
    for i in range(len(rp)):
        out_patches[i,0,:,:] = np.array(Image.fromarray(rp[i,0,:,:]).resize((PS,PS), resample=Image.LANCZOS))
    return out_patches

def describe_with_default_ori(fname, model):
    model = model.cuda()
    cp = read_circle_patches(fname)
    angles = np.loadtxt(fname.replace('big_patches', 'ori'))
    cp_rot = rotate_circle_patches(cp,angles)
    rp_rot = crop_round_patches(cp_rot)
    out_patches = resize_patches(rp_rot).astype(np.float32)
    n_patches = len(out_patches)
    bs = 128
    outs = []
    n_batches = int(n_patches / bs) + 1
    descriptors_for_net = np.zeros((n_patches, 128))
    for i in range(0, n_patches, bs):
        data_a = out_patches[i: i + bs, :, :, :]
        data_a = torch.from_numpy(data_a).cuda()
        with torch.no_grad():
            out_a = model(data_a)
        descriptors_for_net[i: i + bs,:] = out_a.data.cpu().numpy().reshape(-1, 128)
    descriptors_for_net = descriptors_for_net + 0.45;
    out = (descriptors_for_net * 210.).astype(np.int32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    return out

def match_fname(pair, descdir, descname, matchdir):
    fn1 = os.path.join(descdir, pair[0]+'_'+descname + '.csv')
    fn2 = os.path.join(descdir, pair[1]+'_'+descname + '.csv')
    mfn = os.path.join(matchdir, pair[0]+'_'+pair[1]+'_'+descname + '.csv')
    return fn1, fn2, mfn

def distance_matrix_vector(anchor, positive):
    """Given batch of anchor descriptors and positive descriptors calculate distance matrix"""
    d1_sq = torch.sum(anchor * anchor, dim=1).unsqueeze(-1)
    d2_sq = torch.sum(positive * positive, dim=1).unsqueeze(-1)
    eps = 1e-5
    return torch.sqrt(torch.abs((d1_sq.repeat(1, positive.size(0)) + torch.t(d2_sq.repeat(1, anchor.size(0)))
                    - 2.0 * torch.bmm(anchor.unsqueeze(0), torch.t(positive).unsqueeze(0)).squeeze(0)))+eps)
