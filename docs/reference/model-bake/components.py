from pathlib import Path
import struct,json,collections
raw=Path('/home/dj/Downloads/mxmaster4mac-whitesilver-ar-360.glb').read_bytes();jl=struct.unpack_from('<I',raw,12)[0];bo=12+8+jl+8;d=json.loads(raw[20:20+jl]);b=memoryview(raw)[bo:]
def accessor(i):
 a=d['accessors'][i];v=d['bufferViews'][a['bufferView']];count=a['count'];ty={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']];fmt={5125:'I',5126:'f'}[a['componentType']];size=struct.calcsize(fmt)*ty;off=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',size)
 return [struct.unpack_from('<'+fmt*ty,b,off+j*stride) for j in range(count)]
out=[]
for m in d['meshes']:
 p=m['primitives'][0];pos=accessor(p['attributes']['POSITION']);idx=[r[0] for r in accessor(p['indices'])];parent=list(range(len(pos)))
 def find(a):
  while parent[a]!=a:parent[a]=parent[parent[a]];a=parent[a]
  return a
 def union(a,b):
  a,b=find(a),find(b)
  if a!=b:parent[b]=a
 # Weld duplicate positions (normal/UV seams otherwise split logical islands).
 weld={}
 for i,v in enumerate(pos):
  key=tuple(round(c,4) for c in v)
  if key in weld:union(i,weld[key])
  else:weld[key]=i
 for i in range(0,len(idx),3):union(idx[i],idx[i+1]);union(idx[i],idx[i+2])
 comps=collections.defaultdict(list)
 for i in range(len(pos)):comps[find(i)].append(i)
 triangles=collections.Counter(find(idx[i]) for i in range(0,len(idx),3))
 rows=[]
 for root,vs in sorted(comps.items(),key=lambda t:-triangles[t[0]]):
  pts=[pos[j] for j in vs];lo=[min(p[k] for p in pts) for k in range(3)];hi=[max(p[k] for p in pts) for k in range(3)]
  rows.append({'vertices':len(vs),'triangles':triangles[root],'bounds_source_mm':[lo,hi],'center_source_mm':[(lo[k]+hi[k])/2 for k in range(3)]})
 out.append({'mesh':m['name'],'component_count':len(rows),'components':rows})
Path('/tmp/mx4-glb-prep/components.json').write_text(json.dumps(out,indent=2))
for m in out:
 print(m['mesh'],m['component_count'])
 if m['mesh']=='METAL_EXTRA_PARTS':
  for x in m['components'][:30]:print(json.dumps(x))
