# C. What does the model answer to a plain ellipse on a plain field?  (no deer, no camera)
import sys; sys.path.insert(0,'.')
import numpy as np, itertools, json
from exp_util import *
def ellipse(bg,fg,w,h,cx=112,cy=112,soft=0.0,bands=True):
    yy,xx=np.mgrid[0:224,0:224].astype(float)
    r=np.sqrt(((xx-cx)/(w/2.0))**2+((yy-cy)/(h/2.0))**2)
    if soft>0:
        a=np.clip((1+soft-r)/soft,0,1)   # linear falloff beyond the core
    else:
        a=(r<=1).astype(float)
    im=bg+(fg-bg)*a
    rgb=np.stack([im,im,im],-1)
    if bands:
        rgb[:28]=114; rgb[196:]=114
    return np.clip(rgb,0,255).astype(np.uint8)
res=[]
def run(rgb):
    out=forward(to_i8(rgb)); hm=head_max(out)
    return [hm[s]['logit'] for s in range(3)],[round(hm[s]['prob'],3) for s in range(3)],hm[0]['cell']
print('--- bright ellipse on dark, horizontal 2:1, varying width (224-px units), hard edge & soft edge, at center')
print('bg fg  w  h soft | head0 logit prob | head1 | head2')
for bg,fg in ((10,255),(40,255),(10,200),(114,255),(60,255)):
  for soft in (0.0,0.6):
    for w in (6,10,16,24,32,40,48,64,80,96,128,160,200):
        h=w/2
        l,p,c=run(ellipse(bg,fg,w,h,soft=soft))
        res.append(dict(kind='bright',bg=bg,fg=fg,w=w,h=h,soft=soft,logit=l,prob=p))
        print(bg,fg,w,h,soft,'|',l[0],p[0],'|',l[1],p[1],'|',l[2],p[2])
print('--- polarity: dark ellipse on bright field')
for bg,fg in ((245,10),(200,10),(114,10)):
    for w in (16,32,48,64,96,128):
        h=w/2
        l,p,c=run(ellipse(bg,fg,w,h,soft=0.6))
        res.append(dict(kind='dark',bg=bg,fg=fg,w=w,h=h,soft=0.6,logit=l,prob=p))
        print(bg,fg,w,h,'|',l[0],p[0],'|',l[1],p[1],'|',l[2],p[2])
print('--- shape: width 40; aspect tall/round/wide; bg 40 fg 255 soft .6')
for w,h in ((40,10),(40,14),(40,20),(40,28),(40,40),(28,40),(20,40),(14,40)):
    l,p,c=run(ellipse(40,255,w,h,soft=0.6))
    print(w,h,'|',l[0],p[0],'|',l[1],p[1],'|',l[2],p[2])
print('--- position: 40x20 at several places, bg 40 fg 255 soft .6')
for cx,cy in ((112,112),(40,60),(180,60),(40,170),(180,170),(112,180),(112,50)):
    l,p,c=run(ellipse(40,255,40,20,cx=cx,cy=cy,soft=0.6))
    print((cx,cy),'|',l[0],p[0],c,'|',l[1],p[1],'|',l[2],p[2])
print('--- color of the blob: 40x20 bg 40, soft .6')
for col,name in (((255,255,255),'white'),((255,200,200),'pinkish'),((255,0,0),'red'),((0,255,0),'green'),((0,0,255),'blue'),((255,255,0),'yellow'),((200,200,255),'bluish')):
    g=ellipse(0,255,40,20,soft=0.6,bands=False)[...,0].astype(float)/255
    rgb=40+(np.array(col,dtype=float)-40)*g[...,None]
    rgb[:28]=114;rgb[196:]=114
    l,p,c=run(np.clip(rgb,0,255).astype(np.uint8))
    print(name,'|',l[0],p[0],'|',l[1],p[1],'|',l[2],p[2])
print('--- plain fields, no blob')
for v in (0,40,114,200,255):
    rgb=np.full((224,224,3),v,dtype=np.uint8); rgb[:28]=114; rgb[196:]=114
    l,p,c=run(rgb); print('field',v,'|',l[0],p[0],'|',l[1],p[1],'|',l[2],p[2])
rgb=np.full((224,224,3),114,dtype=np.uint8); l,p,c=run(rgb); print('all 114 (the board self-test)',l,p)
json.dump(res,open('results/e_c_synth.json','w'))
