# Occlusion: replace a small patch at a time with its own mean color; how far does the response of the best cell fall?  Shows where in the
# picture that response comes from (where a lit torso and the capture light's patch coincide, it cannot tell them apart).
import sys,glob,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
items=[('f62 D1 torso box',stem(62),(13,17)),('f21 D3 head box',stem(21),None),('s08 D1 ambient box on body',S08,(14,15)),('f56 ceiling light',stem(56),(22,21))]
tiles=[]
P=14;ST=7
for name,st,cell in items:
    x=load_input(st); o=forward(x); q=o['score0'][...,0]*0.25
    if cell is None:
        t=top(o); cell=t['cell']
    base=q[cell]
    g=np.zeros((168//ST+1,224//ST+1))
    for iy,y in enumerate(range(28,196,ST)):
        for ix,X in enumerate(range(0,224,ST)):
            a=x.copy()
            y1=min(y+P,196); x1=min(X+P,224)
            a[y:y1,X:x1]=np.round(a[y:y1,X:x1].reshape(-1,3).mean(0)).astype(np.int8)  # patch -> its own mean color
            oo=forward(a); g[iy,ix]=oo['score0'][...,0][cell]*0.25
    drop=base-g
    print(name,'cell',cell,'base logit',base,'largest drop',round(float(drop.max()),2),'at',np.unravel_index(drop.argmax(),drop.shape),'cells with drop>=1 logit',int((drop>=1).sum()))
    img=Image.fromarray(from_i8(x).astype(np.uint8))
    heat=np.clip(drop/np.maximum(drop.max(),1e-6),0,1)
    H=np.zeros((224,224,3),dtype=np.uint8)
    for iy,y in enumerate(range(28,196,ST)):
        for ix,X in enumerate(range(0,224,ST)):
            H[y:y+ST,X:X+ST,0]=int(255*heat[iy,ix]); H[y:y+ST,X:X+ST,1]=int(160*heat[iy,ix]**2)
    bl=Image.blend(img,Image.fromarray(H),0.6)
    d=ImageDraw.Draw(bl); cy,cx=cell; d.rectangle([cx*8,cy*8,cx*8+8,cy*8+8],outline=(0,255,0)); d.text((3,3),name,fill=(255,255,255),stroke_width=1,stroke_fill=(0,0,0))
    T=Image.new('RGB',(448,224)); T.paste(img,(0,0)); T.paste(bl,(224,0)); tiles.append(T)
S=Image.new('RGB',(896,448))
for i,t in enumerate(tiles): S.paste(t,((i%2)*448,(i//2)*224))
S.save('results/occl.png'); print(S.size)
