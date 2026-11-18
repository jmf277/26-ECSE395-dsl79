import sys
sys.path.insert(0,'.')
from fbs import *
data,b,root=open_espdl(sys.argv[1])
print('magic',data[:4],'bytes4-8',data[4:8].hex(),'len',len(data))
print('root fields',root.fields(),'vsize',root.vsize,'tsize',root.tsize)
print('field0 int',root.i32(0),'field7 graph present',root.has(7))
for f in range(root.nfields):
    print(f,root.off(f))
g=root.table(7)
print('graph fields',g.fields(),'nfields',g.nfields,[ (i,g.off(i)) for i in range(g.nfields)])
for f in g.fields():
    p,n=g.vec(f) if True else (None,0)
    print('field',f,'vec-len-guess',n)
