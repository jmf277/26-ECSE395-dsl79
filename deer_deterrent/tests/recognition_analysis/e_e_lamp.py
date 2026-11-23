# Experiments with the round ceiling light ("lamp" in the names below) on the exact saved inputs: paint it out; scale it; move it; flip it.
import sys,glob,os,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
def cam2in(x,y): return int(x*0.7),int(y*0.7+28)
LAMP=(205,200,296,240)  # camera px, generous box around the ceiling light in the main session
def mask_lamp(x,box=LAMP):
    x=x.copy(); x0,y0=cam2in(box[0],box[1]); x1,y1=cam2in(box[2],box[3]); y1=min(y1,196)
    # fill with the average color of a ring just above and beside the box
    ring=np.concatenate([x[max(y0-6,28):y0,x0:x1].reshape(-1,3),x[y0:y1,max(x0-6,0):x0].reshape(-1,3),x[y0:y1,x1:min(x1+6,224)].reshape(-1,3)])
    x[y0:y1,x0:x1]=np.round(ring.mean(0)).astype(np.int8)
    return x
print('--- paint the ceiling light out (flat fill from its surroundings), all 68 frames: board score, score without the light, new top box')
rows=[]
for fr in range(1,69):
    st=stem(fr); rec=load_rec(st); x=load_input(st)
    o0=forward(x); o1=forward(mask_lamp(x)); t0=top(o0); t1=top(o1)
    rows.append(dict(frame=fr,trial=rec['trial'],s0=score(o0),top0=t0['box'] if t0 else None,s1=score(o1),top1=t1['box'] if t1 else None))
    print(fr,'t',rec['trial'],round(score(o0),3),t0['box'] if t0 else None,'->',round(score(o1),3),t1['box'] if t1 else None)
json.dump(rows,open('results/e_e_lamp.json','w'))
# where is the top box on the board? classify
def onlamp(b): 
    return b is not None and b[0]>=200 and b[1]>=195
import collections
c=collections.Counter()
for r in rows:
    c[(r['trial'],'light' if onlamp(r['top0']) else ('none' if r['top0'] is None else 'other'))]+=1
print(sorted(c.items()))
print('--- scale the real ceiling light crop (from frame 56) and paste it back on its own room at the same center')
st=stem(56); x=load_input(st)
ix0,iy0=cam2in(LAMP[0],LAMP[1]); ix1,iy1=cam2in(LAMP[2],LAMP[3]); iy1=196
crop=x[iy0:iy1,ix0:ix1]
base=mask_lamp(x)
img=Image.fromarray(np.clip(crop.astype(int)*2,0,255).astype(np.uint8))
for f in (0.25,0.5,0.75,1.0,1.25,1.5,2,2.5,3,4):
    w=max(1,int(crop.shape[1]*f)); h=max(1,int(crop.shape[0]*f))
    r=np.asarray(img.resize((w,h),Image.BILINEAR)).astype(int)//2
    b=base.copy()
    cx,cy=(ix0+ix1)//2,(iy0+iy1)//2
    if h>120 : continue
    t=max(28,min(cy-h//2,196-h)); l=max(0,min(cx-w//2,224-w))
    # lighter-of compositing so the crop's dark floor does not cut a rectangle
    region=b[t:t+h,l:l+w]
    b[t:t+h,l:l+w]=np.maximum(region,r[:region.shape[0],:region.shape[1]]).astype(np.int8)
    o=forward(b); hm=head_max(o)
    print('scale',f,'light px wide (224 units) about',round(38*f),'score',round(score(o),3),'head logits',[hm[s]['logit'] for s in range(3)],top(o)['box'] if top(o) else None)
print('--- move the ceiling light (same size, same crop) to other places in the same empty room')
for (cx,cy) in ((172,178),(112,178),(40,178),(172,120),(112,120),(40,120),(172,60),(112,60),(40,60)):
    b=base.copy(); h,w=crop.shape[:2]
    t=max(28,min(cy-h//2,196-h)); l=max(0,min(cx-w//2,224-w))
    region=b[t:t+h,l:l+w]
    b[t:t+h,l:l+w]=np.maximum(region,crop)
    o=forward(b); print((cx,cy),'score',round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
print('--- flip the frame 56 input vertically / horizontally, and flip only the light in place')
for name,a in (('as saved',x),('vertical flip',x[::-1]),('horizontal flip',x[:,::-1]),('180 rotation',x[::-1,::-1])):
    o=forward(a); print(name,round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
b=base.copy(); region=b[iy0:iy1,ix0:ix1]; b[iy0:iy1,ix0:ix1]=np.maximum(region,crop[::-1]); o=forward(b); print('light crop flipped in place',round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
b=base.copy(); region=b[iy0:iy1,ix0:ix1]; b[iy0:iy1,ix0:ix1]=np.maximum(region,crop); o=forward(b); print('light crop re-pasted upright',round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
print('--- put the real ceiling light crop onto a print frame (f50, D1 at 2.5in, score 0), lower right')
x50=load_input(stem(50)); b=x50.copy(); h,w=crop.shape[:2]; b[iy0:iy1,ix0:ix1]=np.maximum(b[iy0:iy1,ix0:ix1],crop); o=forward(b); print('f50+light',round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
print('--- on plain dark and plain mid fields')
for v in (5,20,57):
    b=np.full((224,224,3),v,dtype=np.int8); b[:28]=57;b[196:]=57
    b[iy0:iy1,ix0:ix1]=np.maximum(b[iy0:iy1,ix0:ix1],crop); o=forward(b); print('field',v,round(score(o),3),[ (round(r['score'],3),r['box']) for r in boxes(o)][:3])
Image.fromarray(np.clip(crop.astype(int)*2,0,255).astype(np.uint8)).resize((crop.shape[1]*6,crop.shape[0]*6),Image.NEAREST).save('results/lamp_crop.png')
