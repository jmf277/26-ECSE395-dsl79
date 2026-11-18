# The comparison of compare_board.py with a tolerance on the score (float32 vs double) and exact pixels on boxes, and
# a from-scratch decode written against dl_detect_espdet_postprocessor.cpp / dl_detect_postprocessor.cpp (float32 arithmetic),
# and timing of the model step alone.
import sys,glob,json,os,time,math
import numpy as np
sys.path.insert(0,'.')
from interp import Model,decode
MODEL,REC=sys.argv[1],sys.argv[2]
m=Model(MODEL)
F32=np.float32
def my_decode(T,exp,thr=F32(0.10),nms=F32(0.70),topk=10):
    res=[]
    inv=F32(320)/F32(168)*F32(168)/F32(224)  # placeholder not used
    inv_x=F32(320)/F32(224); inv_y=F32(240)/F32(168)
    for st,sd in enumerate((8,16,32)):
        sc=T[f'score{st}']; bx=T[f'box{st}']
        se=F32(1.0)/F32(1<<(-exp[f'score{st}'])); be=F32(1.0)/F32(1<<(-exp[f'box{st}']))
        v=F32(-math.log(float(F32(1.0/float(thr)-1)))) # -logf(1/x -1)
        q_thr=int(np.floor(F32(v*(F32(1.0)/se))+F32(0.5)))
        H,W=sc.shape[:2]
        for y in range(H):
            for x in range(W):
                q=int(sc[y,x,0])
                if q>=q_thr:
                    cy=y*sd+sd//2; cx=x*sd+sd//2
                    d=[F32(int(bx[y,x,i]))*be for i in range(4)]
                    s=F32(1.0/(1.0+float(F32(math.exp(-float(F32(q)*se))))))
                    box=[int((F32(cx)-d[0]*F32(sd)-F32(0))*inv_x),int((F32(cy)-d[1]*F32(sd)-F32(28))*inv_y),int((F32(cx)+d[2]*F32(sd)-F32(0))*inv_x),int((F32(cy)+d[3]*F32(sd)-F32(28))*inv_y)]
                    res.append(dict(score=float(s),box=box,stage=st))
    res.sort(key=lambda r:-r['score'])
    i=0;n=0
    while i<len(res):
        n+=1
        if n>=topk:
            res=res[:i+1]; break
        a=res[i]['box']; aa=(a[2]-a[0]+1)*(a[3]-a[1]+1)
        j=i+1
        while j<len(res):
            b=res[j]['box']
            ih=min(a[3],b[3])-max(a[1],b[1])+1; iw=min(a[2],b[2])-max(a[0],b[0])+1
            if ih>0 and iw>0:
                ba=(b[2]-b[0]+1)*(b[3]-b[1]+1); ia=ih*iw
                if F32(ia/(aa+ba-ia))>nms:
                    res.pop(j); continue
            j+=1
        i+=1
    for r in res:
        b=r['box']; b[0]=min(max(b[0],0),319); b[1]=min(max(b[1],0),239); b[2]=min(max(b[2],0),319); b[3]=min(max(b[3],0),239)
    return res
files=sorted(glob.glob(REC+'/*/*.input.i8'))
print(len(files),'saved inputs')
tot_boxes=0; ok_px=0; ok_sc=0; ok_frames=0; ok_my=0; tm=[]; stages={0:0,1:0,2:0}; sess={}
hash_set=set()
bad=[]
for f in files:
    st=f[:-len('.input.i8')]
    rec=json.load(open(st+'.json'))
    hash_set.add(rec.get('model_sha256'))
    x=np.fromfile(f,dtype=np.int8).reshape(224,224,3)
    t0=time.perf_counter(); T=m.run(x); t1=time.perf_counter()
    o={k:T[k] for k in m.outputs}
    out=decode(o,m.exp); t2=time.perf_counter()
    tm.append((t1-t0,t2-t1))
    mine=my_decode(o,m.exp)
    rb=[(tuple(b['box']),b['score']) for b in rec['boxes']]
    ob=[(tuple(r['box']),r['score']) for r in out]
    mb=[(tuple(r['box']),r['score']) for r in mine]
    tot_boxes+=len(rb)
    # order-preserving comparison: record's list order versus decode order
    same_order=[a[0] for a in rb]==[a[0] for a in ob]
    s_rec=rec['score']; s_me=max([r['score'] for r in out],default=0.0)
    sok=abs(s_rec-s_me)<1e-6
    ok_sc+=sok
    # match boxes as multisets by pixels + score within 1e-6
    rem=list(ob); good=0
    for b,s in rb:
        hit=[o2 for o2 in rem if o2[0]==b and abs(o2[1]-s)<1e-6]
        if hit: good+=1; rem.remove(hit[0])
    ok_px+=good
    allgood=(good==len(rb) and not rem)
    ok_frames+=allgood
    # float32 exact equality of the from-scratch decode
    myok=sorted((tuple(a[0]),round(a[1],9)) for a in mb)==sorted((tuple(a[0]),round(a[1],9)) for a in rb)
    ok_my+=myok
    for r in out: stages[r['stage']]+=1
    if not(allgood and sok and myok and same_order):
        bad.append((os.path.basename(st),sok,allgood,myok,same_order,rb,ob,mb))
    k=os.path.basename(os.path.dirname(f)); sess.setdefault(k,[0,0]); sess[k][0]+=1; sess[k][1]+=len(rb)
print('score within 1e-6:',ok_sc,'of',len(files))
print('boxes in records:',tot_boxes,'; matched to the pixel and within 1e-6 in score:',ok_px)
print('frames with identical box lists (pixels, score within 1e-6):',ok_frames,'of',len(files))
print('from-scratch float32 decode equal to the record exactly (score to 9 decimals):',ok_my,'of',len(files))
print('boxes by detection layer in interp decode (0 fine,1 mid,2 coarse):',stages)
print('frames/boxes by session:',sess)
print('model hash in records:',hash_set)
tm=np.array(tm); print('seconds per frame: network median %.4f mean %.4f min %.4f; decode median %.5f'%(np.median(tm[:,0]),tm[:,0].mean(),tm[:,0].min(),np.median(tm[:,1])))
print('frames with any difference:',len(bad))
for b in bad: print(b)
