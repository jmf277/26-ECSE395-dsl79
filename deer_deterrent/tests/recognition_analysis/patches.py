import sys; sys.path.insert(0,'.')
import numpy as np
from PIL import Image,ImageDraw
from exp_util import *
fr=[('D1 f6',6),('D2 f18',18),('D3 f22',22),('N1 f32',32),('empty f56',56),('D1 f60',60)]
T=[]
for n,f in fr:
    x=load_input(stem(f)); p=from_i8(x[168:196,143:208]).astype(np.uint8)
    im=Image.fromarray(p).resize((65*5,28*5),Image.NEAREST); d=ImageDraw.Draw(im); d.text((2,2),n,fill=(255,255,0)); T.append(im)
S=Image.new('RGB',(3*325,2*140))
for i,t in enumerate(T): S.paste(t,((i%3)*325,(i//3)*140))
S.save('results/patches.png'); print(S.size)
