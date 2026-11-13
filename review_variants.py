"""C variants justified by the second review: branch polarity and disjoint stores."""
import copy
import re
from search_rules import Variants


def generate(source, repository):
    rules=Variants(repository)
    tree,fn=rules.parse(source)
    a=rules.a
    nodes=list(rules._nodes(fn.body))
    integral_types={'s8','u8','s16','u16','s32','u32','s64','u64','char','short','int','unsigned int','unsigned char','unsigned short'}
    declared={}
    for *_,node in rules._nodes(fn):
        if isinstance(node,a.Decl) and node.name:declared.setdefault(node.name,[]).append(node)
    def integral_arm(expr):
        if isinstance(expr,a.Constant):return expr.type in ('int','unsigned int')
        if isinstance(expr,a.ID):return len(declared.get(expr.name,[]))==1 and rules.generator.visit(declared[expr.name][0].type) in integral_types
        if isinstance(expr,a.Cast):return rules.generator.visit(expr.to_type.type) in integral_types
        return False
    # Invert the truth value, not an FP comparison. Original condition and the
    # selected operand are still each evaluated exactly once.
    for position,(_,_,_,node) in enumerate(nodes):
        if isinstance(node,a.TernaryOp):
            other=copy.deepcopy(tree)
            target=next(n for n in other.ext if isinstance(n,a.FuncDef))
            ternary=list(rules._nodes(target.body))[position][3]
            ternary.cond=a.UnaryOp('!',ternary.cond)
            ternary.iftrue,ternary.iffalse=ternary.iffalse,ternary.iftrue
            yield 'bedingten-ausdruck-umkehren',rules._emit(source,other)
            # A named literal can keep a loop-invariant result in a register.
            # Preserve the literal's exact C type/value; never add padding or a
            # fabricated live value. The variable replaces a real existing use.
            loops=[n for *_,n in rules._nodes(fn.body) if isinstance(n,(a.For,a.While,a.DoWhile))]
            if integral_arm(node.iftrue) and integral_arm(node.iffalse) and any(any(child is node for *_,child in rules._nodes(loop)) for loop in loops):
                for arm in ('iftrue','iffalse'):
                    literal=getattr(node,arm)
                    if not isinstance(literal,a.Constant) or literal.type not in ('int','unsigned int'):continue
                    token=re.sub(r'[uUlL]+$','',literal.value)
                    try:number=int(token,16 if token.lower().startswith('0x') else 8 if token.startswith('0') and len(token)>1 else 10)
                    except ValueError:continue
                    if not 0<=number<=(0x7fffffff if literal.type=='int' else 0xffffffff):continue
                    reserved={n.name for *_,n in rules._nodes(tree) if isinstance(n,(a.ID,a.Decl))}
                    name=next('rac4_loop_result_'+str(i) for i in range(10000) if 'rac4_loop_result_'+str(i) not in reserved)
                    other=copy.deepcopy(tree)
                    target=next(n for n in other.ext if isinstance(n,a.FuncDef))
                    ternary=list(rules._nodes(target.body))[position][3]
                    constant=getattr(ternary,arm)
                    declaration=rules.parser().parse(literal.type+' '+name+' = '+constant.value+';').ext[0]
                    setattr(ternary,arm,a.ID(name))
                    target.body.block_items.insert(0,declaration)
                    yield 'benannte-schleifenkonstante',rules._emit(source,other)
    # Only known byte pointers, unshadowed locals, and constant disjoint spans.
    # Calls, memory reads on the RHS, volatile/atomic objects and global IDs are
    # excluded. No claim about arbitrary pointer aliasing is needed.
    decls={}
    address_taken={node.expr.name for *_,node in rules._nodes(fn)
                   if isinstance(node,a.UnaryOp) and node.op=='&' and isinstance(node.expr,a.ID)}
    for *_,node in rules._nodes(fn):
        if isinstance(node,a.Decl) and node.name:
            decls.setdefault(node.name,[]).append(node)
    def safe_type(decl):
        if any(q in ('volatile','_Atomic') for *_,n in rules._nodes(decl) for q in getattr(n,'quals',[])):return False
        return not decl.storage
    def pure(expr):
        if isinstance(expr,a.Constant):return expr.type in ('int','unsigned int','long','unsigned long')
        if isinstance(expr,a.ID):
            if expr.name in address_taken or len(decls.get(expr.name,[]))!=1 or not safe_type(decls[expr.name][0]):return False
            spelling=rules.generator.visit(decls[expr.name][0].type)
            return spelling in ('s8','u8','s16','u16','s32','u32','s64','u64','int','unsigned int','char','short') or spelling.endswith('*')
        if isinstance(expr,a.Cast):
            spelling=rules.generator.visit(expr.to_type.type)
            if spelling not in ('s8','u8','s16','u16','s32','u32','s64','u64','int','unsigned int','char','short') and not spelling.endswith('*'):return False
            if 'volatile' in spelling or '_Atomic' in spelling:return False
            return pure(expr.expr)
        return False
    def span(statement):
        if not isinstance(statement,a.Assignment) or statement.op!='=' or not pure(statement.rvalue):return None
        lval=statement.lvalue
        if not isinstance(lval,a.UnaryOp) or lval.op!='*' or not isinstance(lval.expr,a.Cast):return None
        cast=lval.expr
        spelling=rules.generator.visit(cast.to_type.type)
        widths={'s8 *':1,'u8 *':1,'s16 *':2,'u16 *':2,'s32 *':4,'u32 *':4,'void **':4,'u8 **':4,'s8 **':4}
        if spelling not in widths:return None
        ptr=cast.expr; offset=0
        if isinstance(ptr,a.BinaryOp) and ptr.op=='+' and isinstance(ptr.right,a.Constant):
            try:offset=int(re.sub(r'[uUlL]+$','',ptr.right.value),0)
            except ValueError:return None
            ptr=ptr.left
        if not isinstance(ptr,a.ID) or ptr.name in address_taken or len(decls.get(ptr.name,[]))!=1:return None
        decl=decls[ptr.name][0]
        if not safe_type(decl) or rules.generator.visit(decl.type) not in ('u8 *','s8 *','char *'):return None
        if offset<0 or offset+widths[spelling]>0x100000:return None
        return ptr.name,offset,offset+widths[spelling]
    for position,(_,_,_,node) in enumerate(nodes):
        if not isinstance(node,a.Compound):continue
        statements=node.block_items or []
        for i in range(len(statements)-1):
            first,second=span(statements[i]),span(statements[i+1])
            if not first or not second or first[0]!=second[0] or not (first[2]<=second[1] or second[2]<=first[1]):continue
            other=copy.deepcopy(tree)
            target=next(n for n in other.ext if isinstance(n,a.FuncDef))
            compound=list(rules._nodes(target.body))[position][3]
            compound.block_items[i],compound.block_items[i+1]=compound.block_items[i+1],compound.block_items[i]
            yield 'getrennte-feldstores-tauschen',rules._emit(source,other)
