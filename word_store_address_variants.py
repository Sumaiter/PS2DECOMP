"""Native-gated spelling of an already typed word store on the 32-bit EE ABI.

Does not infer a type or initializer. Only a private local pointer, private
index and enclosing nonnegative guard are accepted. The emitted address is
the same valid C word element represented as an unsigned32 byte address.
"""
import copy,re,struct
from search_rules import Variants,code_only

def native_store(body,parameter,offset):
 if not isinstance(body,bytes) or len(body)%4:return False
 words=[x[0] for x in struct.iter_unpack('<I',body)]
 for i in range(len(words)-2):
  w,x,y=words[i:i+3]
  op,rs,rt,rd,sh,fn=w>>26,(w>>21)&31,(w>>16)&31,(w>>11)&31,(w>>6)&31,w&63
  if not(op==0 and rs==0 and sh==2 and fn==0 and rd!=0 and rt!=0):continue
  # Distinct saved pointer base, shifted index and final store address.
  base=(x>>21)&31
  if not(x>>26==0 and x&63==33 and (x>>16)&31==rd and (x>>11)&31==rd and 16<=base<=23 and base not in {rt,rd}):continue
  if not(y>>26==43 and (y>>21)&31==rd and y&0xffff==0):continue
  defs=[]
  for q in words[:i]:
   qp,qrs,qrt,qrd,qfn=q>>26,(q>>21)&31,(q>>16)&31,(q>>11)&31,q&63
   writes=(qp==0 and qrd==base and qfn not in (8,9)) or (qp not in (0,1,2,3,4,5,6,7,17,20,21,22,23,40,41,43,49,57,63) and qrt==base)
   if writes:defs.append(q)
  expected=(9<<26)|(parameter<<21)|(base<<16)|(offset&0xffff)
  if defs!=[expected]:continue
  # The signed local's only use here is after a native BLTZ on that index.
  if not any(q>>26==1 and (q>>21)&31==rt and (q>>16)&31==0 for q in words[max(0,i-8):i]):continue
  return True
 return False

