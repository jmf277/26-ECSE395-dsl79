# The real frame nearest a retest at 1 to 2 ft: set-up session 08, D1 held by hand about 9 in from the lens (its exact model input).
# Paint its ceiling light out; then keep only the sheet.
import sys,glob; sys.path.insert(0,'.')
import numpy as np
from exp_util import *
S08=glob.glob(REC+'/setup-check-20261104-08/*-frame-000001.json')[0][:-5]
x=load_input(S08); o=forward(x)
print('s08 as recorded',[(round(b['score'],4),b['box']) for b in boxes(o)])
a=x.copy()
# light cells in camera px about x 228-282, y 211-239 -> input x 150-200, y 175-196; paint with color just above it
ring=a[165:172,140:210].reshape(-1,3).mean(0).round().astype(np.int8)
a[172:196,140:210]=ring
o=forward(a); print('s08 with light painted flat',[(round(b['score'],4),b['box']) for b in boxes(o)])
# keep the sheet, blank everything else to the local dark color, as if the room were out of view
b=x.copy()
mask=np.zeros((224,224),bool); mask[40:150,60:170]=True
fill=x[150:165,20:60].reshape(-1,3).mean(0).round().astype(np.int8)
b[28:196][~mask[28:196]]=fill
o=forward(b); print('s08 sheet only',[(round(r['score'],4),r['box']) for r in boxes(o)])
