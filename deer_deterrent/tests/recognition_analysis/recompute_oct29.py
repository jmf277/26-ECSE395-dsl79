import json,re,statistics as st,math,sys
fb=json.load(open('../../experiments/deer-classifier/test-results/full-body-test.json')); cu=json.load(open('../../experiments/deer-classifier/test-results/close-up-test.json'))
log=open('../../experiments/deer-classifier/test-results/startup-log.txt',encoding='utf-8',errors='replace').read()
print({k:fb[k] for k in fb if k!='observations'}); print({k:cu[k] for k in cu if k!='observations'})
for name,d in (('full',fb),('close',cu)):
    s=[o['score'] for o in d['observations']]
    print(name,'n',len(s),'first',d['observations'][0]['frame'],'last',d['observations'][-1]['frame'],'mean',round(st.mean(s),7),'median',st.median(s),'max',max(s),'argmax frames',[o['frame'] for o in d['observations'] if o['score']==max(s)],'zeros',s.count(0.0),'>=.25',sum(x>=.25 for x in s),'>=.85',sum(x>=.85 for x in s),'present',sum(o['present'] for o in d['observations']))
# parse complete frame lines from the log
pat=re.compile(r'FRAME n=(\d+) score=([\d.]+) present=(\d) capture_ms=([\d.]+) decode_ms=([\d.]+) infer_ms=([\d.]+) total_ms=([\d.]+) psram_free=(\d+)')
cut=re.compile(r'FRAME n=(\d+) score=([\d.]+) present=(\d) capture_ms=([\d.]+) decode_ms=([\d.]*)')
rows=[(int(a),float(b),int(c),float(d_),float(e),float(f),float(g),int(h)) for a,b,c,d_,e,f,g,h in pat.findall(log)]
print('complete lines',len(rows))
by={}
seq=[]
for r in rows: seq.append(r[0])
print('frame number runs:',end=' ')
start=seq[0];prev=seq[0];runs=[]
for x in seq[1:]:
    if x!=prev+1: runs.append((start,prev)); start=x
    prev=x
runs.append((start,prev)); print(runs)
allm=cut.findall(log); print('all readable (incl cut)',len(allm))
print('cut lines:',[m for m in re.findall(r'FRAME n=(\d+) score=([\d.]+) present=\d capture_ms=[\d.]+ decode_ms=[\d.]*FRAME',log)], [m for m in re.findall(r'(FRAME n=\d+ score=[\d.]+ present=\d capture_ms=[\d.]+ decode_ms=[\d.]+)FRAME',log)])
# compare JSON with log for the two looks
lg={r[0]:r for r in rows[9:]}
for name,d in (('full',fb),('close',cu)):
    diff=[o['frame'] for o in d['observations'] if lg[o['frame']][1]!=o['score']]
    print(name,'json vs log mismatch',diff)
    tot=[lg[o['frame']] for o in d['observations']]
    print(name,'mean capture',round(st.mean(t[3] for t in tot),3),'decode',round(st.mean(t[4] for t in tot),3),'infer',round(st.mean(t[5] for t in tot),3),'total',round(st.mean(t[6] for t in tot),3),'psram',set(t[7] for t in tot))
# distinct nonzero scores and ladder
all_s=sorted(set(float(x[1]) for x in allm))
print('distinct',all_s)
print('ladder k*0.25 sigmoid',[ (k,round(1/(1+math.exp(-k*0.25)),4)) for k in range(-10,9)])
# gate replay
def gate(scores,thr):
    h=[];first=None;conf=0
    for i,s in enumerate(scores,1):
        h.append(s>=thr);h=h[-3:]
        if sum(h)>=2:
            conf+=1
            if first is None:first=i
    return first,conf
for thr in (0.85,0.5,0.4,0.35,0.3,0.25,0.2,0.15,0.1):
    print('thr',thr,'full',gate([o['score'] for o in fb['observations']],thr),'close',gate([o['score'] for o in cu['observations']],thr))
# early frames, second start
second=[m for m in allm][9:]
e1=[float(m[1]) for m in second if 1<=int(m[0])<=19]; e2=[float(m[1]) for m in second if 51<=int(m[0])<=84]
print('early 1-19',len(e1),'zeros',e1.count(0.0),'mean',round(st.mean(e1),4),'max',max(e1)); print('early 51-84',len(e2),'zeros',e2.count(0.0),'mean',round(st.mean(e2),4),'max',max(e2))
others=[float(m[1]) for m in allm if not(275<=int(m[0])<=307 or 409<=int(m[0])<=441)]
print('others n',len(others),'>=.2',sum(x>=.2 for x in others),'max',max(others))
print('first-start frames 1-9',[float(m[1]) for m in allm[:9]])
print('frame 308 score',[m[1] for m in allm if m[0]=='308'])