def generate(source,repository,native_bodies=(),address_bits=None):
 if address_bits!=32 or not native_bodies:return
 if re.search(r'\b(?:volatile|asm|__asm__|__LINE__|__COUNTER__|__FILE__)\b',code_only(source)):return
 rules=Variants(repository);a=rules.a
 try:tree,fn=rules.parse(source)
 except Exception:return
 nodes=list(rules._nodes(fn)); allnodes=list(rules._nodes(tree))
 if any(isinstance(n,(a.Typedef,a.Goto,a.Label)) or getattr(n,'quals',None) for *_,n in allnodes):return
 decls={}
 for *_,n in nodes:
  if isinstance(n,a.Decl) and n.name:decls.setdefault(n.name,[]).append(n)
 params=fn.decl.type.args.params if fn.decl.type.args else []
 if not 1<=len(params)<=4 or any(not isinstance(p,a.Decl) or rules.generator.visit(p.type) not in {'void *','s8 *','u8 *','char *','s32','u32','int','unsigned int'} for p in params):return
 paramnames={p.name:4+i for i,p in enumerate(params)}
 escaped={n.expr.name for *_,n in nodes if isinstance(n,a.UnaryOp) and n.op=='&' and isinstance(n.expr,a.ID)}
 def integer(n):
  if not isinstance(n,a.Constant):return None
  try:return int(n.value,0)
  except ValueError:return None
 def guarded(n,name):
  if isinstance(n,a.BinaryOp) and n.op=='&&':return guarded(n.left,name) or guarded(n.right,name)
  return isinstance(n,a.BinaryOp) and n.op=='>=' and isinstance(n.left,a.ID) and n.left.name==name and integer(n.right)==0
 statements=fn.body.block_items or []
 for index,(_,_,_,node) in enumerate(nodes):
  if not isinstance(node,a.Assignment) or node.op!='=' or not isinstance(node.lvalue,a.ArrayRef):continue
  ref=node.lvalue
  if not isinstance(ref.name,a.ID) or not isinstance(ref.subscript,a.ID):continue
  base,idx=ref.name.name,ref.subscript.name
  if base==idx or base in escaped or idx in escaped:continue
  bd,idc=decls.get(base,[]),decls.get(idx,[])
  if len(bd)!=1 or len(idc)!=1 or bd[0] not in statements or idc[0] not in statements:continue
  if bd[0].storage or idc[0].storage or rules.generator.visit(bd[0].type) not in {'s32 *','u32 *','int *','unsigned int *'} or rules.generator.visit(idc[0].type) not in {'s32','int'}:continue
  definitions=[n for *_,n in nodes if isinstance(n,a.Assignment) and isinstance(n.lvalue,a.ID) and n.lvalue.name==base]
  if len(definitions)!=1 or bd[0].init is not None:continue
  value=definitions[0].rvalue
  # Existing explicit word-pointer initialization from parameter+byte offset.
  if not isinstance(value,a.Cast) or rules.generator.visit(value.to_type.type)!=rules.generator.visit(bd[0].type):continue
  plus=value.expr
  if not isinstance(plus,a.BinaryOp) or plus.op!='+' or not isinstance(plus.left,a.Cast) or rules.generator.visit(plus.left.to_type.type) not in {'s8 *','u8 *','char *'} or not isinstance(plus.left.expr,a.ID):continue
  param=plus.left.expr.name;off=integer(plus.right)
  if param not in paramnames or off is None or not 0<=off<=0x7fff or off%4:continue
  # Original parameter register provenance cannot be asserted after a source
  # parameter mutation or escape. Reject the whole function conservatively.
  if param in escaped or any((isinstance(q,a.Assignment) and isinstance(q.lvalue,a.ID) and q.lvalue.name==param) or (isinstance(q,a.UnaryOp) and q.op in ('++','--','p++','p--') and isinstance(q.expr,a.ID) and q.expr.name==param) for *_,q in nodes):continue
  guards=[n for *_,n in nodes if isinstance(n,a.If) and guarded(n.cond,idx) and any(q is node for *_,q in rules._nodes(n.iftrue))]
  if not guards:continue
  guard=guards[-1];before=[]
  # A guard can invalidate its own lower bound before entering the body.
  guardnodes=[q for *_,q in rules._nodes(guard.cond)]
  if any(isinstance(q,a.FuncCall) or (isinstance(q,a.Assignment) and isinstance(q.lvalue,a.ID) and q.lvalue.name in {base,idx}) or (isinstance(q,a.UnaryOp) and q.op in ('++','--','p++','p--') and isinstance(q.expr,a.ID) and q.expr.name in {base,idx}) for q in guardnodes):continue
  for *_,q in rules._nodes(guard.iftrue):
   if q is node:break
   before.append(q)
  if any((isinstance(q,a.Assignment) and isinstance(q.lvalue,a.ID) and q.lvalue.name in {base,idx}) or (isinstance(q,a.UnaryOp) and q.op in ('++','--','p++','p--') and isinstance(q.expr,a.ID) and q.expr.name in {base,idx}) for q in before):continue
  if not all(native_store(body,paramnames[param],off) for body in native_bodies):continue
  changed=copy.deepcopy(tree);f=next(q for q in changed.ext if isinstance(q,a.FuncDef));target=list(rules._nodes(f))[index][3]
  temp=rules.parser().parse(rules.prelude+'\nu32 rac4_word_addr;').ext[-1]
  typ=copy.deepcopy(temp.type);typ.declname=None
  def cast(n):return a.Cast(a.Typename(name=None,quals=[],align=None,gcc_attributes=[],type=copy.deepcopy(typ)),copy.deepcopy(n))
  ptr=copy.deepcopy(bd[0].type);ptr.type.declname=None
  addr=a.BinaryOp('+',cast(ref.name),a.BinaryOp('<<',cast(ref.subscript),a.Constant('int','2')))
  target.lvalue=a.UnaryOp('*',a.Cast(a.Typename(name=None,quals=[],align=None,gcc_attributes=[],type=ptr),addr))
  yield 'native-word-store-base-first-byte-address',rules._emit(source,changed)
  return
