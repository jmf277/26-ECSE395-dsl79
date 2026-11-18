# How much could the rounding of the WEIGHTS matter?  Stats, parameter count, and a noise test:
# add random error of the same size as weight rounding (uniform +-0.5 of one step) to every weight, decimal arithmetic.
import sys,json; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
from e_g_float_lib import run_float
m=model()
tot=0;zeros=0;rows=[]
for nd in m.nodes:
    if nd['op']=='Conv':
        w=nd['w']; n=w.size; tot+=n; z=int((w==0).sum()); zeros+=z
        rows.append((nd['name'],w.shape,m.init[nd['ins'][1]]['exp'],int(np.abs(w).max()),round(float(np.abs(w).mean()),1),round(z/n,3)))
bias=sum(len(nd['bias']) for nd in m.nodes if nd['op']=='Conv' and nd['bias'] is not None)
print('weights',tot,'biases',bias,'parameters',tot+bias,'; weights exactly zero',zeros,round(zeros/tot,4))
print('mean |weight| in steps: median over layers',np.median([r[4] for r in rows]),' largest |weight| by layer: min',min(r[3] for r in rows),'median',np.median([r[3] for r in rows]))
print('layers with smallest mean |w|:',sorted(rows,key=lambda r:r[4])[:6])
# noise test
frames=[6,18,22,32,50,56,62]
rng=np.random.default_rng(395)
base={}
for fr in frames:
    F=run_float(load_input(stem(fr)))
    base[fr]=[float(F[f'score{s}'].max()) for s in range(3)]
print('frame: decimal-arithmetic max logit per head',base)
orig={id(nd):nd['w'].copy() for nd in m.nodes if nd['op']=='Conv'}
res={fr:[] for fr in frames}
for trial in range(5):
    for nd in m.nodes:
        if nd['op']=='Conv':
            nd['w']=orig[id(nd)]+rng.uniform(-0.5,0.5,size=orig[id(nd)].shape)
    for fr in frames:
        F=run_float(load_input(stem(fr)))
        res[fr].append([float(F[f'score{s}'].max()) for s in range(3)])
for nd in m.nodes:
    if nd['op']=='Conv': nd['w']=orig[id(nd)]
for fr in frames:
    a=np.array(res[fr]); print(fr,'head0 base',round(base[fr][0],2),'noisy mean',round(a[:,0].mean(),2),'spread',round(a[:,0].std(),2),'| head1',round(base[fr][1],2),round(a[:,1].mean(),2),'| head2',round(base[fr][2],2),round(a[:,2].mean(),2))
