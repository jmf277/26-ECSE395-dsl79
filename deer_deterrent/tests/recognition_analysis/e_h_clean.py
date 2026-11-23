# The D2 print from the phone photograph IMG_6710, as the camera saw it (rectify_d2.py), with no capture light on it: gray, cut out on a
# printed checkerboard, and out of focus (the phone focused on the wires). "Clean" below means unlit.
# Same picture at every size, on plain fields, through a camera-like path: 320x240, JPEG with the camera's own quantization tables and
# chroma subsampling, Pillow's decode, nearest, letterbox.
import sys,glob,os,json,io; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw,ImageEnhance,ImageOps,JpegImagePlugin
from exp_util import *
d2=Image.open('results/d2_rectified.png').convert('RGB')
# keep the picture area only (drop the dark top-left corner and the marker edge at the left)
d2=d2.crop((30,40,490,555))
# whiten the ambient cast a little so the paper reads neutral
a=np.asarray(d2).astype(float); a=a/np.median(a.reshape(-1,3),axis=0)*200; d2=Image.fromarray(np.clip(a,0,255).astype(np.uint8))
d2.save('results/d2_clean.png')
# the camera's own JPEG settings, as frame 18's saved JPEG carries them: the quantization tables its quality 12 gives (a setting on the
# camera's own scale, not Pillow's) and its 4:2:2 chroma subsampling
with Image.open(stem(18)+'.jpg') as cam: CAM_QTABLES=cam.quantization; CAM_SUBSAMPLING=JpegImagePlugin.get_sampling(cam)
def jpeg_path(canvas):
    b=io.BytesIO(); canvas.save(b,'JPEG',qtables=CAM_QTABLES,subsampling=CAM_SUBSAMPLING); b.seek(0)
    return np.asarray(Image.open(b).convert('RGB'))
def test(pic,name,fields=(40,114,200),widths=(16,20,24,28,32,40,48,56,64,80,96,112,128,160,200,240,280,320),pos='centre',jp=None,show=True,resample=None):
    w0,h0=pic.size; out={}
    for bg in fields:
        line={}
        for W in widths:
            H=int(round(W*h0/w0))
            if H>240: line[W]=None; continue
            c=Image.new('RGB',(320,240),(bg,bg,bg))
            r=pic.resize((W,H),resample if resample is not None else (Image.BOX if W<w0*0.7 else Image.BICUBIC))
            l=(320-W)//2; t=(240-H)//2 if pos=='centre' else min(240-H,int(240*0.62-H/2))
            c.paste(r,(l,t))
            x=to_i8(firmware_resize((jp or jpeg_path)(c)))
            o=forward(x); hm=head_max(o)
            q=o['score0'][...,0]*0.25
            ys,xs=np.mgrid[0:28,0:28]; cx=(xs*8+4)/0.7; cy=(ys*8+4-28)/0.7
            ins=(cx>=l)&(cx<l+W)&(cy>=t)&(cy<t+H)
            best=float(q[ins].max()) if ins.any() else -99
            line[W]=(round(1/(1+np.exp(-best)),3),round(hm[1]['prob'],3),round(hm[2]['prob'],6))
        out[bg]=line
        if show:
            print(name,'field',bg,'{width camera px: (best head0 on picture, best head1 anywhere, best head2 anywhere)}')
            print('   ',line)
    return out
r1=test(d2,'D2 clean')
# with a hot spot like the capture light's: three offset color lobes, red, green and blue, at the positions seen in frame 18, white where
# they overlap
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
# Which changes come from the imitation and which from the path? The variants: D2 as the camera saw it and as a person sees it (its
# mirror image, which puts the lobes on other parts of the body); each through the camera's JPEG settings and through Pillow's quality
# 60 with its default 4:2:0 chroma subsampling; and each shrunk with five of Pillow's filters: box (what test() uses at every width here,
# all below 0.7 of the print's width, as every experiment that shrinks a print does below that), bilinear, bicubic, nearest and Lanczos,
# since a resize by itself moves a frame's score (e_d_preproc.py). A change that holds through all 20 does not come from what was varied;
# the decode is Pillow's in every one. Per field: the best score on the picture over the widths and the first width reaching it, unlit
# and lit, and the change in steps of the finest layer's ladder, sigmoid(k*0.25)
def jpeg_q60(canvas):
    b=io.BytesIO(); canvas.save(b,'JPEG',quality=60); b.seek(0)
    return np.asarray(Image.open(b).convert('RGB'))
def best(r):
    out={}
    for bg,line in r.items():
        s=max(v[0] for v in line.values() if v); out[bg]=(s,min(W for W,v in line.items() if v and v[0]==s))
    return out
def step(s): return round(float(np.log(s/(1-s)))/0.25)
print('--- D2 as the camera saw it and as a person sees it, through two JPEG settings, shrunk with five filters: per field, the best on the picture over the widths (first width in camera px reaching it), unlit -> lit, and the change in ladder steps')
for view,pic in (('as the camera saw it',d2),('as a person sees it',ImageOps.mirror(d2))):
    for jname,jp in (("the camera's JPEG settings",jpeg_path),("Pillow's quality 60, 4:2:0",jpeg_q60)):
        for fname,rs in (('box',Image.BOX),('bilinear',Image.BILINEAR),('bicubic',Image.BICUBIC),('nearest',Image.NEAREST),('Lanczos',Image.LANCZOS)):
            un=best(test(pic,'D2',jp=jp,show=False,resample=rs)); li=best(test(lit(pic),'D2',jp=jp,show=False,resample=rs))
            print('D2',view+', through',jname+', shrunk with the',fname,'filter:',' | '.join(f'field {bg} {un[bg][0]} ({un[bg][1]}) -> {li[bg][0]} ({li[bg][1]}), steps {step(li[bg][0])-step(un[bg][0]):+d}' for bg in un))
