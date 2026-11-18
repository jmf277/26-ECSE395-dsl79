# For a retest farther away: distribution (not only best) of the same placements as e_n_sweep at 40-78 model px (D1's picture from its width
# in set-up session 08, about 9 in from the lens, down to about half, about 18 in), four pictures, score on the print only. Model responses, no board.
import sys,glob; sys.path.insert(0,'.')
import numpy as np
from PIL import Image
from exp_util import *
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
d1=Image.open(S08+'.jpg').convert('RGB').crop((120,60,231,151))
f6=Image.open(stem(6)+'.jpg').convert('RGB').crop((50,12,262,178))
d2=Image.open('results/d2_clean.png').convert('RGB')
n1=Image.open(stem(32)+'.jpg').convert('RGB').crop((30,20,280,170))
p=lambda v:round(float(1/(1+np.exp(-v))),3)
for pic,name in ((d1,'D1 dim (s08 crop)'),(f6,'D1 lit (f6 crop)'),(d2,'D2 unlit (phone)'),(n1,'dog lit (f32 crop)')):
    w0,h0=pic.size; hist=[]
    for W in (40,44,48,52,56,60,64,72,78):
        H=int(round(W*h0/w0))
        r0=pic.resize((W,H),Image.BOX if W<w0*0.7 else Image.BICUBIC)
        for gain in (0.6,0.8,1.0,1.3):
            r=np.clip(np.asarray(r0).astype(float)*gain,0,255).astype(np.uint8)
            for bg in (10,40,80,114,160,200):
                for fx,fy in ((.5,.5),(.25,.6),(.75,.6),(.5,.75),(.3,.8),(.7,.8),(.5,.35)):
                    a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=bg
                    l=int((224-W)*fx); t=28+int((168-H)*fy)
                    a[t:t+H,l:l+W]=r
                    o=forward(to_i8(a)); q=o['score0'][...,0]*0.25
                    ys,xs=np.mgrid[0:28,0:28]; cx=xs*8+4; cy=ys*8+4
                    ins=(cx>=l)&(cx<l+W)&(cy>=t)&(cy<t+H)
                    hist.append(float(q[ins].max()) if ins.any() else -99)
    h=np.array(hist)
    print(name,'runs',len(h),'percentiles 10,25,50,75,90,100 of score on the print:',[p(np.percentile(h,q)) for q in (10,25,50,75,90,100)],'share>=0.3',round(float((h>=np.log(.3/.7)).mean()),3),'share>=0.0953',round(float((h>=-2.25).mean()),3))
