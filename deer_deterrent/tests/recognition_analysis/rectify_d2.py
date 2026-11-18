import sys; sys.path.insert(0,'.')
import numpy as np
from PIL import Image
im=Image.open('inputs/IMG_6710.png').convert('RGB')
print(im.size)
s=4032/1600
# corners read from the 1600-px view of the photograph (print is upside down in the photo)
A,B,C,D=[(845,15),(1255,175),(1085,845),(815,680)]
A,B,C,D=[(x*s,y*s) for x,y in (A,B,C,D)]
W,H=500,560
# output corners: (0,0)=C (photo bottom right -> upright top left), (W,0)=D, (W,H)=A, (0,H)=B
dst=[(0,0),(W,0),(W,H),(0,H)]; src=[C,D,A,B]
def coeffs(dst,src):
    M=[]
    for (x,y),(u,v) in zip(dst,src):
        M.append([x,y,1,0,0,0,-u*x,-u*y]); M.append([0,0,0,x,y,1,-v*x,-v*y])
    return np.linalg.solve(np.array(M,float),np.array(src,float).reshape(8))
out=im.transform((W,H),Image.PERSPECTIVE,coeffs(dst,src),Image.BICUBIC)
out.save('results/d2_rectified.png'); print(out.size)
