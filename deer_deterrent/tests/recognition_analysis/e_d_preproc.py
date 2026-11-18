# Does the saved exact input equal what the JPEG + stated preprocessing gives?  Color order, scale, letterbox, resize method.
import sys,glob,os,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image
from exp_util import *
frames=[1,6,18,22,32,50,56,62]
print('frame | band value row0,row27,row196,row223 | i8 min,max | mean abs diff to the reconstruction: nearest-floor RGB / nearest-floor BGR / bilinear RGB / area-like RGB | exact-byte share nearest RGB')
for fr in frames:
    st=stem(fr); x=load_input(st).astype(int)
    im=np.asarray(Image.open(st+'.jpg').convert('RGB'))
    rec=to_i8(firmware_resize(im)).astype(int)
    bgr=to_i8(firmware_resize(im[...,::-1])).astype(int)
    pil=Image.open(st+'.jpg').convert('RGB')
    def letter(p,mode):
        r=p.resize((224,168),mode); a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=np.asarray(r); return to_i8(a).astype(int)
    bil=letter(pil,Image.BILINEAR); box=letter(pil,Image.BOX)
    body=(28,196)
    d=lambda a:float(np.abs(a[28:196]-x[28:196]).mean())
    print(fr,'|',np.unique(x[0]),np.unique(x[27]),np.unique(x[196]),np.unique(x[223]),'|',x.min(),x.max(),'|',round(d(rec),3),round(d(bgr),3),round(d(bil),3),round(d(box),3),'|',round(float((rec[28:196]==x[28:196]).mean()),4))
# score consequences of resize method on real frames, same JPEG, same model
print()
print('frame board_score | nearest (from the Pillow-decoded JPEG) | bilinear | box-average | stretched to 224x224 no letterbox | BGR-swapped | gray | flipped vertically')
rows=[]
for fr in list(range(1,69)):
    st=stem(fr); rec=load_rec(st)
    pil=Image.open(st+'.jpg').convert('RGB'); im=np.asarray(pil)
    def s(a): return score(forward(a))
    near=s(to_i8(firmware_resize(im)))
    def letter(mode):
        r=pil.resize((224,168),mode); a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=np.asarray(r); return a
    bil=s(to_i8(letter(Image.BILINEAR))); box=s(to_i8(letter(Image.BOX)))
    str_=s(to_i8(np.asarray(pil.resize((224,224),Image.BILINEAR))))
    x=load_input(st)
    sw=s(x[...,::-1])
    g=np.round(x[...,0]*0.299+x[...,1]*0.587+x[...,2]*0.114).astype(np.int8); gray=s(np.stack([g,g,g],-1))
    fl=s(x[::-1])
    rows.append(dict(frame=fr,board=rec['score'],near=near,bil=bil,box=box,stretch=str_,bgr=sw,gray=gray,flip=fl))
json.dump(rows,open('results/e_d_preproc.json','w'))
print('frame board near bil box stretch bgr gray flip')
for r in rows:
    print(r['frame'],*(round(r[k],3) for k in ('board','near','bil','box','stretch','bgr','gray','flip')))
