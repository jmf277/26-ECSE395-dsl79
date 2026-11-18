# The D2 print from the phone photograph IMG_6710, with no capture light on it: gray, cut out on a printed checkerboard, and out of focus
# (the phone focused on the wires). "Clean" below means unlit.
# Same picture at every size, on plain fields, through a camera-like path: 320x240, JPEG, nearest, letterbox.
import sys,glob,os,json,io; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw,ImageEnhance
from exp_util import *
d2=Image.open('results/d2_rectified.png').convert('RGB')
# keep the picture area only (drop the dark top-right corner and the marker edge)
d2=d2.crop((10,40,470,555))
# whiten the ambient cast a little so the paper reads neutral
a=np.asarray(d2).astype(float); a=a/np.median(a.reshape(-1,3),axis=0)*200; d2=Image.fromarray(np.clip(a,0,255).astype(np.uint8))
d2.save('results/d2_clean.png')
def jpeg_path(canvas,q=60):
    b=io.BytesIO(); canvas.save(b,'JPEG',quality=q); b.seek(0)
    return np.asarray(Image.open(b).convert('RGB'))
def test(pic,name,fields=(40,114,200),widths=(16,20,24,28,32,40,48,56,64,80,96,112,128,160,200,240,280,320),pos='centre'):
    w0,h0=pic.size; out={}
    for bg in fields:
        line={}
        for W in widths:
            H=int(round(W*h0/w0))
            if H>240: line[W]=None; continue
            c=Image.new('RGB',(320,240),(bg,bg,bg))
            r=pic.resize((W,H),Image.BOX if W<w0*0.7 else Image.BICUBIC)
            l=(320-W)//2; t=(240-H)//2 if pos=='centre' else min(240-H,int(240*0.62-H/2))
            c.paste(r,(l,t))
            x=to_i8(firmware_resize(jpeg_path(c)))
            o=forward(x); hm=head_max(o)
            q=o['score0'][...,0]*0.25
            ys,xs=np.mgrid[0:28,0:28]; cx=(xs*8+4)/0.7; cy=(ys*8+4-28)/0.7
            ins=(cx>=l)&(cx<l+W)&(cy>=t)&(cy<t+H)
            best=float(q[ins].max()) if ins.any() else -99
            line[W]=(round(1/(1+np.exp(-best)),3),round(hm[1]['prob'],3),round(hm[2]['prob'],6))
        out[bg]=line
        print(name,'field',bg,'{width camera px: (best head0 on picture, best head1 anywhere, best head2 anywhere)}')
        print('   ',line)
    return out
r1=test(d2,'D2 clean')
# with a hot spot like the capture light's: three offset color lobes plus a white core at the position seen in frame 18
def lit(pic,strength=1.0):
    a=np.asarray(pic).astype(float); h,w=a.shape[:2]
    yy,xx=np.mgrid[0:h,0:w]
    def g(cx,cy,s): return np.exp(-(((xx-cx*w)**2+(yy-cy*h)**2)/(2*(s*w)**2)))
    r=g(0.52,0.52,0.20); gr=g(0.47,0.70,0.20); b=g(0.72,0.45,0.20)
    a=a*0.35+a*np.stack([r,gr,b],-1)*1.6*strength+np.stack([r,gr,b],-1)*90*strength
    return Image.fromarray(np.clip(a,0,255).astype(np.uint8))
d2lit=lit(d2); d2lit.save('results/d2_lit.png')
r2=test(d2lit,'D2 with synthetic RGB light')
json.dump(dict(clean={str(k):{str(a):b for a,b in v.items()} for k,v in r1.items()},lit={str(k):{str(a):b for a,b in v.items()} for k,v in r2.items()}),open('results/e_h_clean.json','w'))
# Color rendition: gray picture is all we have; try tinting as if a color print (brown body) to test "gray print" cheaply
def tint(pic):
    a=np.asarray(pic).astype(float); g=a.mean(-1)/255
    rgb=np.stack([g*1.05,g*0.82,g*0.62],-1)*255
    return Image.fromarray(np.clip(rgb,0,255).astype(np.uint8))
test(tint(d2),'D2 sepia-tinted',fields=(40,114))
# the same picture placed lower in the frame
c=d2
print('--- D2 clean, lower in frame')
test(d2,'D2 clean low',fields=(40,114),pos='low')
