"""Reorder adjacent independent switch arms; never change fallthrough paths.

Each arm must finish with an unconditional top-level break/return. Declaration,
goto, label, nested-switch and nested-case bodies are deliberately excluded.
The switch condition, case constants, complete arm statements and default body
remain untouched. This is a source search lever, never a correctness verdict.
"""
from copy import deepcopy
import re

def children(source,rules):
    a=rules.a
    if re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b',source):return []
    try:tree,fn=rules.parse(source)
    except Exception:return []
    sites=[n for *_,n in rules._nodes(fn.body) if isinstance(n,a.Switch)]
    out=[]
    for site_index,site in enumerate(sites):
        if not isinstance(site.stmt,a.Compound):continue
        arms=site.stmt.block_items or []
        if not 2<=len(arms)<=6 or any(not isinstance(v,(a.Case,a.Default)) for v in arms):continue
        if sum(isinstance(v,a.Default) for v in arms)>1:continue
        valid=True
        for arm in arms:
            statements=arm.stmts or []
            if not statements or not isinstance(statements[-1],(a.Break,a.Return)):
                valid=False;break
            for statement in statements:
                if any(isinstance(v,(a.Decl,a.Goto,a.Label,a.Case,a.Default,a.Switch)) for *_,v in rules._nodes(statement)):
                    valid=False;break
        if not valid:continue
        # Keep the default arm in place, reducing branching without losing the
        # common two/three explicit-case scheduler alternatives.
        for index in range(len(arms)-1):
            if not all(isinstance(v,a.Case) for v in arms[index:index+2]):continue
            changed=deepcopy(tree)
            changed_fn=next(n for n in changed.ext if isinstance(n,a.FuncDef))
            changed_sites=[n for *_,n in rules._nodes(changed_fn.body) if isinstance(n,a.Switch)]
            changed_arms=changed_sites[site_index].stmt.block_items
            changed_arms[index:index+2]=reversed(changed_arms[index:index+2])
            out.append((rules._emit(source,changed),'abgeschlossene-switch-zweige-tauschen'))
    return out

def generate(source,repository):
    from search_rules import Variants
    for text,label in children(source,Variants(repository)):yield label,text
