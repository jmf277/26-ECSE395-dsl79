# A. For every saved input: per-head maximum, which head and cell give the top score, top box.
import json,glob,os,sys; sys.path.insert(0,".")
import numpy as np
from exp_util import *
rows=[]
folders=sorted(glob.glob(REC+'/*/'))
for fo in folders:
    for js in sorted(glob.glob(fo+'*-frame-*.json')):
        if js.endswith('.received.json'): continue
        st=js[:-5]
        if not os.path.exists(st+'.input.i8'): continue
        rec=load_rec(st); x=load_input(st); out=forward(x); hm=head_max(out); t=top(out)
        rows.append(dict(session=os.path.basename(fo[:-1]),trial=rec['trial'],frame=rec['frame'],light=rec['lighting'],score=rec['score'],
            head_q=[hm[s]['q'] for s in range(3)],head_logit=[hm[s]['logit'] for s in range(3)],head_prob=[round(hm[s]['prob'],4) for s in range(3)],
            top_stage=t['stage'] if t else None,top_cell=t['cell'] if t else None,top_box=t['box'] if t else None,nboxes=len(boxes(out))))
json.dump(rows,open('results/e_a_heads.json','w'),indent=0)
print('sess trial frame light score | q(head0,1,2) logit | prob per head | top_stage box')
for r in rows:
    print(f"{r['session'][:9]:9s} t{r['trial']} f{r['frame']:<3d} {r['light'][:3]} {r['score']:.3f} | {r['head_q']} {r['head_logit']} | {r['head_prob']} | {r['top_stage']} {r['top_box']} n={r['nboxes']}")
import collections
print(collections.Counter(r['top_stage'] for r in rows))
for s in range(3):
    print('head',s,'max prob anywhere',max(r['head_prob'][s] for r in rows),'median',np.median([r['head_prob'][s] for r in rows]))
