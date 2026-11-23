"""Minimal read-only FlatBuffers walker for the ESP-DL .espdl container.

The field numbers are given by its callers: walk1.py and walk2.py worked them out by walking
the file itself, and they agree with ESP-DL's own schema, espdl.fbs (ESP-DL 3.3.14, the
version the trial program builds with), which dump_model.py follows; interp.py uses the same
numbers. The layout mirrors ONNX, which is what ESP-DL's schema was derived from. Everything
its callers print is read from the bytes.
"""
import struct


class Buf:
    def __init__(self, data, base=0):
        self.d = data
        self.base = base

    def u8(self, p):  return self.d[self.base + p]
    def u16(self, p): return struct.unpack_from('<H', self.d, self.base + p)[0]
    def i16(self, p): return struct.unpack_from('<h', self.d, self.base + p)[0]
    def u32(self, p): return struct.unpack_from('<I', self.d, self.base + p)[0]
    def i32(self, p): return struct.unpack_from('<i', self.d, self.base + p)[0]
    def i64(self, p): return struct.unpack_from('<q', self.d, self.base + p)[0]
    def f32(self, p): return struct.unpack_from('<f', self.d, self.base + p)[0]


class Table:
    def __init__(self, buf, pos):
        self.b = buf
        self.pos = pos
        vt = pos - buf.i32(pos)
        self.vt = vt
        self.vsize = buf.u16(vt)
        self.tsize = buf.u16(vt + 2)
        self.nfields = (self.vsize - 4) // 2

    def off(self, field):
        if field >= self.nfields:
            return 0
        return self.b.u16(self.vt + 4 + 2 * field)

    def has(self, field):
        return self.off(field) != 0

    def fields(self):
        return [i for i in range(self.nfields) if self.off(i)]

    def i32(self, f, default=0):
        o = self.off(f)
        return self.b.i32(self.pos + o) if o else default

    def i64(self, f, default=0):
        o = self.off(f)
        return self.b.i64(self.pos + o) if o else default

    def f32(self, f, default=0.0):
        o = self.off(f)
        return self.b.f32(self.pos + o) if o else default

    def ref(self, f):
        o = self.off(f)
        if not o:
            return None
        p = self.pos + o
        return p + self.b.u32(p)

    def table(self, f):
        r = self.ref(f)
        return Table(self.b, r) if r is not None else None

    def string(self, f):
        r = self.ref(f)
        if r is None:
            return None
        n = self.b.u32(r)
        return bytes(self.b.d[self.b.base + r + 4: self.b.base + r + 4 + n]).decode('utf-8', 'replace')

    def vec(self, f):
        """returns (position of first element, length)"""
        r = self.ref(f)
        if r is None:
            return None, 0
        return r + 4, self.b.u32(r)

    def vec_tables(self, f):
        p, n = self.vec(f)
        out = []
        for i in range(n):
            q = p + 4 * i
            out.append(Table(self.b, q + self.b.u32(q)))
        return out

    def vec_strings(self, f):
        p, n = self.vec(f)
        out = []
        for i in range(n):
            q = p + 4 * i
            r = q + self.b.u32(q)
            m = self.b.u32(r)
            out.append(bytes(self.b.d[self.b.base + r + 4: self.b.base + r + 4 + m]).decode('utf-8', 'replace'))
        return out

    def vec_i64(self, f):
        p, n = self.vec(f)
        return [self.b.i64(p + 8 * i) for i in range(n)]

    def vec_i32(self, f):
        p, n = self.vec(f)
        return [self.b.i32(p + 4 * i) for i in range(n)]


def open_espdl(path):
    data = open(path, 'rb').read()
    assert data[:4] == b'EDL2', data[:4]
    length = struct.unpack_from('<Q', data, 8)[0]
    assert length + 16 == len(data), (length, len(data))
    b = Buf(data, 16)
    root = Table(b, b.u32(0))
    return data, b, root
