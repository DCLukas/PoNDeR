import pkg_resources
import argparse
import os
import numpy as np
import torch
import sys
import h5py
import random

from deeprank.features import AtomicFeature
from deeprank.tools import StructureSimilarity

'''
x, y, z   -> Coordinates
occ       -> Occupancy
temp      -> Temperature factor (uncertainty)
eps       -> 
sig       -> Sigma
charge    ->
'''

# Parser
parser = argparse.ArgumentParser()
parser.add_argument('--root_dir', type=str, default='/home/lukas/DR_DATA/', help='Path to data')
parser.add_argument('--decoy_dir', type=str, default='decoys/', help='Relative path to decoys')
parser.add_argument('--decoy_subdir', type=str, default='', help='Subfolder within specific decoy folder (e.g. water/)')
parser.add_argument('--native_dir', type=str, default='natives/', help='Relative path to natives')
parser.add_argument('--dual', dest='dual', default=False, action='store_true',help='Store pointclouds of different proteins separately')
parser.add_argument('--filename', type=str, default='', help='Name of HDF5 file')
parser.add_argument('--pairs', dest='pairs', default=False, action='store_true', help='Store rows of atom pairs instead of single atoms')
parser.add_argument('--full_cloud', dest='full_cloud', default=False, action='store_true', help='Store full clouds instead of contact atoms')
parser.add_argument('--minimal', dest='minimal', default=False, action='store_true', help='One per folder, merely for testing')
arg = parser.parse_args()

# Check for incompatibilities
if arg.dual and arg.pairs:
    raise AttributeError('Dual and pair options are incomplatible')

if arg.full_cloud and arg.pairs: 
    raise AttributeError('Pairs can not be stored when storing full clouds')

# Force field provided with deeprank
FF = pkg_resources.resource_filename('deeprank.features', '') + '/forcefield/'
param_charge = FF + 'protein-allhdg5-4_new.top'
param_vdw = FF + 'protein-allhdg5-4_new.param'
patch_file = FF + 'patch.top'

# Prepare HDF5 file & groups
if arg.filename == '':
    if arg.dual:
        filename = 'dualPointclouds.h5'
    else:
        filename = 'pointclouds.h5'
else:
    filename = arg.filename

hf = h5py.File(filename, 'w')

g_test = hf.create_group('test')
g_train = hf.create_group('train')
g_holdout = hf.create_group('holdout')

# Feature width
if arg.dual:
    hf.attrs['feat_width'] = 8
elif arg.full_cloud:
    hf.attrs['feat_width'] = 6
elif arg.pairs:
    hf.attrs['feat_width'] = 17
else:
    hf.attrs['feat_width'] = 16

# Random distribution of protein pairs among groups
random.seed(1) # Deterministic data split

def getGroup(native_name):
    rand = random.random()
    if rand < 0.75:
        group = g_train
    elif rand < 0.875:
        group = g_test
    else:
        group = g_holdout
    return group

# Concatenate (dual -> single format)
def zeroPadConcat(pcA, pcB):
    pcA = np.c_[pcA, np.zeros_like(pcA)] # Pad right
    pcB = np.c_[np.zeros_like(pcB), pcB] # Pad left
    return np.r_[pcA, pcB]

