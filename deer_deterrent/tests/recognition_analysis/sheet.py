import sys,json,glob,os
import numpy as np
from PIL import Image,ImageDraw
REC='../recognition/p3-demo-20261104-02'
frames=[int(x) for x in sys.argv[2].split(',')]
scale=float(sys.argv[3]) if len(sys.argv)>3 else 1.0
cols=int(sys.argv[4]) if len(sys.argv)>4 else 4
tiles=[]
for fr in frames:
    p=glob.glob(REC+f'/*-frame-{fr:06d}.json')[0]
    rec=json.load(open(p))
    im=Image.open(p[:-5]+'.jpg').convert('RGB')
    w,h=im.size
    im=im.resize((int(w*scale),int(h*scale)),Image.NEAREST)
    d=ImageDraw.Draw(im)
    for b in sorted(rec['boxes'],key=lambda b:b['score']):
        x0,y0,x1,y1=[int(v*scale) for v in b['box']]
        d.rectangle([x0,y0,x1,y1],outline=(0,255,0) if b['score']==rec['score'] else (255,255,0))
    d.text((3,3),f"f{fr} t{rec['trial']} s={rec['score']:.3f} n={len(rec['boxes'])}",fill=(255,255,255),stroke_width=1,stroke_fill=(0,0,0))
    tiles.append(im)
W,H=tiles[0].size
rows=(len(tiles)+cols-1)//cols
S=Image.new('RGB',(cols*W,rows*H))
for i,t in enumerate(tiles):
    S.paste(t,((i%cols)*W,(i//cols)*H))
S.save(sys.argv[1])
print(S.size)
