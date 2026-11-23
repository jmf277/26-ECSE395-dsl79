import sys,glob,json,os,time,re
import numpy as np
sys.path.insert(0,'.')
from interp import *
MODEL,REC=sys.argv[1],sys.argv[2]
sat=len(sys.argv)>3 and sys.argv[3]=='sat'
m=Model(MODEL,qacc_sat=sat)
print('output exponents',{k:m.exp[k] for k in m.outputs},'input',m.exp[m.inputs[0]])
files=sorted(glob.glob(REC+'/*/*.input.i8'))
print(len(files),'saved inputs')
ok_score=ok_boxes=0;rows=[]
t0=time.time()
for f in files:
    stem=f[:-len('.input.i8')]
    rec=json.load(open(stem+'.json'))
    x=np.fromfile(f,dtype=np.int8).reshape(224,224,3)
    T=m.run(x)
    out=decode({k:T[k] for k in m.outputs},m.exp)
    s=max([r['score'] for r in out],default=0.0)
    bs=sorted([ (round(r['score'],6),tuple(r['box'])) for r in out])
    br=sorted([ (round(b['score'],6),tuple(b['box'])) for b in rec['boxes']])
    sm=abs(s-rec['score'])<1e-6; bm=bs==br
    ok_score+=sm; ok_boxes+=bm
    rows.append(dict(file=os.path.basename(stem),rec_score=rec['score'],computed_score=s,score_match=bool(sm),box_match=bool(bm),rec_boxes=br,computed_boxes=bs))
    if not(sm and bm):
        print('DIFF',os.path.relpath(stem,REC),'rec',rec['score'],br,'computed',round(s,6),bs)
print('score matches',ok_score,'of',len(files),'; full box-list matches',ok_boxes,'of',len(files),'; secs per frame',round((time.time()-t0)/len(files),2))
json.dump(rows,open('results/compare_board_%s.json'%('sat' if sat else 'plain'),'w'),indent=0)
