# Plain soft ellipses (as in e_k_polarity.py) from 16 to 160 model px wide at its 9 placements: does the size effect seen at the
# center (e_c_synth.py) hold away from it?  The best fine-layer score over the placements and three field/blob pairs, with where it is.
import sys; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
def ell(bg,fg,w,h,cx,cy,soft=0.6):
    yy,xx=np.mgrid[0:224,0:224].astype(float)
    r=np.sqrt(((xx-cx)/(w/2.0))**2+((yy-cy)/(h/2.0))**2)
    a=np.clip((1+soft-r)/soft,0,1); im=bg+(fg-bg)*a
    rgb=np.stack([im,im,im],-1); rgb[:28]=114; rgb[196:]=114
    return np.clip(rgb,0,255).astype(np.uint8)
pos=[(60,110),(112,110),(170,110),(60,150),(112,150),(170,150),(60,180),(112,180),(170,180)]
print('w h | best head0 prob over 9 placements and 3 pairs (field,blob)=(10,255),(40,255),(114,255) | best head1 | best head2')
for w,h in ((16,8),(24,12),(32,16),(40,20),(48,24),(56,28),(64,32),(80,40),(96,48),(112,56),(128,64),(160,80)):
    b0=b1=b2=-99; where=None
    for bg,fg in ((10,255),(40,255),(114,255)):
        for cx,cy in pos:
            o=forward(to_i8(ell(bg,fg,w,h,cx,cy)))
            v0=float(o['score0'].max())*.25; v1=float(o['score1'].max())*.5; v2=float(o['score2'].max())*.5
            if v0>b0: b0=v0; where=(bg,cx,cy)
            b1=max(b1,v1); b2=max(b2,v2)
    p=lambda v:round(float(1/(1+np.exp(-v))),6)
    print(w,h,'|',p(b0),where,'|',p(b1),'|',p(b2))
