# Take the printed picture as the camera saw it, shrink it, paste it on a plain field, run the 8-bit model.
# Question: does the printed deer score better once it is as small as the model seems to want?
import sys,glob,os,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
PRA=glob.glob(REC+'/d1-practice-20261104-01/*-frame-000001.json')[0][:-5]
items=[('D1 5in white f6',stem(6),(50,12,262,178)),('D2 5in white f18',stem(18),(40,0,262,225)),('D3 5in white f22',stem(22),(5,0,270,220)),('N1 dog 5in white f32',stem(32),(30,20,280,170)),('D1 2.5in white f50',stem(50),(0,0,320,240)),('D1 9in ambient s08',S08,(120,60,231,151)),('D1 close white practice',PRA,(70,10,290,190))]
fields=(40,114,200)
widths=(16,20,24,28,32,40,48,56,64,80,96,112,128,160,200)
tab={}
tiles=[]
for name,st,box in items:
    pil=Image.open(st+'.jpg').convert('RGB').crop(box)
    w0,h0=pil.size
    print(name,'crop',pil.size)
    for bg in fields:
        line=[]
        for W in widths:
            H=int(round(W*h0/w0))
            if H>168: 
                line.append(None); continue
            r=pil.resize((W,H),Image.BOX if W<w0*0.7 else Image.BILINEAR)
            a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=bg
            l=(224-W)//2; t=28+(168-H)//2
            a[t:t+H,l:l+W]=np.asarray(r)
            o=forward(to_i8(a))
            # best probability on any cell of head 0 whose center lies inside the pasted picture
            q=o['score0'][...,0]*0.25
            ys,xs=np.mgrid[0:28,0:28]; cx=xs*8+4; cy=ys*8+4
            ins=(cx>=l)&(cx<l+W)&(cy>=t)&(cy<t+H)
            best=float(q[ins].max()) if ins.any() else -99
            line.append(round(1/(1+np.exp(-best)),3))
            if bg==40 and W in (24,40,56,80,128): 
                im=Image.fromarray(a); d=ImageDraw.Draw(im); 
                for b in boxes(o):
                    bb=b['box']; d.rectangle([bb[0]*.7,bb[1]*.7+28,bb[2]*.7,bb[3]*.7+28],outline=(0,255,0))
                d.text((2,2),f"{name[:12]} w{W} {line[-1]}",fill=(255,255,255))
                tiles.append(im.crop((0,28,224,196)))
        tab[(name,bg)]=line
        print('  field',bg,dict(zip(widths,line)))
json.dump({f"{k[0]}|{k[1]}":v for k,v in tab.items()},open('results/e_f_scale.json','w'))
cols=5; rows=(len(tiles)+cols-1)//cols
S=Image.new('RGB',(cols*224,rows*168))
for i,t in enumerate(tiles): S.paste(t,((i%cols)*224,(i//cols)*168))
S=S.resize((S.size[0]*3//4,S.size[1]*3//4),Image.LANCZOS); S.save('results/scale.png'); print(S.size)
