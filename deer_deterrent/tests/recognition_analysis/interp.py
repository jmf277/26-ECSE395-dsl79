"""A small numpy interpreter for deer.espdl (ESP-DL int8 graph).

Written from the file's own graph and exponents and from ESP-DL 3.3.14's kernel source:
per-tensor power-of-two shift, round half up, saturate to int8, ReLU clamp at 0.
It only claims to reproduce the board where it has been compared with the board's recorded
boxes and scores (see compare_board.py).
"""
import sys, struct, json
import numpy as np
sys.path.insert(0, '.')
from fbs import *

DTYPE = {1: np.float32, 3: np.int8, 6: np.int32, 7: np.int64}


def rshift_round(a, s):
    if s > 0:
        return (a + (1 << (s - 1))) >> s
    if s < 0:
        return a << (-s)
    return a


class Model:
    def __init__(self, path, qacc_sat=False):
        self.qacc_sat = qacc_sat
        data, b, root = open_espdl(path)
        self.b = b
        g = root.table(7)
        self.exp = {}
        for fld in (4, 5, 6):
            for v in g.vec_tables(fld):
                e = v.vec_i64(3)
                self.exp[v.string(0)] = e[0] if e else 0
        self.inputs = [v.string(0) for v in g.vec_tables(4)]
        self.outputs = [v.string(0) for v in g.vec_tables(5)]
        self.init = {}
        for t in g.vec_tables(2):
            name = t.string(6)
            dims = t.vec_i64(0)
            p, n = t.vec(8)
            raw = bytes(b.d[b.base + p: b.base + p + 16 * n])
            self.init[name] = dict(dims=dims, dt=t.i32(1), raw=raw, exp=(t.vec_i64(13) or [0])[0], doc=t.string(7))
        self.nodes = []
        for n in g.vec_tables(0):
            at = {}
            for a in n.vec_tables(5):
                nm = a.string(0)
                if a.has(5): at[nm] = a.b.i64(a.pos + a.off(5))
                elif a.has(4): at[nm] = a.b.f32(a.pos + a.off(4))
                elif a.has(6):
                    p, m = a.vec(6); at[nm] = bytes(b.d[b.base + p: b.base + p + m]).decode()
                elif a.has(11): at[nm] = a.vec_i64(11)
                elif a.has(10):
                    p, m = a.vec(10); at[nm] = [a.b.f32(p + 4 * i) for i in range(m)]
            self.nodes.append(dict(op=n.string(3), name=n.string(2), ins=n.vec_strings(0), outs=n.vec_strings(1), at=at))
        self._prep()

    def _vals(self, name):
        t = self.init[name]
        cnt = int(np.prod(t['dims'])) if t['dims'] else 1
        return np.frombuffer(t['raw'], dtype=DTYPE[t['dt']], count=cnt)

    def _conv_w(self, node):
        name = node['ins'][1]
        t = self.init[name]
        kh, kw, c, n = t['dims']
        v = self._vals(name)
        if node['at']['group'] != 1:
            # depthwise: dims [kh,kw,C,1]; (C/16) groups of [kh][kw][16]
            assert n == 1 and c % 16 == 0 and c == node['at']['group'], t['dims']
            a = v.reshape(c // 16, kh, kw, 16)
            return a.reshape(c // 16, kh, kw, 16).transpose(1, 2, 0, 3).reshape(kh, kw, c).astype(np.int64)
        w = np.zeros((kh, kw, c, n), dtype=np.int64)
        nd = n // 16
        off = 0
        if nd:
            a = v[: nd * 16 * kh * kw * c].reshape(nd, kh, kw, c, 16)
            w[:, :, :, : nd * 16] = a.transpose(1, 2, 3, 0, 4).reshape(kh, kw, c, nd * 16)
            off = nd * 16 * kh * kw * c
        r = n - nd * 16
        if r:
            a = v[off:].reshape(r, kh, kw, c)
            w[:, :, :, nd * 16:] = a.transpose(1, 2, 3, 0)
        return w

    def _prep(self):
        for nd in self.nodes:
            if nd['op'] == 'Conv':
                nd['w'] = self._conv_w(nd)
                nd['bias'] = self._vals(nd['ins'][2]).astype(np.int64) if len(nd['ins']) > 2 else None
                nd['mac_shift'] = self.exp[nd['outs'][0]] - self.init[nd['ins'][1]]['exp'] - self.exp[nd['ins'][0]]
            if nd['op'] == 'Split':
                nd['sizes'] = self._vals(nd['ins'][1]).tolist()

    def conv(self, nd, x):
        at = nd['at']
        sy, sx = at['strides']
        pt, pl, pb, pr = at['pads']
        w = nd['w']
        kh, kw = w.shape[:2]
        xp = np.pad(x, ((pt, pb), (pl, pr), (0, 0)))
        OH = (x.shape[0] + pt + pb - kh) // sy + 1
        OW = (x.shape[1] + pl + pr - kw) // sx + 1
        dep = at['group'] != 1
        N = x.shape[2] if dep else w.shape[3]
        acc = np.zeros((OH, OW, N), dtype=np.int64)
        for i in range(kh):
            for j in range(kw):
                sl = xp[i: i + sy * (OH - 1) + 1: sy, j: j + sx * (OW - 1) + 1: sx, :]
                acc += (sl * w[i, j]) if dep else (sl @ w[i, j])
        if nd['bias'] is not None:
            acc += nd['bias']
        if self.qacc_sat:
            acc = np.clip(acc, -(1 << 19), (1 << 19) - 1)
        out = rshift_round(acc, nd['mac_shift'])
        out = np.clip(out, -128, 127)
        if at['activation'] == 'Relu':
            out = np.maximum(out, 0)
        else:
            assert at['activation'] == 'Linear', at['activation']
        return out

    def maxpool(self, nd, x):
        k = nd['at']['kernel_shape'][0]
        p = nd['at']['pads'][0]
        v = np.pad(x, ((p, p), (p, p), (0, 0)), constant_values=-128)  # border: only real pixels count
        sw = np.lib.stride_tricks.sliding_window_view(v, (k, k), axis=(0, 1))
        return sw.max(axis=(3, 4))

    def run(self, x, keep=None):
        T = {self.inputs[0]: x.astype(np.int64)}
        for nd in self.nodes:
            op = nd['op']
            if op == 'Conv':
                T[nd['outs'][0]] = self.conv(nd, T[nd['ins'][0]])
            elif op == 'RequantizeLinear':
                s = self.exp[nd['outs'][0]] - self.exp[nd['ins'][0]]
                T[nd['outs'][0]] = np.clip(rshift_round(T[nd['ins'][0]], s), -128, 127)
            elif op == 'Add':
                assert self.exp[nd['ins'][0]] == self.exp[nd['ins'][1]] == self.exp[nd['outs'][0]]
                T[nd['outs'][0]] = np.clip(T[nd['ins'][0]] + T[nd['ins'][1]], -128, 127)
            elif op == 'Concat':
                assert nd['at']['axis'] in (3, -1, 1), nd['at']
                for i in nd['ins']:
                    assert self.exp[i] == self.exp[nd['outs'][0]], (nd['name'], i)
                T[nd['outs'][0]] = np.concatenate([T[i] for i in nd['ins']], axis=2)
            elif op == 'Split':
                a = T[nd['ins'][0]]
                s = nd['sizes']
                T[nd['outs'][0]] = a[:, :, :s[0]]
                T[nd['outs'][1]] = a[:, :, s[0]:]
            elif op == 'MaxPool':
                T[nd['outs'][0]] = self.maxpool(nd, T[nd['ins'][0]])
            elif op == 'Resize':
                a = T[nd['ins'][0]]
                s = self.exp[nd['outs'][0]] - self.exp[nd['ins'][0]]
                assert s == 0
                T[nd['outs'][0]] = a.repeat(2, axis=0).repeat(2, axis=1)
            else:
                raise ValueError(op)
        return T


def inv_sigmoid(p):
    return float(np.log(p / (1 - p)))


def decode(T, exp, cam_w=320, cam_h=240, floor=0.10, nms_iou=0.70, top_k=10):
    """ESPDetPostProcessor: returns boxes as the board's record shows them."""
    inv = np.float32(cam_w) / np.float32(224)       # 320/224
    border_top = (224 - int(np.float32(224) / np.float32(cam_w) * cam_h)) // 2
    res = []
    for stage, stride in enumerate((8, 16, 32)):
        sc = T[f'score{stage}']; bx = T[f'box{stage}']
        se, be = exp[f'score{stage}'], exp[f'box{stage}']
        thr = int(np.floor(inv_sigmoid(floor) / 2.0 ** se + 0.5))
        H, W = sc.shape[:2]
        for y in range(H):
            for x in range(W):
                q = int(sc[y, x, 0])
                if q >= thr:
                    cx = x * stride + stride // 2
                    cy = y * stride + stride // 2
                    d = bx[y, x].astype(np.float32) * np.float32(2.0 ** be)
                    s = float(1 / (1 + np.exp(-float(q * 2.0 ** se))))
                    box = [int(((cx - d[0] * stride) - 0) * inv), int(((cy - d[1] * stride) - border_top) * inv),
                           int(((cx + d[2] * stride) - 0) * inv), int(((cy + d[3] * stride) - border_top) * inv)]
                    res.append(dict(score=s, q=q, stage=stage, cell=(y, x), box=box))
    # sorted descending, ties keep insertion order (std::upper_bound with greater_box)
    res.sort(key=lambda r: -r['score'])
    kept = []
    rest = res
    i = 0
    n = 0
    lst = res[:]
    k = 0
    while k < len(lst):
        n += 1
        if n >= top_k:
            lst = lst[: k + 1]
            break
        a = lst[k]['box']
        area = (a[2] - a[0] + 1) * (a[3] - a[1] + 1)
        j = k + 1
        while j < len(lst):
            b = lst[j]['box']
            ih = min(a[3], b[3]) - max(a[1], b[1]) + 1
            iw = min(a[2], b[2]) - max(a[0], b[0]) + 1
            if ih > 0 and iw > 0:
                oa = (b[2] - b[0] + 1) * (b[3] - b[1] + 1)
                inter = ih * iw
                if inter / (area + oa - inter) > nms_iou:
                    lst.pop(j)
                    continue
            j += 1
        k += 1
    for r in lst:
        b = r['box']
        b[0] = max(b[0], 0); b[1] = max(b[1], 0)
        b[2] = min(b[2], cam_w - 1); b[3] = min(b[3], cam_h - 1)
    return lst
