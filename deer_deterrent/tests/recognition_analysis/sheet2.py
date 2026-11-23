import sys,json,glob,os
import numpy as np
from PIL import Image,ImageDraw
REC='../recognition'
names=['setup-check-20261104-04','setup-check-20261104-05','setup-check-20261104-06','setup-check-20261104-07','setup-check-20261104-08','d1-practice-20261104-01']
tiles=[]
for n in names:
    p=glob.glob(f'{REC}/{n}/*-frame-*.json')
    p=[x for x in p if not x.endswith('received.json')][0]
    rec=json.load(open(p))
    im=Image.open(p[:-5]+'.jpg').convert('RGB')
    s=2
    im=im.resize((im.size[0]*s,im.size[1]*s),Image.NEAREST)
    d=ImageDraw.Draw(im)
    for b in sorted(rec['boxes'],key=lambda b:b['score']):
        x0,y0,x1,y1=[int(v*s) for v in b['box']]
        d.rectangle([x0,y0,x1,y1],outline=(0,255,0) if b['score']==rec['score'] else (255,255,0))
        d.text((x0+2,y0+2),f"{b['score']:.3f}",fill=(255,255,0))
    d.text((3,3),f"{n[-14:]} light={rec['lighting']} s={rec['score']:.3f}",fill=(255,255,255),stroke_width=1,stroke_fill=(0,0,0))
    tiles.append(im)
W,H=tiles[0].size
S=Image.new('RGB',(3*W,2*H))
for i,t in enumerate(tiles): S.paste(t,((i%3)*W,(i//3)*H))
S=S.resize((S.size[0]*2//3,S.size[1]*2//3),Image.LANCZOS)
S.save(sys.argv[1]); print(S.size)
