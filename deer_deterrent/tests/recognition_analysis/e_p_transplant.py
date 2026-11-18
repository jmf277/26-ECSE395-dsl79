# Why does the same ceiling light score 0.5 in the empty room, up to 0.32 beside D1, nothing beside D3?
# Swap the light's own patch between frames and see whether the score follows the patch or the rest of the picture.
import sys,json; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
def cam2in(x,y): return int(x*0.7),int(y*0.7+28)
BOX=(205,200,296,240)
x0,y0=cam2in(BOX[0],BOX[1]); x1,y1=cam2in(BOX[2],BOX[3]); y1=196
frames={'D1 f6':6,'D2 f18':18,'D3 f22':22,'D3 f25':25,'N1 f32':32,'empty f56':56,'empty f68':68,'D1 f60':60}
inp={k:load_input(stem(v)) for k,v in frames.items()}
print('light patch itself: mean int8 value R,G,B; count of pixels with all channels >=120 (near white); and its score cell logit in place')
for k,x in inp.items():
    p=x[y0:y1,x0:x1].astype(int)
    white=int((p.min(axis=2)>=120).sum())
    o=forward(x); q=o['score0'][...,0]*0.25
    print(k,'patch mean',p.reshape(-1,3).mean(0).round(1),'near-white px',white,'best logit in patch cells',float(q[(y0//8):(y1//8)+1,(x0//8):(x1//8)+1].max()),'whole-frame score',round(score(o),3))
print('\ncolumns: rest of picture from frame A, light patch from frame B -> best logit among the patch cells')
names=list(frames)
print(' '*10,*[n[:9].rjust(10) for n in names])
for a in names:
    row=[]
    for b in names:
        c=inp[a].copy(); c[y0:y1,x0:x1]=inp[b][y0:y1,x0:x1]
        o=forward(c); q=o['score0'][...,0]*0.25
        row.append(float(q[(y0//8):(y1//8)+1,(x0//8):(x1//8)+1].max()))
    print(a[:10].ljust(10),*[str(v).rjust(10) for v in row])
print('\nsame swap but the rest of the picture flattened to its own mean color outside the patch (no context at all):')
for b in names:
    m=inp[b].reshape(-1,3).mean(0).round().astype(np.int8)
    c=np.tile(m,(224,224,1)).astype(np.int8); c[:28]=57; c[196:]=57
    c[y0:y1,x0:x1]=inp[b][y0:y1,x0:x1]
    o=forward(c); q=o['score0'][...,0]*0.25
    print(b,'best logit',float(q[(y0//8):(y1//8)+1,(x0//8):(x1//8)+1].max()))
print('\nhow far away can the rest of the picture reach?  Blank growing rings around the light in f56 (flat room mean):')
x=inp['empty f56']; m=x.reshape(-1,3).mean(0).round().astype(np.int8)
ycell,xcell=22,21
for keep in (224,160,120,96,80,64,48,40):
    c=np.tile(m,(224,224,1)).astype(np.int8); c[:28]=57; c[196:]=57
    cx,cy=xcell*8+4,ycell*8+4
    l=max(cx-keep//2,0); r=min(cx+keep//2,224); t=max(cy-keep//2,28); b=min(cy+keep//2,196)
    c[t:b,l:r]=x[t:b,l:r]
    o=forward(c); print('window',keep,'px kept around the light: logit at light cell',float(o['score0'][...,0][ycell,xcell]*0.25),'whole score',round(score(o),3))
