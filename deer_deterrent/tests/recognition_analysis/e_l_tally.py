import sys,json,glob; sys.path.insert(0,'.')
import numpy as np
# the 68 frame records of the main session as committed; its trials by frame number as the recordings README gives them
rows=[json.load(open(p)) for p in glob.glob('../recognition/p3-demo-20261104-02/*-frame-*.json') if not p.endswith('.received.json')]; rows.sort(key=lambda r:r['frame'])
print(len(rows),'frames; all model hash same:',len(set(r['model_sha256'] for r in rows))==1)
def phase(r):
    f=r['frame']
    if f<=10: return 'D1 5in'
    if f<=20: return 'D2'
    if f<=30: return 'D3'
    if f<=40: return 'N1 dog'
    if f<=50: return 'D1 2.5in'
    if f<=56: return 'empty before'
    if f<=62: return 'D1 cont'
    return 'empty after'
def onlamp(b): return b[0]>=200 and b[1]>=195
ph={}
for r in rows:
    p=phase(r); d=ph.setdefault(p,dict(n=0,scores=[],anybox=0,lamp_frames=0,other_frames=0,toplamp=0,topother=0,max_other=0.0,max_lamp=0.0,other_boxes=[]))
    d['n']+=1; d['scores'].append(r['score'])
    bs=r['boxes']
    if bs: d['anybox']+=1
    if any(onlamp(b['box']) for b in bs): d['lamp_frames']+=1
    oth=[b for b in bs if not onlamp(b['box'])]
    if oth: d['other_frames']+=1; d['other_boxes']+=[(r['frame'],round(b['score'],3),b['box']) for b in oth]
    if bs:
        t=max(bs,key=lambda b:b['score'])
        if onlamp(t['box']): d['toplamp']+=1
        else: d['topother']+=1
    d['max_other']=max([d['max_other']]+[b['score'] for b in oth])
    d['max_lamp']=max([d['max_lamp']]+[b['score'] for b in bs if onlamp(b['box'])])
for p,d in ph.items():
    print(p,'n',d['n'],'frames with any box',d['anybox'],'| frames with a light box',d['lamp_frames'],'| frames with a non-light box',d['other_frames'],'| top box on light',d['toplamp'],'other',d['topother'],'| best light',round(d['max_lamp'],3),'best non-light',round(d['max_other'],3),'| score min,max',round(min(d['scores']),3),round(max(d['scores']),3))
    if d['other_boxes']: print('    non-light boxes:',d['other_boxes'])
# Separation
deer=[r['score'] for r in rows if phase(r) in('D1 5in','D2','D3','D1 2.5in','D1 cont')]
non=[r['score'] for r in rows if phase(r) in('N1 dog','empty before','empty after')]
gt=sum((a>b)+0.5*(a==b) for a in deer for b in non)/(len(deer)*len(non))
print('deer frames',len(deer),'non-deer frames',len(non),'share of deer/non-deer frame pairs in which the deer frame scores higher, ties counted half (AUC)',round(gt,3))
# excluding the boxes on the ceiling light
def nolamp(r): 
    v=[b['score'] for b in r['boxes'] if not onlamp(b['box'])]; return max(v) if v else 0.0
deer=[nolamp(r) for r in rows if phase(r) in('D1 5in','D2','D3','D1 2.5in','D1 cont')]
non=[nolamp(r) for r in rows if phase(r) in('N1 dog','empty before','empty after')]
gt=sum((a>b)+0.5*(a==b) for a in deer for b in non)/(len(deer)*len(non))
print('same, ignoring boxes on the light: AUC (ties counted half)',round(gt,3),'; deer frames with a non-light box',sum(x>0 for x in deer),'of',len(deer),'; non-deer',sum(x>0 for x in non),'of',len(non))
# gate replay within each trial
def gate(s,thr):
    h=[];first=None;n=0
    for i,x in enumerate(s,1):
        h.append(x>=thr);h=h[-3:]
        if sum(h)>=2:
            n+=1
            if first is None:first=i
    return first,n
tr={}
for r in rows: tr.setdefault(r['trial'],[]).append(r)
print('two-of-three replay (first confirming frame within trial, frames in confirmed state)')
for thr in (0.85,0.6,0.5,0.45,0.43,0.40,0.35,0.30,0.25,0.20,0.15,0.14,0.11,0.09):
    print(thr,{t:gate([x['score'] for x in v],thr) for t,v in sorted(tr.items())})
print('box sizes (camera px, then in model-input px) of all 68 frames: width,height')
W=[];H=[]
for r in rows:
    for b in r['boxes']:
        W.append(b['box'][2]-b['box'][0]);H.append(b['box'][3]-b['box'][1])
W=np.array(W);H=np.array(H)
print(len(W),'boxes; width camera px min/med/max',W.min(),np.median(W),W.max(),'height',H.min(),np.median(H),H.max(),'; model px width',round(W.min()*.7),round(np.median(W)*.7),round(W.max()*.7))
