# The comparison of compare_board.py with a tolerance on the score (float32 vs double) and exact pixels on boxes, and
# a from-scratch decode written against dl_detect_espdet_postprocessor.cpp / dl_detect_postprocessor.cpp (float32 arithmetic),
# and timing of the model step alone, and the bound on the 20-bit run (compare_board.py ... sat): the largest sum any
# convolution reaches, bias included, before the shift, against the [-2^19, 2^19-1] that run holds it to.
import sys,glob,json,os,time,math
import numpy as np
sys.path.insert(0,'.')
from interp import Model,decode,rshift_round
MODEL,REC=sys.argv[1],sys.argv[2]
m=Model(MODEL)
F32=np.float32
def f32_decode(T,exp,thr=F32(0.10),nms=F32(0.70),topk=10):
    res=[]
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
tot_boxes=0; ok_px=0; ok_sc=0; ok_frames=0; ok_f32=0; tm=[]; stages={0:0,1:0,2:0}; sess={}
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
    f32_out=f32_decode(o,m.exp)
    rb=[(tuple(b['box']),b['score']) for b in rec['boxes']]
    ob=[(tuple(r['box']),r['score']) for r in out]
    mb=[(tuple(r['box']),r['score']) for r in f32_out]
    tot_boxes+=len(rb)
    # order-preserving comparison: record's list order versus decode order
    same_order=[a[0] for a in rb]==[a[0] for a in ob]
    s_rec=rec['score']; s_computed=max([r['score'] for r in out],default=0.0)
    sok=abs(s_rec-s_computed)<1e-6
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
    f32_ok=sorted((tuple(a[0]),round(a[1],9)) for a in mb)==sorted((tuple(a[0]),round(a[1],9)) for a in rb)
    ok_f32+=f32_ok
    for r in out: stages[r['stage']]+=1
    if not(allgood and sok and f32_ok and same_order):
        bad.append((os.path.basename(st),sok,allgood,f32_ok,same_order,rb,ob,mb))
    k=os.path.basename(os.path.dirname(f)); sess.setdefault(k,[0,0]); sess[k][0]+=1; sess[k][1]+=len(rb)
print('score within 1e-6:',ok_sc,'of',len(files))
print('boxes in records:',tot_boxes,'; matched to the pixel and within 1e-6 in score:',ok_px)
print('frames with identical box lists (pixels, score within 1e-6):',ok_frames,'of',len(files))
print('from-scratch float32 decode equal to the record exactly (score to 9 decimals):',ok_f32,'of',len(files))
print('boxes by detection layer in interp decode (0 fine,1 mid,2 coarse):',stages)
print('frames/boxes by session:',sess)
print('model hash in records:',hash_set)
tm=np.array(tm); print('seconds per frame: network median %.4f mean %.4f min %.4f; decode median %.5f'%(np.median(tm[:,0]),tm[:,0].mean(),tm[:,0].min(),np.median(tm[:,1])))
print('frames with any difference:',len(bad))
for b in bad: print(b)
# The bound on the 20-bit run: interp.py's convolution (Model.conv) unchanged but for measuring each sum, bias included,
# before the shift, the value that run holds to [-2^19, 2^19-1]: the largest in magnitude, and how many are 2^19 or more
# in magnitude. Its output must equal interp.py's on every input.
class SumModel(Model):
    def __init__(self,path,qacc_sat=False):
        super().__init__(path,qacc_sat); self.largest=0; self.beyond=0
    def conv(self,nd,x):
        at=nd['at']; sy,sx=at['strides']; pt,pl,pb,pr=at['pads']; w=nd['w']; kh,kw=w.shape[:2]
        xp=np.pad(x,((pt,pb),(pl,pr),(0,0)))
        OH=(x.shape[0]+pt+pb-kh)//sy+1; OW=(x.shape[1]+pl+pr-kw)//sx+1
        dep=at['group']!=1; N=x.shape[2] if dep else w.shape[3]
        acc=np.zeros((OH,OW,N),dtype=np.int64)
        for i in range(kh):
            for j in range(kw):
                sl=xp[i:i+sy*(OH-1)+1:sy,j:j+sx*(OW-1)+1:sx,:]
                acc+=(sl*w[i,j]) if dep else (sl@w[i,j])
        if nd['bias'] is not None: acc+=nd['bias']
        a=np.abs(acc); self.largest=max(self.largest,int(a.max())); self.beyond+=int((a>=(1<<19)).sum())
        if self.qacc_sat: acc=np.clip(acc,-(1<<19),(1<<19)-1)
        out=np.clip(rshift_round(acc,nd['mac_shift']),-128,127)
        if at['activation']=='Relu': out=np.maximum(out,0)
        else: assert at['activation']=='Linear',at['activation']
        return out
sm=SumModel(MODEL)
for f in files:
    x=np.fromfile(f,dtype=np.int8).reshape(224,224,3)
    T=m.run(x); T2=sm.run(x)
    assert T.keys()==T2.keys() and all(np.array_equal(T[k],T2[k]) for k in T),f
print('convolution sums, bias included, before the shift, which the 20-bit run holds to [-2^19, 2^19-1]: largest in magnitude',sm.largest,'; at or beyond 2^19 =',1<<19,'in magnitude:',sm.beyond)
