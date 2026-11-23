import sys,json,glob,os; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
S07=glob.glob(REC+'/setup-check-20261104-07/*-frame-000001.json')[0][:-5]
PRA=glob.glob(REC+'/d1-practice-20261104-01/*-frame-000001.json')[0][:-5]
items=[('f6 D1 5in',stem(6)),('f18 D2',stem(18)),('f22 D3',stem(22)),('f32 N1',stem(32)),('f50 D1 2.5in',stem(50)),('f56 empty',stem(56)),('f62 D1',stem(62)),('s08 D1 9in ambient',S08),('prac D1 white',PRA),('s07 room ambient',S07)]
tiles=[]
def colormap(v):
    # v in 0..1 -> simple heat (black-red-yellow-white)
    r=np.clip(v*3,0,1);g=np.clip(v*3-1,0,1);b=np.clip(v*3-2,0,1)
    return np.stack([r,g,b],-1)
print('name | local maxima of head0 logit (logit, prob, cell y,x, center in camera px)')
for name,st in items:
    x=load_input(st); out=forward(x)
    q=out['score0'][...,0].astype(float)*0.25
    # local maxima listing
    lm=[]
    for y in range(28):
        for X in range(28):
            y0,y1,x0,x1=max(y-1,0),min(y+2,28),max(X-1,0),min(X+2,28)
            if q[y,X]==q[y0:y1,x0:x1].max() and q[y,X]>-6:
                cx=(X*8+4)*320/224; cy=(y*8+4-28)*320/224
                lm.append((q[y,X],round(float(1/(1+np.exp(-q[y,X]))),3),y,X,round(cx),round(cy)))
    lm.sort(reverse=True)
    print(name,lm[:6])
    img=from_i8(x).astype(np.uint8)
    im=Image.fromarray(img)
    # heat: logit from -6..0 mapped
    v=np.clip((q+6)/6,0,1)
    heat=(colormap(v)*255).astype(np.uint8)
    hm=Image.fromarray(heat).resize((224,224),Image.NEAREST)
    blend=Image.blend(im.convert('RGB'),hm,0.55)
    d=ImageDraw.Draw(blend)
    for b in boxes(out):
        bb=b['box']; f=224/320
        d.rectangle([bb[0]*f,bb[1]*f+28,bb[2]*f,bb[3]*f+28],outline=(0,255,0))
    tile=Image.new('RGB',(448,224)); tile.paste(im,(0,0)); tile.paste(blend,(224,0))
    d2=ImageDraw.Draw(tile); d2.text((3,3),name,fill=(255,255,255),stroke_width=1,stroke_fill=(0,0,0))
    tiles.append(tile)
cols=2; rows=(len(tiles)+1)//2
S=Image.new('RGB',(cols*448,rows*224))
for i,t in enumerate(tiles): S.paste(t,((i%cols)*448,(i//cols)*224))
S.save('results/heat.png'); print(S.size)
