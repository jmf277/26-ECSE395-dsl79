# Two-of-three replay over the frame records of the main session, using the exact logic of temporal_gate.hpp (experiments/deer-classifier/src),
# history cleared at each new trial; levels include 0.09 (below the firmware's allowed 10-100 %). Arithmetic only: no board run.
import json,glob,os
REC='../recognition'
M=REC+'/p3-demo-20261104-02'
rows=[]
for js in sorted(glob.glob(M+'/*-frame-*.json')):
    if js.endswith('.received.json'): continue
    r=json.load(open(js)); rows.append(r)
print(len(rows),'frames; keys sample',sorted(rows[0].keys())[:40])
print('frame numbers in file',rows[0]['frame'],rows[-1]['frame'],'trials',sorted(set(r['trial'] for r in rows)),'triggers',sorted(set(r.get('trigger') for r in rows)))
# running frame number across the session: position in file order
for i,r in enumerate(rows,1): r['n']=i
assert all(r['frame']==r['n'] for r in rows) or print('frame field not running; using file order')
def onlight(b): return b[0]>=200 and b[1]>=195
def phase(n):
    return 't1 D1 5in' if n<=10 else 't2 D2' if n<=20 else 't3 D3' if n<=30 else 't4 N1 dog' if n<=40 else 't5 D1 2.5in' if n<=50 else 't6 empty before' if n<=56 else 't6 D1' if n<=62 else 't6 empty after'
def gate(seq,thr):
    h=0; first=None; conf=[]
    for n,s in seq:
        h=((h<<1)|(1 if s>=thr else 0))&7
        hits=bin(h).count('1')
        if hits>=2:
            conf.append(n)
            if first is None: first=n
    return first,conf
def score_all(r): return r['score']
def score_nonlight(r): return max([b['score'] for b in r['boxes'] if not onlight(b['box'])],default=0.0)
def score_light(r): return max([b['score'] for b in r['boxes'] if onlight(b['box'])],default=0.0)
levels=[0.85,0.60,0.55,0.51,0.50,0.45,0.44,0.43,0.40,0.38,0.35,0.33,0.32,0.30,0.27,0.26,0.25,0.22,0.20,0.18,0.15,0.14,0.12,0.11,0.10,0.09]
print('\nlevel | first confirming frame per trial using each frame\'s score (all boxes)')
for lv in levels:
    res={}
    for t in sorted(set(r['trial'] for r in rows)):
        sel=[r for r in rows if r['trial']==t]
        f,conf=gate([(r['n'],score_all(r)) for r in sel],lv)
        if f: 
            # which box carries the hits on the confirming frames?
            res[t]=(f,len(conf))
    print(lv,res)
print('\nlevel | same, counting ONLY boxes NOT on the light (deer-located boxes)')
for lv in levels:
    res={}
    for t in sorted(set(r['trial'] for r in rows)):
        sel=[r for r in rows if r['trial']==t]
        f,conf=gate([(r['n'],score_nonlight(r)) for r in sel],lv)
        if f: res[t]=(f,conf)
    print(lv,res)
print('\nlevel | same, counting ONLY light boxes')
for lv in levels:
    res={}
    for t in sorted(set(r['trial'] for r in rows)):
        sel=[r for r in rows if r['trial']==t]
        f,conf=gate([(r['n'],score_light(r)) for r in sel],lv)
        if f: res[t]=(f,len(conf))
    print(lv,res)
print('\ntrial 6 replay split by phase as if each phase were its own window (D1 phase only):')
for lv in levels:
    sel=[r for r in rows if 57<=r['n']<=62]
    print(lv,'D1 phase all boxes',gate([(r['n'],score_all(r)) for r in sel],lv),'| D1 phase deer-located boxes',gate([(r['n'],score_nonlight(r)) for r in sel],lv))
# frame list for hits at the floor
print('\nframes with non-light boxes:',[(r['n'],phase(r['n']),[(round(b['score'],4),b['box']) for b in r['boxes'] if not onlight(b['box'])]) for r in rows if any(not onlight(b['box']) for b in r['boxes'])])
# deer versus non-deer comparison
deer=[r for r in rows if phase(r['n']) in('t1 D1 5in','t2 D2','t3 D3','t5 D1 2.5in','t6 D1')]
non=[r for r in rows if r not in deer]
def auc(a,b):
    gt=sum(x>y for x in a for y in b); eq=sum(x==y for x in a for y in b); n=len(a)*len(b)
    return gt/n,eq/n,(gt+.5*eq)/n
print('\nall boxes: strict P(deer>non), ties, P+ties/2',auc([score_all(r) for r in deer],[score_all(r) for r in non]),'n',len(deer),len(non))
print('light ignored: strict, ties, P+ties/2',auc([score_nonlight(r) for r in deer],[score_nonlight(r) for r in non]),'deer frames with non-light box',sum(score_nonlight(r)>0 for r in deer),'non-deer',sum(score_nonlight(r)>0 for r in non))
print('board flags: present any',any(r['present'] for r in rows),'alarm_started any',any(r['alarm_started'] for r in rows),'thresholds',set(r['threshold'] for r in rows),'window',set(r.get('window') for r in rows),'required',set(r.get('required') for r in rows))
print('max score armed',max(r['score'] for r in rows if r['armed']),'max score',max(r['score'] for r in rows),'best box on deer',max(score_nonlight(r) for r in deer))
# ladder and rungs
import math
ks=sorted(set(round(math.log(b['score']/(1-b['score']))/0.25) for r in rows for b in r['boxes']))
print('k on boxes in main session',ks)
