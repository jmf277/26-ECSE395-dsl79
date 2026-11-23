import sys,glob,json,os
import numpy as np
sys.path.insert(0,'.')
from interp import Model,decode
# paths relative to tests/recognition_analysis, the folder every script is run from
MODEL='../../experiments/deer-classifier/model/deer.espdl'
REC='../recognition'
MAIN=REC+'/p3-demo-20261104-02'
_m=None
def model():
    global _m
    if _m is None: _m=Model(MODEL)
    return _m
def stem(fr,folder=MAIN):
    return [x for x in glob.glob(folder+f'/*-frame-{fr:06d}.json')][0][:-5]
def load_input(st): return np.fromfile(st+'.input.i8',dtype=np.int8).reshape(224,224,3)
def load_rec(st): return json.load(open(st+'.json'))
def forward(x):
    m=model(); T=m.run(x)
    return {k:T[k] for k in m.outputs}
def boxes(out,floor=0.10):
    return decode(out,model().exp,floor=floor)
def head_max(out):
    m=model(); r={}
    for s in range(3):
        sc=out[f'score{s}'][...,0]; e=m.exp[f'score{s}']
        q=int(sc.max()); idx=np.unravel_index(sc.argmax(),sc.shape)
        r[s]=dict(q=q,logit=q*2.0**e,prob=float(1/(1+np.exp(-q*2.0**e))),cell=tuple(int(i) for i in idx))
    return r
def score(out):
    b=boxes(out); return max([r['score'] for r in b],default=0.0)
def top(out):
    b=boxes(out)
    return max(b,key=lambda r:r['score']) if b else None
def to_i8(rgb224):
    """uint8 RGB (224x224x3, already letterboxed) -> firmware int8 input: round(v/255*128), clip 127"""
    v=np.round(rgb224.astype(np.float64)/255.0*128.0)
    return np.clip(v,-128,127).astype(np.int8)
def from_i8(x):
    return np.clip(x.astype(np.float64)/128.0*255.0,0,255)
def firmware_resize(rgb):
    """ESP-DL: nearest with floor(i*inverse_scale), centered letterbox 114. rgb = HxWx3 uint8"""
    H,W=rgb.shape[:2]
    scale=min(np.float32(224)/np.float32(W),np.float32(224)/np.float32(H))
    cw=int(np.float32(scale)*np.float32(W)); ch=int(np.float32(scale)*np.float32(H))
    ix=np.float32(W)/np.float32(cw); iy=np.float32(H)/np.float32(ch)
    xs=np.minimum((np.arange(cw,dtype=np.float32)*ix).astype(int),W-1)
    ys=np.minimum((np.arange(ch,dtype=np.float32)*iy).astype(int),H-1)
    out=np.full((224,224,3),114,dtype=np.uint8)
    l=(224-cw)//2; t=(224-ch)//2
    out[t:t+ch,l:l+cw]=rgb[ys[:,None],xs[None,:]]
    return out
