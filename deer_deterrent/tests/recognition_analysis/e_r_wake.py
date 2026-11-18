# Can the two coarser heads be woken at all?  Search over blocky 7x7 color pictures for the highest response of each head.
import sys; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
rng=np.random.default_rng(395)
levels=np.array([0,64,128,192,255],dtype=float)
def render(g):
    im=np.repeat(np.repeat(g,24,axis=0),32,axis=1)  # 7 x 7 tiles -> 168 x 224
    a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=im.astype(np.uint8)
    return a
for head in (2,1):
    best_all=-99
    for start in range(2):
        g=rng.choice(levels,size=(7,7,3))
        f=lambda gg: float(forward(to_i8(render(gg)))[f'score{head}'].max())*2.0**model().exp[f'score{head}']
        cur=f(g)
        for it in range(2):
            for i in rng.permutation(7):
                for j in rng.permutation(7):
                    for c in range(3):
                        old=g[i,j,c]
                        for v in levels:
                            if v==old: continue
                            g[i,j,c]=v; val=f(g)
                            if val>cur: cur=val; old=v
                        g[i,j,c]=old
            print('head',head,'start',start,'pass',it,'best logit',cur,'prob',round(1/(1+np.exp(-cur)),6),flush=True)
        best_all=max(best_all,cur)
    print('head',head,'best logit found',best_all,'prob',round(1/(1+np.exp(-best_all)),6))
