# The 175 camera pictures saved in tests/captures (no deer in any) through the 8-bit model: a negative control.
import sys,glob,os,json; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
D='../captures'
rows=[]
for s in sorted(os.listdir(D)):
    fs=sorted(glob.glob(f'{D}/{s}/**/*.jpg',recursive=True))
    sc=[]
    for f in fs:
        im=np.asarray(Image.open(f).convert('RGB'))
        o=forward(to_i8(firmware_resize(im))); hm=head_max(o); t=top(o)
        r=dict(session=s,file=os.path.basename(f),size=im.shape[:2],score=score(o),head=[hm[k]['prob'] for k in range(3)],box=t['box'] if t else None,stage=t['stage'] if t else None)
        rows.append(r); sc.append(r['score'])
    if sc: print(s[:48].ljust(48),'n',len(sc),'max',round(max(sc),3),'mean',round(float(np.mean(sc)),3),'>=.25',sum(x>=.25 for x in sc),'>=.5',sum(x>=.5 for x in sc),'head1max',round(max(r['head'][1] for r in rows if r['session']==s),3),'head2max',round(max(r['head'][2] for r in rows if r['session']==s),6))
print('total',len(rows),'max',max(r['score'] for r in rows),'>=.25',sum(r['score']>=.25 for r in rows),'>=.5',sum(r['score']>=.5 for r in rows),'>=.85',sum(r['score']>=.85 for r in rows),'any box',sum(r['score']>0 for r in rows))
print('stage counter',{k:sum(r['stage']==k for r in rows) for k in (0,1,2,None)},'head1 max',max(r['head'][1] for r in rows),'head2 max',max(r['head'][2] for r in rows))
json.dump(rows,open('results/e_j_captures.json','w'))
rows.sort(key=lambda r:-r['score'])
tiles=[]
for r in rows[:12]:
    f=glob.glob(f"{D}/{r['session']}/**/{r['file']}",recursive=True)[0]
    im=Image.open(f).convert('RGB'); d=ImageDraw.Draw(im)
    if r['box']: d.rectangle(r['box'],outline=(0,255,0))
    d.text((3,3),f"{r['score']:.3f} {r['session'][16:40]}",fill=(255,255,255),stroke_width=1,stroke_fill=(0,0,0))
    tiles.append(im)
W,H=tiles[0].size
S=Image.new('RGB',(4*W,3*H))
for i,t in enumerate(tiles): S.paste(t,((i%4)*W,(i//4)*H))
S.save('results/captures_top.png'); print(S.size)
