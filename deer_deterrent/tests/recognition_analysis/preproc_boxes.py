# Where do the boxes land when the same frames are decoded or resized another way (Pillow decode with the firmware's resize, bilinear,
# box, stretched with no bars, flipped)? (interpreter results, no board)
import sys,glob,json; sys.path.insert(0,'.')
import numpy as np, math, collections
from PIL import Image,ImageDraw
from exp_util import *
def k(s): return None if s==0 else round(math.log(s/(1-s))/0.25)
def ph(f):
    return 'D1 5in' if f<=10 else 'D2' if f<=20 else 'D3' if f<=30 else 'N1' if f<=40 else 'D1 2.5in' if f<=50 else 'empty' if f<=56 else 'D1 t6' if f<=62 else 'empty'
res={}
tiles=[]
for fr in range(1,69):
    st=stem(fr); rec=load_rec(st)
    pil=Image.open(st+'.jpg').convert('RGB'); im=np.asarray(pil)
    def letter(mode):
        r=pil.resize((224,168),mode); a=np.full((224,224,3),114,dtype=np.uint8); a[28:196]=np.asarray(r); return a
    var={'board':load_input(st),'near':to_i8(firmware_resize(im)),'bil':to_i8(letter(Image.BILINEAR)),'box':to_i8(letter(Image.BOX)),'stretch':to_i8(np.asarray(pil.resize((224,224),Image.BILINEAR))),'flip':load_input(st)[::-1]}
    for name,x in var.items():
        o=forward(x)
        # each box in camera pixel coordinates of the picture as saved: decode() maps a letterboxed input's boxes to the camera picture;
        # a stretched input has no gray bands, so its boxes are decoded as for a 320x320 picture, which has none (x then in camera
        # pixels, y on the same scale), and y scaled by 240/320 to the camera picture's rows; a flipped input's boxes are turned back
        # to where they lie in the picture as saved
        bs=decode(o,model().exp,cam_w=320,cam_h=320) if name=='stretch' else boxes(o)
        out=[]
        for b in bs:
            bb=b['box']
            if name=='stretch':
                cam=[bb[0],bb[1]*240/320,bb[2],bb[3]*240/320]
            elif name=='flip':
                iy0=bb[1]*0.7+28; iy1=bb[3]*0.7+28; fy0=224-iy1; fy1=224-iy0
                cam=[bb[0],(fy0-28)/0.7,bb[2],(fy1-28)/0.7]
            else:
                cam=list(bb)
            onlight=cam[0]>=200 and cam[1]>=195
            out.append(dict(score=round(b['score'],4),cam=[round(v) for v in cam],light=bool(onlight)))
        res.setdefault(name,{})[fr]=out
json.dump(res,open('results/preproc_boxes.json','w'))
for name in res:
    c=collections.defaultdict(lambda:[0,0.0,0,0.0])
    lst=collections.defaultdict(list)
    for fr,bs in res[name].items():
        for b in bs:
            d=c[ph(fr)]
            if b['light']:
                d[0]+=1; d[1]=max(d[1],b['score'])
            else:
                d[2]+=1; d[3]=max(d[3],b['score']); lst[ph(fr)].append((fr,b['score'],b['cam']))
    print(name,{p:{'light boxes':v[0],'best light':v[1],'other boxes':v[2],'best other':v[3]} for p,v in c.items()})
    for p,l in lst.items():
        print('   other',p,l[:14])
