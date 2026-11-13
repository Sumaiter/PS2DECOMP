"""RAC4 PAL evidence rule: shorten an unescaped local constant's lifetime.

No type, value, ABI, memory access, call, flag or expression changes. Native
placements must all put the existing signed immediate after the first call.
This emits a search variant, never a semantic or technical match certificate.
"""
import copy
import re
import struct
from ee_common import complete_cfg
from native_guard_variants import _integer


def _native(raw, start, value, target):
    if (not isinstance(raw, (bytes, bytearray)) or not 0<len(raw)<=32768
            or isinstance(start,bool) or not isinstance(start, int) or start%4
            or not 0<=start<=0xffffffff-len(raw) or len(raw) % 4
            or isinstance(target,bool) or not isinstance(target,int) or target%4
            or not 0<=target<=0xfffffffc or not complete_cfg(raw, start)):
        return False
    words = struct.unpack('<' + 'I' * (len(raw)//4), raw)
    call = next((i for i,w in enumerate(words) if w>>26 == 3), None)
    if call is None or call+2 >= len(words):
        return False
    if (((start+call*4+4)&0xf0000000)|((words[call]&0x3ffffff)<<2)) != target:
        return False
    def control(w):
        return (w>>26 in (1,2,3,4,5,6,7,20,21,22,23)
                or w>>26 == 0 and w&63 in (8,9)
                or w>>26 in (17,18) and w>>21&31 == 8)
    if any(control(w) for w in words[:call]):
        return False
    def immediate(w):
        return (w>>26 == 9 and w>>21&31 == 0 and w>>16&31 not in (0,29,31)
                and (w&0x7fff)-(w&0x8000) == value)
    # The call delay slot executes before the callee. Do not call that a late
    # initialization or move a source call argument across the call.
    if any(immediate(w) for w in words[:call+2]):
        return False
    prefix=[]
    for w in words[call+2:]:
        if control(w):break
        prefix.append(w)
    return sum(immediate(w) for w in prefix) == 1


def analyze(rules, source):
    """Return a positive source-only shape, before any native/engine access."""
    if not callable(getattr(rules, 'plain_header', None)) or not rules.plain_header():
        return None
    from m2c_pycparser.plyparser import ParseError
    a=rules.a
    try:
        if not isinstance(source,str) or '\\\n' in source or '\\\r\n' in source:return None
        from search_rules import code_only
        directives=re.findall(r'^\s*#.*$',code_only(source),re.M)
        if any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*',line) for line in directives):return None
        flags=re.findall(r'/\*\s*cflags\b.*?\*/',source,re.S)
        if flags and (len(flags)!=1 or source.splitlines()[0]!=flags[0]
                      or not re.fullmatch(r'/\* cflags: [^\r\n]* \*/',flags[0])):return None
        tree,fn=copy.deepcopy(rules.parse(source))
        nodes=[n for *_,n in rules._nodes(tree)]
        if any(any(getattr(n,k,None) for k in ('quals','align','funcspec','gcc_attributes','asmlabel'))
               or 'register' in getattr(n,'storage',[]) or 'asm' in type(n).__name__.lower()
               for n in nodes):return None
        if any(isinstance(getattr(n,'name',None),str) and (n.name.startswith('__') or n.name in ('asm','_asm'))
               for n in nodes):return None
        definitions=[n for n in rules.parser().parse(rules.prelude).ext if isinstance(n,a.Typedef)]
        aliases={n.name:n for n in definitions}
        if len(aliases)!=len(definitions):return None
        def scalar(node,seen=frozenset()):
            if any(getattr(node,k,None) for k in ('quals','align','funcspec','gcc_attributes','asmlabel')):return None
            if isinstance(node,a.Typedef):return scalar(node.type,seen)
            if not isinstance(node,a.TypeDecl) or not isinstance(node.type,a.IdentifierType):return None
            names=node.type.names
            if len(names)==1 and names[0] in aliases:
                if names[0] in seen:return None
                return scalar(aliases[names[0]],seen|{names[0]})
            return ' '.join(names)
        if any(isinstance(n,a.Typedef) for n in tree.ext):return None
        items=fn.body.block_items or []
        declarations=[n for n in items if isinstance(n,a.Decl)]
        first=next((i for i,n in enumerate(items) if not isinstance(n,a.Decl)),None)
        if first is None or first+1>=len(items):return None
        init,call_statement=items[first:first+2]
        if (not isinstance(init,a.Assignment) or init.op!='=' or not isinstance(init.lvalue,a.ID)
                or not isinstance(call_statement,a.Assignment) or call_statement.op!='='):return None
        name=init.lvalue.name
        own=[d for d in declarations if d.name==name]
        if (len(own)!=1 or own[0].init is not None or own[0].storage
                or scalar(own[0].type) not in ('int','signed int')):return None
        if sum(isinstance(n,a.Decl) and n.name==name for n in nodes)!=1:return None
        expression=init.rvalue
        sign=1
        if isinstance(expression,a.UnaryOp) and expression.op in ('+','-'):
            sign=-1 if expression.op=='-' else 1
            expression=expression.expr
        value=_integer(expression,a)
        if value is not None:value*=sign
        if value is None or not -32768<=value<=32767 or value==0:return None
        calls=[n for *_,n in rules._nodes(call_statement) if isinstance(n,a.FuncCall)]
        if len(calls)!=1 or not isinstance(calls[0].name,a.ID):return None
        if any(isinstance(n,a.ID) and n.name==name for *_,n in rules._nodes(call_statement)):return None
        if any(isinstance(n,a.UnaryOp) and n.op=='&' and isinstance(n.expr,a.ID)
               and n.expr.name==name for n in nodes):return None
        callee=calls[0].name.name
        # An ID call through a shadowing object is not a direct native symbol.
        if any(isinstance(n,a.Decl) and n.name==callee and not isinstance(n.type,a.FuncDecl)
               for n in nodes):return None
        return dict(tree=tree,fn=fn,first=first,init=init,call_statement=call_statement,value=value,callee=callee)
    except (ValueError,TypeError,AttributeError,IndexError,KeyError,ParseError):
        return None


def generate(rules, source, placements):
    shape=analyze(rules,source)
    if shape is None:return None
    try:
        tree,fn=shape['tree'],shape['fn'];value,callee=shape['value'],shape['callee']
        if not placements or not all(_native(p['raw'],p['start'],value,p['symbols'].get(callee)) for p in placements):return None
        items=fn.body.block_items;first=shape['first']
        items[first:first+2]=[shape['call_statement'],shape['init']]
        return 'native-local-constant-after-call', rules._emit(source,tree)
    except (ValueError,TypeError,AttributeError,IndexError,KeyError):
        return None
