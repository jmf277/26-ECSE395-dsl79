import sys; sys.path.insert(0,".")
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
