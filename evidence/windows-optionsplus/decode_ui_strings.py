#!/usr/bin/env python3
"""Recover plain string lookups in the supplied Options+ UI bundles, without executing JS.

Usage: python3 decode_ui_strings.py /absolute/path/to/app.min.js
Also supports the cc.min.js a1 table. Writes transformed files, old/new span maps,
and identity metadata beside the input. Mapping entries are [new_start,
old_start, old_length, new_length]. Apply the decoded map followed by the folded
map for original -> folded coordinates; reverse the order for folded -> original.
Inside a replaced span only the span boundary is an exact source location.

LIMITATION: decoder aliases are propagated by identifier names, not a lexical JS
scope analysis. This is an aid to inspecting original source, not proof of call
flow, runtime execution, or arbitrary JavaScript semantics. Always corroborate
conclusions against raw source, explicit local assignments, and other artifacts.
"""
from pathlib import Path
import ast,re,json,sys
from hashlib import sha256
p=Path(sys.argv[1]);d=p.read_bytes().decode('latin1')
m=re.search(r'function (a[01]_0x[0-9a-f]+)\(\)\{var (_0x[0-9a-f]+)=\[',d)
if not m: raise ValueError('no table')
start=m.end()-1;end=d.index('];',start)+1
arr=ast.literal_eval(d[start:end])
root=re.search(r'=([a-zA-Z0-9_]+),',d[:150]).group(1)
subtract=int(re.search(r'(_0x[0-9a-f]+)=\1-(0x[0-9a-f]+)',d[:3500000]).group(2),16)
rotator=d[d.index('while(!![])'):re.search(r'var a[01]_0x',d[d.index('while(!![])'):]).start()+d.index('while(!![])')]
expr=re.search(r'try\{var _0x[0-9a-f]+=(.*?);if',rotator).group(1)
target=int(re.search(r'\}\}\}\('+re.escape(m.group(1))+r',(0x[0-9a-f]+)\)',rotator).group(1),16)
# Translate only an explicit safe arithmetic grammar, never execute artifact JavaScript.
expr=re.sub(r'parseInt\(_0x[0-9a-f]+\((0x[0-9a-f]+)\)\)',lambda m:f'P({int(m.group(1),16)})',expr)
if not re.fullmatch(r'[P0-9a-fx()+*/\-]+',expr):raise ValueError('unsafe arithmetic')
tree=ast.parse(expr,mode='eval')
def arithmetic(n,offset):
 if isinstance(n,ast.Expression):return arithmetic(n.body,offset)
 if isinstance(n,ast.Constant) and type(n.value) is int:return n.value
 if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):return (-1 if isinstance(n.op,ast.USub) else 1)*arithmetic(n.operand,offset)
 if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
  a,b=arithmetic(n.left,offset),arithmetic(n.right,offset)
  if isinstance(n.op,ast.Add):return a+b
  if isinstance(n.op,ast.Sub):return a-b
  if isinstance(n.op,ast.Mult):return a*b
  return a/b
 if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='P' and len(n.args)==1:
  idx=arithmetic(n.args[0],offset);v=arr[(idx-subtract+offset)%len(arr)]
  num=re.match(r'^[+-]?\d+',v)
  return int(num.group()) if num else float('nan')
 raise ValueError(ast.dump(n))
rotation=next(i for i in range(len(arr)) if arithmetic(tree,i)==target)
arr=arr[rotation:]+arr[:rotation]
aliases={root}
assigns=re.findall(r'\b([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)(?=[,;])',d)
while True:
 new={a for a,b in assigns if b in aliases}-aliases
 if not new:break
 aliases.update(new)
pat=re.compile(r'\b([A-Za-z_$][\w$]*)\((0x[0-9a-f]+)\)')
replacements=[]
for m in pat.finditer(d):
 if m.group(1) not in aliases:continue
 idx=int(m.group(2),16)-subtract
 if not 0<=idx<len(arr):continue
 replacements.append((m.start(),m.end(),json.dumps(arr[idx],ensure_ascii=True)))
# output transformed file and positions for recovering original evidence locations
parts=[];cursor=0;mapping=[];newpos=0
for a,b,s in replacements:
 parts.append(d[cursor:a]);newpos+=a-cursor
 mapping.append((newpos,a,b-a,len(s)));parts.append(s);newpos+=len(s);cursor=b
parts.append(d[cursor:]);out=''.join(parts)
p.with_suffix('.static-decoded.js').write_text(out,encoding='latin1')
p.with_suffix('.static-decoded-map.json').write_text(json.dumps(mapping))
metadata={'file':str(p),'sha256':sha256(p.read_bytes()).hexdigest(),'table_entries':len(arr),'rotation':rotation,'aliases':len(aliases),'replacements':len(replacements),'output':str(p.with_suffix('.static-decoded.js'))}
p.with_suffix('.static-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata))

# Fold only contiguous literal string addition; parse literals as data.
d=out
literal=r'''(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")'''
pat=re.compile(literal+r'(?:\s*\+\s*'+literal+r')+')
parts=[];cursor=0;mapping=[];newpos=0
for m in pat.finditer(d):
 try:val=''.join(ast.literal_eval(t.group()) for t in re.finditer(literal,m.group()))
 except (ValueError,SyntaxError):continue
 s=json.dumps(val,ensure_ascii=True);a,b=m.span()
 parts.append(d[cursor:a]);newpos+=a-cursor
 mapping.append((newpos,a,b-a,len(s)));parts.append(s);newpos+=len(s);cursor=b
parts.append(d[cursor:]);out=''.join(parts)
p.with_suffix('.static-folded.js').write_text(out,encoding='latin1')
p.with_suffix('.static-folded-map.json').write_text(json.dumps(mapping))
print('folded',len(mapping))
