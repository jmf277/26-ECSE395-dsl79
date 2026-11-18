import sys,json,glob,re,math; sys.path.insert(0,'.')
import numpy as np
from PIL import Image
from exp_util import *
r=json.load(open('results/compare_board_plain.json'))
W=[];H=[]
for x in r:
    for s,b in x['rec_boxes']:
        W.append(b[2]-b[0]);H.append(b[3]-b[1])
W=np.array(W);H=np.array(H)
print('all 69 boxes: width camera px',W.min(),np.median(W),W.max(),'= share of frame width',round(W.min()/320,3),round(np.median(W)/320,3),round(W.max()/320,3),'; height',H.min(),np.median(H),H.max())
# floors per head
m=model()
for s in range(3):
    e=m.exp[f'score{s}']; q=int(np.floor(math.log(.1/.9)/2.0**e+.5)); print('head',s,'stride',8<<s,'cells',28>>s,'exponent',e,'step',2.0**e,'floor q',q,'floor score',round(1/(1+math.exp(-q*2.0**e)),4),'first >=0.85: q',math.ceil(math.log(.85/.15)/2.0**e),'score',round(1/(1+math.exp(-math.ceil(math.log(.85/.15)/2.0**e)*2.0**e)),4))
print('box exponents',[m.exp[f'box{s}'] for s in range(3)],'input exponent',m.exp['images'])
# 29 Oct: which scores can only be the finest head (odd multiples of 0.25)
fb=json.load(open('../../experiments/deer-classifier/test-results/full-body-test.json')); cu=json.load(open('../../experiments/deer-classifier/test-results/close-up-test.json'))
def k(s): return round(math.log(s/(1-s))/0.25)
for name,d in (('full',fb),('close',cu)):
    nz=[o['score'] for o in d['observations'] if o['score']>0]
    odd=sum(k(s)%2==1 for s in nz)
    print(name,'non-zero',len(nz),'odd k (finest head only)',odd,'even k',len(nz)-odd, sorted(set(k(s) for s in nz)))
# the float model's own evaluation at 0.25, best.pt and last.pt: TP, FN, FP, TN as the training project's record gives them,
# deer_classifier/EVALUATION.md in the model's repository, F26-ECSE395-ams926 (linked from ../../SOURCES.md)
tp,fn,fp,tn=175,342,14,71
rec=tp/(tp+fn); fpr=fp/(fp+tn); print('best .25: recall',round(rec,4),'fpr',round(fpr,4),'LR+',round(rec/fpr,2),'precision if deer and non-deer equally common',round(rec/(rec+fpr),3))
tp,fn,fp,tn=182,335,9,76
rec=tp/(tp+fn); fpr=fp/(fp+tn); print('last .25: recall',round(rec,4),'fpr',round(fpr,4),'LR+',round(rec/fpr,2),'precision if equal',round(rec/(rec+fpr),3))
# neutralize the room's purple cast on the ambient frame of set-up session 08 (D1 held about 9 in from the lens)
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
im=np.asarray(Image.open(S08+'.jpg').convert('RGB')).astype(float)
crop=im[60:151,120:231]
gain=crop.reshape(-1,3).mean(0); gain=gain.mean()/gain
print('frame s08 picture-area mean RGB',crop.reshape(-1,3).mean(0).round(1),'gains',gain.round(2))
for name,a in (('as saved',im),('gray-world balanced',np.clip(im*gain,0,255)),('balanced, brighter x1.5',np.clip(im*gain*1.5,0,255))):
    o=forward(to_i8(firmware_resize(a.astype(np.uint8)))); print(name,[(round(b['score'],3),b['box']) for b in boxes(o)][:5])
# the ceiling light painted out of s08, balanced and brightened
x=to_i8(firmware_resize(np.clip(im*gain*1.5,0,255).astype(np.uint8))).copy()
x[168:196,140:210]=np.round(x[160:168,140:210].reshape(-1,3).mean(0)).astype(np.int8)
o=forward(x); print('balanced, brighter, light painted out',[(round(b['score'],3),b['box']) for b in boxes(o)][:5])
