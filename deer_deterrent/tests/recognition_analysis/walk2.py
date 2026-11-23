import sys,collections
sys.path.insert(0,'.')
from fbs import *
data,b,root=open_espdl(sys.argv[1])
g=root.table(7)
print('graph name',g.string(1))
nodes=g.vec_tables(0)
print('first node fields',nodes[0].fields(),nodes[0].nfields)
n=nodes[0]
for f in n.fields():
    print(f,n.off(f))
print('inputs',n.vec_strings(0)); print('outputs',n.vec_strings(1)); print('name',n.string(2)); print('op',n.string(3)); print('field4',n.string(4) if n.has(4) else None)
at=n.vec_tables(5) if n.has(5) else []
print('attrs',len(at))
for a in at:
    print(' attr fields',a.fields(),a.nfields,[ (i,a.off(i)) for i in range(a.nfields)],'name',a.string(0))
ops=collections.Counter(x.string(3) for x in nodes)
print(ops)
inp=g.vec_tables(4)[0]
print('input fields',inp.fields(),[ (i,inp.off(i)) for i in range(inp.nfields)],inp.string(0))
out=g.vec_tables(5)
for o in out:
    print('output',o.string(0),o.fields(),[ (i,o.off(i)) for i in range(o.nfields)])
vi=g.vec_tables(6)
print('valueinfo sample',vi[0].string(0),vi[0].fields(),[ (i,vi[0].off(i)) for i in range(vi[0].nfields)])
ini=g.vec_tables(2)
t=ini[0]
print('initializer fields',t.fields(),[ (i,t.off(i)) for i in range(t.nfields)])
