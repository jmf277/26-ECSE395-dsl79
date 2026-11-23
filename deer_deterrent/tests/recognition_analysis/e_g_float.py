# G. Same stored weights, but activations in ordinary decimal numbers: no rounding, no saturation.
# It is NOT best.pt: the weights themselves are the 8-bit ones.  It isolates only what the 8-bit *activations* do.
import sys,glob,os,json; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
m=model()
def run_float(x):
    T={m.inputs[0]:x.astype(np.float64)*2.0**m.exp[m.inputs[0]]}
    sat={}
    for nd in m.nodes:
        op=nd['op']
        if op=='Conv':
            at=nd['at']; sy,sx=at['strides']; pt,pl,pb,pr=at['pads']
            w=nd['w']*2.0**m.init[nd['ins'][1]]['exp']; kh,kw=w.shape[:2]
            a=T[nd['ins'][0]]
            xp=np.pad(a,((pt,pb),(pl,pr),(0,0)))
            OH=(a.shape[0]+pt+pb-kh)//sy+1; OW=(a.shape[1]+pl+pr-kw)//sx+1
            dep=at['group']!=1; N=a.shape[2] if dep else w.shape[3]
            acc=np.zeros((OH,OW,N))
            for i in range(kh):
                for j in range(kw):
                    sl=xp[i:i+sy*(OH-1)+1:sy,j:j+sx*(OW-1)+1:sx,:]
                    acc+=(sl*w[i,j]) if dep else (sl@w[i,j])
            if nd['bias'] is not None: acc+=nd['bias']*2.0**m.init[nd['ins'][2]]['exp']
            if at['activation']=='Relu': acc=np.maximum(acc,0)
            T[nd['outs'][0]]=acc
        elif op=='RequantizeLinear': T[nd['outs'][0]]=T[nd['ins'][0]]
        elif op=='Add': T[nd['outs'][0]]=T[nd['ins'][0]]+T[nd['ins'][1]]
        elif op=='Concat': T[nd['outs'][0]]=np.concatenate([T[i] for i in nd['ins']],axis=2)
        elif op=='Split':
            a=T[nd['ins'][0]]; s=nd['sizes']; T[nd['outs'][0]]=a[:,:,:s[0]]; T[nd['outs'][1]]=a[:,:,s[0]:]
        elif op=='MaxPool':
            k=nd['at']['kernel_shape'][0]; p=nd['at']['pads'][0]
            v=np.pad(T[nd['ins'][0]],((p,p),(p,p),(0,0)),constant_values=-1e9)
            T[nd['outs'][0]]=np.lib.stride_tricks.sliding_window_view(v,(k,k),axis=(0,1)).max(axis=(3,4))
        elif op=='Resize': T[nd['outs'][0]]=T[nd['ins'][0]].repeat(2,axis=0).repeat(2,axis=1)
    return T
def sat_stats(x):
    T=m.run(x); res={}
    tot=hi=lo=0
    for nd in m.nodes:
        if nd['op'] in('Conv','Add','RequantizeLinear'):
            a=T[nd['outs'][0]]; 
            n=a.size; h=int((a==127).sum()); l=int((a==-128).sum()); tot+=n; hi+=h; lo+=l
            if (h+l)/n>0.02: res[nd['name']]=round((h+l)/n,3)
    return tot,hi,lo,res,T
rows=[]
print('frame | int8 logit top head0 | float logit (same cell) | float max head0 | int8 score | float score | float max head1 head2 | share of activations pinned at +127/-128')
fr_list=[1,3,6,18,22,32,41,50,51,56,57,58,60,62,63,64,68]
sigm=lambda v:float(1/(1+np.exp(-v)))
allrows=[]
for fr in range(1,69):
    x=load_input(stem(fr)); F=run_float(x)
    tot,hi,lo,res,T=sat_stats(x)
    q=T['score0'][...,0]*0.25; idx=np.unravel_index(q.argmax(),q.shape)
    f0=F['score0'][...,0]
    r=dict(frame=fr,int_top=float(q.max()),float_same=float(f0[idx]),float_max=float(f0.max()),f1=float(F['score1'].max()),f2=float(F['score2'].max()),i1=float(T['score1'].max()*0.5),i2=float(T['score2'].max()*0.5),pin=(hi+lo)/tot,hi=hi,lo=lo,top_layers=res)
    allrows.append(r)
    if fr in fr_list:
        print(fr,'|',r['int_top'],'|',round(r['float_same'],3),'|',round(r['float_max'],3),'|',round(sigm(r['int_top']),3),'|',round(sigm(r['float_max']),3),'|',round(r['f1'],2),round(r['f2'],2),'| int8',r['i1'],r['i2'],'| pinned',round(100*r['pin'],3),'%',res)
json.dump(allrows,open('results/e_g_float.json','w'))
d=np.array([r['float_same']-r['int_top'] for r in allrows]); dm=np.array([r['float_max']-r['int_top'] for r in allrows])
print('float minus int8 at top cell: mean',round(d.mean(),3),'min',round(d.min(),3),'max',round(d.max(),3),'; float max minus int8 max: mean',round(dm.mean(),3),'min',round(dm.min(),3),'max',round(dm.max(),3))
print('largest float head0 score in 68 frames',round(sigm(max(r['float_max'] for r in allrows)),3),' largest float head1',round(sigm(max(r['f1'] for r in allrows)),3),' largest float head2',round(sigm(max(r['f2'] for r in allrows)),6))
print('pinned activations share: mean %.3f%% max %.3f%%'%(100*np.mean([r['pin'] for r in allrows]),100*max(r['pin'] for r in allrows)))
# per-trial comparison: top logit
import collections
print('trial: mean top logit int8 | float')
tr={1:(1,10),2:(11,20),3:(21,30),4:(31,40),5:(41,50),'6 empty':(51,56),'6 D1':(57,62),'6 empty after':(63,68)}
for k,(a,b) in tr.items():
    sel=[r for r in allrows if a<=r['frame']<=b]
    print(k,round(np.mean([r['int_top'] for r in sel]),3),round(np.mean([r['float_max'] for r in sel]),3))
# final-layer biases of the score heads, in logit units, and exponents
for s,nm in ((0,'model.23.cv3.0.2'),(1,'model.23.cv3.1.2'),(2,'model.23.cv3.2.2')):
    b=m.init[nm+'.bias']; w=m.init[nm+'.weight']
    bv=np.frombuffer(b['raw'],dtype=np.int32,count=1)[0]*2.0**b['exp']
    wv=np.frombuffer(w['raw'],dtype=np.int8,count=int(np.prod(w['dims'])))*2.0**w['exp']
    print('head',s,nm,'bias in logit units',round(float(bv),3),'weights dims',w['dims'],'exp',w['exp'],'sum abs',round(float(np.abs(wv).sum()),2),'max',round(float(wv.max()),3),'min',round(float(wv.min()),3))
# does any bias exceed the 20-bit lane the S3 keeps?
big=[(k,int(np.abs(np.frombuffer(v['raw'],dtype=np.int32,count=int(np.prod(v['dims'])))).max())) for k,v in m.init.items() if v['dt']==6]
print('largest |bias| integer',max(b for _,b in big),'; 2^19 =',2**19,'; biases beyond 2^19:',sum(b>=2**19 for _,b in big))
