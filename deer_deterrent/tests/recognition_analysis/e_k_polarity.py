# Bright-on-dark versus dark-on-bright, over many sizes and positions (lower two thirds of the frame), soft ellipses.
import sys; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
def ell(bg,fg,w,h,cx,cy,soft=0.6):
    yy,xx=np.mgrid[0:224,0:224].astype(float)
    r=np.sqrt(((xx-cx)/(w/2.0))**2+((yy-cy)/(h/2.0))**2)
    a=np.clip((1+soft-r)/soft,0,1); im=bg+(fg-bg)*a
    rgb=np.stack([im,im,im],-1); rgb[:28]=114; rgb[196:]=114
    return np.clip(rgb,0,255).astype(np.uint8)
sizes=[(14,7),(16,8),(20,10),(24,12),(28,14),(32,16),(14,14),(20,20),(28,28),(36,18)]
pos=[(60,110),(112,110),(170,110),(60,150),(112,150),(170,150),(60,180),(112,180),(170,180)]
def stat(bg,fg):
    v=[]
    for w,h in sizes:
        for cx,cy in pos:
            o=forward(to_i8(ell(bg,fg,w,h,cx,cy))); v.append(float(o['score0'].max())*0.25)
    v=np.array(v); return round(float(v.mean()),3),round(float(np.median(v)),3),round(float(v.max()),2),round(float((v>=-1.5).mean()),3)
print('pair (field, blob) | mean logit, median, max logit, share of 90 placements scoring >= 0.18')
for bg,fg in ((10,255),(40,255),(60,220),(114,255),(114,10),(200,10),(245,10),(150,60),(60,150)):
    print((bg,fg),stat(bg,fg))
