"""Lazy PAL dispatch for a native-backed private-local search variant.

The positive AST shape and function identity are established before any catalogue,
symbol, reference ELF or native bytes are accessed. Reference/placement failure
means no variant. Native entries must cover the explicit primary exactly once.
"""
import re
from .native_local_after_call import analyze, generate


def generate_for_engine(engine,name,source,*,rules=None):
    try:
        if not isinstance(name,str) or not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-F]{8}',name):return []
        if rules is None:
            from search_rules import Variants
            rules=Variants(engine.root)
        shape=analyze(rules,source)
        if shape is None or shape['fn'].decl.name!=name:return []
        primary=tuple(engine.place(name)[:4])
        places=[tuple(p) for p in engine.all_places(name)]
        if not places or places[0]!=primary or places.count(primary)!=1 or len(set(places))!=len(places):return []
        for place in places:
            if (len(place)!=4 or not all(isinstance(p,str) and p for p in place[:2])
                    or any(isinstance(p,bool) or not isinstance(p,int) for p in place[2:])
                    or place[2]%4 or place[3]<=0 or place[3]%4 or not 0<=place[2]<=0xffffffff-place[3]):return []
        references=set()
        for place in places:
            if place[0] not in references:
                if engine.assert_reference(place[0]) is not None:return []
                references.add(place[0])
        symbols=[]
        for place in places:
            address=engine.symbol_address(shape['callee'],place[0])
            if isinstance(address,bool) or not isinstance(address,int) or not 0<=address<=0xfffffffc or address%4:return []
            symbols.append(address)
        native=[]
        for place,target in zip(places,symbols):
            raw=engine.check.disc_bytes(place)
            if not isinstance(raw,(bytes,bytearray)) or len(raw)!=place[3]:return []
            native.append(dict(raw=bytes(raw),start=place[2],symbols={shape['callee']:target}))
        output=generate(rules,source,native)
        return [output] if output else []
    except (ValueError,TypeError,AttributeError,IndexError,KeyError,OSError,StopIteration):
        return []
