"""Structure an exact private-pointer two-arm call/return join, without ABI edits."""
from copy import deepcopy
from search_rules import Variants
import re

def walk(node):
    if node is not None:
        yield node
        for _, child in node.children():
            yield from walk(child)

def plain_void_pointer(decl,c):
    if not isinstance(decl,c.Decl) or decl.init or decl.quals or decl.storage:
        return False
    p=decl.type
    return (isinstance(p,c.PtrDecl) and not p.quals and
            isinstance(p.type,c.TypeDecl) and not p.type.quals and
            isinstance(p.type.type,c.IdentifierType) and p.type.type.names==['void'])

def children(source,rules):
    c=rules.a
    if re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b',source):return []
    if re.search(r'^\s*#\s*(?:define|undef)\b',source,re.M):return []
    try: tree,_=rules.parse(source)
    except Exception: return []
    funcs=[x for x in tree.ext if isinstance(x,c.FuncDef)]
    if len(funcs)!=1: return []
    fn=funcs[0];ret=fn.decl.type.type
    if not (isinstance(ret,c.TypeDecl) and isinstance(ret.type,c.IdentifierType) and ret.type.names==['void']):return []
    items=fn.body.block_items or [];i=0
    while i<len(items) and isinstance(items[i],c.Decl):i+=1
    if len(items)-i<3:return []
    first,second=items[i:i+2];tail=items[i+2:]
    if not all(isinstance(x,c.If) and x.iffalse is None and isinstance(x.iftrue,c.Compound) for x in (first,second)):return []
    a=first.iftrue.block_items or [];b=second.iftrue.block_items or []
    if len(a)!=2 or len(b)!=3 or not isinstance(a[0],c.Assignment) or not isinstance(b[0],c.Assignment):return []
    x,y=a[0],b[0]
    if x.op!='=' or y.op!='=' or not all(isinstance(v,c.ID) for v in (x.lvalue,y.lvalue,x.rvalue,y.rvalue)):return []
    relay=x.lvalue.name
    if y.lvalue.name!=relay:return []
    if not (isinstance(a[1],c.Goto) and isinstance(b[1],c.Label) and a[1].name==b[1].name and isinstance(b[1].stmt,c.FuncCall) and isinstance(b[2],c.Return) and b[2].expr is None):return []
    if sum(isinstance(v,c.Goto) for v in walk(fn))!=1 or sum(isinstance(v,c.Label) for v in walk(fn))!=1:return []
    if any(not isinstance(v,c.Assignment) for v in tail):return []
    if any(isinstance(v,(c.Decl,c.Goto,c.Label,c.Return,c.If,c.For,c.While,c.DoWhile,c.Switch)) for item in tail for v in walk(item)):return []
    decls=[d for d in items[:i] if d.name==relay]
    if len(decls)!=1 or not plain_void_pointer(decls[0],c):return []
    params={d.name:d for d in (fn.decl.type.args.params if fn.decl.type.args else []) if isinstance(d,c.Decl)}
    if not all(plain_void_pointer(params.get(v.name),c) for v in (x.rvalue,y.rvalue)):return []
    if sum(isinstance(v,c.ID) and v.name==relay for v in walk(fn))!=3:return []
    call=b[1].stmt
    if not isinstance(call.name,c.ID) or call.name.name==relay or not call.args:return []
    if any(not isinstance(arg,(c.ID,c.Constant)) for arg in call.args.exprs):return []
    if sum(isinstance(arg,c.ID) and arg.name==relay for arg in call.args.exprs)!=1:return []
    # No declaration may shadow either parameter or the selected function name.
    if any(d.name in {x.rvalue.name,y.rvalue.name,call.name.name} for d in items[:i]):return []
    out=deepcopy(tree);newfn=next(v for v in out.ext if isinstance(v,c.FuncDef))
    def arm(value):
        copied=deepcopy(call)
        copied.args.exprs=[deepcopy(value) if isinstance(arg,c.ID) and arg.name==relay else arg for arg in copied.args.exprs]
        return c.Compound([copied])
    structured=c.If(deepcopy(first.cond),arm(x.rvalue),c.If(deepcopy(second.cond),arm(y.rvalue),c.Compound(deepcopy(tail))))
    newfn.body.block_items=[deepcopy(v) for v in items[:i] if v.name!=relay]+[structured]
    text=rules._emit(source,out)
    return [(text,'privaten-aufruf-join-strukturieren')]

def generate(source,repository):
    for text,label in children(source,Variants(repository)):
        yield label,text
