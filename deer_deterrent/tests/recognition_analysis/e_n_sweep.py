# Is 0.85 reachable by any framing?  Sweep size, position, field and exposure for five pictures of the prints: D2 unlit (the phone
# photograph), D1 in room light (set-up session 08), and D1, D2 and the dog in the capture light (frames 6, 18 and 32).
import sys,glob,io,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageEnhance
from exp_util import *
d2=Image.open('results/d2_clean.png').convert('RGB')
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
d1=Image.open(S08+'.jpg').convert('RGB').crop((120,60,231,151))
f6=Image.open(stem(6)+'.jpg').convert('RGB').crop((50,12,262,178))
f18=Image.open(stem(18)+'.jpg').convert('RGB').crop((40,0,262,225))
n1=Image.open(stem(32)+'.jpg').convert('RGB').crop((30,20,280,170))
def sweep(pic,name):
    w0,h0=pic.size; best=[]; hist=[]
    for W in (24,28,32,36,40,44,48,52,56,60,64,72,80,88,96,112,128,144,160,180,200):
        H=int(round(W*h0/w0))
        if H>168: continue
        r0=pic.resize((W,H),Image.BOX if W<w0*0.7 else Image.BICUBIC)
        for gain in (0.6,0.8,1.0,1.3):
            r=np.clip(np.asarray(r0).astype(float)*gain,0,255).astype(np.uint8)
            for bg in (10,40,80,114,160,200):
                for fx,fy in ((.5,.5),(.25,.6),(.75,.6),(.5,.75),(.3,.8),(.7,.8),(.5,.35)):
                    a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=bg
                    l=int((224-W)*fx); t=28+int((168-H)*fy)
                    a[t:t+H,l:l+W]=r
                    o=forward(to_i8(a))
                    q=o['score0'][...,0]*0.25
                    ys,xs=np.mgrid[0:28,0:28]; cx=xs*8+4; cy=ys*8+4
                    ins=(cx>=l)&(cx<l+W)&(cy>=t)&(cy<t+H)
                    v=float(q[ins].max()) if ins.any() else -99
                    best.append((v,W,gain,bg,fx,fy)); hist.append(v)
    best.sort(reverse=True); h=np.array(hist)
    p=lambda v:round(float(1/(1+np.exp(-v))),3)
    print(name,'runs',len(h),'best',[(p(b[0]),)+b[1:] for b in best[:5]],'share>=0.5',round(float((h>=0).mean()),4),'share>=0.25',round(float((h>=-1.0986).mean()),3),'median',p(float(np.median(h))))
    # by width: best at each width
    by={}
    for b in best: by[b[1]]=max(by.get(b[1],-99),b[0])
    print('   best per picture width (model px):',{k:p(v) for k,v in sorted(by.items())})
sweep(d2,'D2 clean, no capture light')
sweep(d1,'D1 ambient (9 in frame)')
sweep(f6,'D1 white light (5 in frame)')
sweep(f18,'D2 white light (5 in frame)')
sweep(n1,'N1 dog white light (5 in frame)')
