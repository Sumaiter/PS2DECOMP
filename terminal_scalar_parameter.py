"""Review18 search variant: reuse a by-value scalar in terminal branches.

Deliberately narrow: one f32 parameter, two uninitialized f32 locals, one final
if/else whose arms initialize separate locals from the parameter and return.
No arithmetic, condition, operand order, precision, ABI or flags are changed.
"""
import copy

def generate(rules,source):
 a=rules.a
 try:tree,fn=copy.deepcopy(rules.parse(source))
 except Exception:return None
 if len(tree.ext)!=1:return None
 for *_,n in rules._nodes(tree):
  if isinstance(n,a.Typedef) or any(getattr(n,k,None) for k in ('quals','align','gcc_attributes','asmlabel')):return None
  if 'register' in getattr(n,'storage',[]):return None
  name=getattr(n,'name',None)
  if isinstance(name,str) and (name.startswith('__') or name in ('asm','_asm')):return None
  if 'asm' in type(n).__name__.lower():return None
 # Resolve the actual common.h typedefs, including qualifiers hidden behind
 # aliases. Source-local typedef overrides were rejected by the one-definition
 # shape above; recursive aliases and unknown/qualified leaves stay manual.
 try:
  aliases={n.name:n.type for n in rules.parser().parse(rules.prelude).ext if isinstance(n,a.Typedef)}
 except Exception:return None
 def primitive(t,seen=frozenset()):
  if not isinstance(t,a.TypeDecl) or t.quals or not isinstance(t.type,a.IdentifierType):return None
  if any(getattr(t,k,None) for k in ('align','gcc_attributes','asmlabel')):return None
  names=t.type.names
  if len(names)==1 and names[0] in aliases:
   if names[0] in seen:return None
   return primitive(aliases[names[0]],seen|{names[0]})
  return ' '.join(names)
 if 'f32' not in aliases or primitive(aliases['f32'],frozenset({'f32'}))!='float':return None
 def scalar(t):
  return isinstance(t,a.TypeDecl) and not t.quals and isinstance(t.type,a.IdentifierType) and t.type.names==['f32']
 if not scalar(fn.decl.type.type):return None
 args=fn.decl.type.args.params if fn.decl.type.args else []
 if len(args)!=1 or not isinstance(args[0],a.Decl) or not scalar(args[0].type):return None
 param=args[0].name
 if not isinstance(param,str) or param==fn.decl.name:return None
 items=fn.body.block_items or []
 if len(items)!=3 or not all(isinstance(n,a.Decl) and scalar(n.type) and n.init is None and not n.storage for n in items[:2]):return None
 names={n.name for n in items[:2]}
 if len(names)!=2 or None in names or param in names or fn.decl.name in names:return None
 branch=items[-1]
 if not isinstance(branch,a.If) or not isinstance(branch.iftrue,a.Compound) or not isinstance(branch.iffalse,a.Compound):return None
 def pure(n,ids):
  if isinstance(n,a.ID):return n.name in ids
  if isinstance(n,a.Constant):return n.type in ('float','int')
  if isinstance(n,a.UnaryOp):return n.op in ('+','-','!') and pure(n.expr,ids)
  if isinstance(n,a.BinaryOp):return n.op in ('+','-','*','/','<','<=','>','>=','==','!=') and pure(n.left,ids) and pure(n.right,ids)
  if isinstance(n,a.Cast):
   t=n.to_type.type
   return isinstance(t,a.TypeDecl) and isinstance(t.type,a.IdentifierType) and t.type.names in (['f32'],['s32']) and primitive(t) in ('float','int','signed','signed int') and pure(n.expr,ids)
  return False
 if not pure(branch.cond,{param}):return None
 targets=[]
 for block in (branch.iftrue,branch.iffalse):
  seq=block.block_items or []
  if len(seq)<2 or not isinstance(seq[-1],a.Return):return None
  first=seq[0]
  if not isinstance(first,a.Assignment) or first.op!='=' or not isinstance(first.lvalue,a.ID) or first.lvalue.name not in names:return None
  target=first.lvalue.name
  if not pure(first.rvalue,{param}) or not pure(seq[-1].expr,{target}):return None
  for item in seq[1:-1]:
   if not isinstance(item,a.Assignment) or item.op!='=' or not isinstance(item.lvalue,a.ID) or item.lvalue.name!=target or not pure(item.rvalue,{target}):return None
  targets.append(target)
 if set(targets)!=names:return None
 for block,target in zip((branch.iftrue,branch.iffalse),targets):
  for *_,n in rules._nodes(block):
   if isinstance(n,a.ID) and n.name==target:n.name=param
 fn.body.block_items=[branch]
 return rules._emit(source,tree)


LABEL = 'terminaler-skalarparameter-statt-zweiglokalen'

def children(source, variants):
    output = generate(variants, source)
    return [(LABEL, output)] if output is not None else []
