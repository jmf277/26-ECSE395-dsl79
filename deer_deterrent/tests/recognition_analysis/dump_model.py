"""Dump the whole graph of deer.espdl from the file's own bytes, using the field numbers of
ESP-DL's espdl.fbs (ESP-DL 3.3.14, the version the trial program builds with)."""
import sys,collections,json
sys.path.insert(0,'.')
from fbs import *
data,b,root=open_espdl(sys.argv[1])
g=root.table(7)
print('EDL2 header bytes 4-8 (not encrypted if zero):',data[4:8].hex(),'payload',len(data)-16)
print('ir_version',root.i32(0),'graph',g.string(1))
print('producer',root.string(2),root.string(3),'doc',root.string(6))
op=root.vec_tables(1)
print('opset',[(o.string(0),o.i64(1)) for o in op])
print('metadata_props fields',root.has(8))
print('test_inputs_value',g.vec(8)[1],'test_outputs_value',g.vec(9)[1])
vi={}
for src,fld in (('value',6),('in',4),('out',5)):
    for v in g.vec_tables(fld):
        vi[v.string(0)]=(src,v.vec_i64(3))
# ValueInfo shape: value_info_type (TypeInfo) -> value union
def shape_of(v):
    ti=v.table(1)
    if ti is None: return None
    # TypeInfo: value_type (union type byte, field 0), value (field 1), denotation
    tt=ti.table(1)
    if tt is None: return None
    sh=tt.table(1); et=tt.i32(0)
    if sh is None: return et,None
    dims=[]
    for d in sh.vec_tables(0):
        dv=d.table(0)
        dims.append(dv.i64(1) if dv is not None else None)
    return et,dims
print('--- inputs/outputs')
for fld,nm in ((4,'input'),(5,'output')):
    for v in g.vec_tables(fld):
        print(nm,v.string(0),'exponents',v.vec_i64(3),'type/shape',shape_of(v))
print('--- nodes')
inits={}
for t in g.vec_tables(2):
    inits[t.string(6)]=t
def attr(a):
    name=a.string(0); t=a.i32(3); r=None
    if a.has(5): r=a.b.i64(a.pos+a.off(5))
    if a.has(4): r=a.b.f32(a.pos+a.off(4))
    if a.has(6):
        p,n=a.vec(6); r=bytes(b.d[b.base+p:b.base+p+n]).decode('utf-8','replace')
    if a.has(11): r=a.vec_i64(11)
    if a.has(10):
        p,n=a.vec(10); r=[a.b.f32(p+4*i) for i in range(n)]
    return name,r
rows=[]
for i,n in enumerate(g.vec_tables(0)):
    ins=n.vec_strings(0); outs=n.vec_strings(1)
    at=dict(attr(a) for a in n.vec_tables(5)) if n.has(5) else {}
    line=f"{i:3d} {n.string(3):16s} {n.string(2)}"
    print(line)
    for x in ins:
        if x in inits:
            t=inits[x]
            print('     init',x,'dims',t.vec_i64(0),'dtype',t.i32(1),'exp',t.vec_i64(13),'doc',t.string(7),'rawlen',t.vec(8)[1]*16)
        else:
            print('     in  ',x,'exp',vi.get(x))
    for x in outs:
        print('     out ',x,'exp',vi.get(x))
    print('     attr',at)