# Extract contact atoms in dual format
def getDual(atFeat):
    indA, indB = atFeat.sqldb.get_contact_atoms(cutoff=7) # Get contact atoms

    if len(indA)==0: # If no contact atoms found
        return None, None
    else:
        pcA = np.array(atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ', rowID=indA)).astype(np.float32)
        pcB = np.array(atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ', rowID=indB)).astype(np.float32)
    return pcA, pcB

# Extract full point cloud in dual format
def getFull(atFeat):
    pcA = np.array(atFeat.sqldb.get('x,y,z,charge,temp,occ', chainID='A')).astype(np.float32)
    pcB = np.array(atFeat.sqldb.get('x,y,z,charge,temp,occ', chainID='B')).astype(np.float32)
    return pcA, pcB

# Extract contact atoms in single format
def getSingle(atFeat):
    indA, indB = atFeat.sqldb.get_contact_atoms(cutoff=7) # Get contact atoms

    if len(indA)==0: # If no contact atoms found
        return None
    else:
        pcA = np.array(atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ', rowID=indA)).astype(np.float32)
        pcB = np.array(atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ', rowID=indB)).astype(np.float32)
        pcA = np.c_[pcA, np.zeros_like(pcA)] # Pad right
        pcB = np.c_[np.zeros_like(pcB), pcB] # Pad left
        pc  = np.r_[pcA, pcB]
    return pc

# Extract contact atom pairs in single format
def getPairs(atFeat):
    index = atFeat.sqldb.get_contact_atoms(return_contact_pairs=True, cutoff=7) # Get contact atoms
    pc_pairs = []

    if index: # If not empty
        for key,val in index.items():
            pc1 = atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ',rowID=key)[0]
            pc2 = atFeat.sqldb.get('x,y,z,eps,sig,charge,temp,occ',rowID=val)
            a = np.array(pc1[0:3], dtype=np.float32)
            
            for p in pc2:
                b = np.array(p[0:3], dtype=np.float32)
                dist = np.linalg.norm(a-b) # Euclidian distance
                pc_pairs.append(pc1+p+[dist])
        
        pc = np.vstack(pc_pairs).astype(np.float32) # List of atom pair parameters to array
    else: # If no contact atoms found
        return None
    return pc

# Calculate scoring metrics
def getMetrics(sim):
    irmsd = sim.compute_irmsd_fast(method='svd')
    lrmsd = sim.compute_lrmsd_fast(method='svd')
    fnat = sim.compute_Fnat_fast()
    dockQ = sim.compute_DockQScore(fnat,lrmsd,irmsd)
    return irmsd, lrmsd, fnat, dockQ

# Start converting
for native_name in sorted(os.listdir(arg.root_dir+arg.native_dir)):
    decoy_dir = arg.root_dir+arg.decoy_dir+native_name[:4]+'/'+arg.decoy_subdir
    if os.path.isdir(decoy_dir) and native_name.endswith(".pdb"):
        group = getGroup(native_name)
        print('Putting', native_name[:4], 'in', group.name)
        for decoy_name in sorted(os.listdir(decoy_dir)):
            # Declare the feature calculator instance
            atFeat = AtomicFeature(decoy_dir+'/'+decoy_name, param_charge=param_charge, param_vdw=param_vdw, patch_file=patch_file)
            atFeat.assign_parameters() # Assign parameters
            if not arg.full_cloud:
                atFeat.evaluate_pair_interaction() # Compute the pair interactions
            sim = StructureSimilarity(decoy_dir+'/'+decoy_name, arg.root_dir+arg.native_dir+native_name)

            # Dual types
            if arg.dual or arg.full_cloud: 
                if arg.dual: 
                    pcA, pcB = getDual(atFeat)
                elif arg.full_cloud:
                    pcA, pcB = getFull(atFeat)

                if pcA is not None:
                    irmsd, lrmsd, fnat, dockQ = getMetrics(sim)
                    subgroup = group.create_group(decoy_name[:-4])
                    dsA = subgroup.create_dataset('A', data = pcA)
                    dsB = subgroup.create_dataset('B', data = pcB)
                    subgroup.attrs['irmsd'] = irmsd
                    subgroup.attrs['lrmsd'] = lrmsd
                    subgroup.attrs['fnat']  = fnat
                    subgroup.attrs['dockQ'] = dockQ
                else:
                    print('    ',decoy_name[:-4], 'did not contain contact atoms')
                    break

            # Non-dual types
            else: 
                if arg.pairs: # Pairwise extraction
                    pc = getPairs(atFeat)
                else:
                    pc = getSingle(atFeat)
                
                if pc is not None: 
                    irmsd, lrmsd, fnat, dockQ = getMetrics(sim)
                    ds = group.create_dataset(decoy_name[:-4], data = pc)
                    ds.attrs['irmsd'] = irmsd
                    ds.attrs['lrmsd'] = lrmsd
                    ds.attrs['fnat']  = fnat
                    ds.attrs['dockQ'] = dockQ
                else:
                    print('    ',decoy_name[:-4], 'did not contain contact atoms')
                    break

            print('    ',decoy_name[:-4], 'done')
            if arg.minimal: # For testing this script, only one per loop
                break
    else:
        print(decoy_dir, 'not found')
hf.close()