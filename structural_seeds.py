"""Small byte-decoded C reconstructions learned from reviewed functions, without AI."""
import struct


def reverse_word_fill(name, raw, start):
    """Recognize an entire descending word-array fill followed by a field reset.

    Every non-nop instruction must participate in the pattern. No addresses,
    function names, array bounds, stored values or register choices are baked in.
    """
    if len(raw) % 4 or len(raw) > 512:
        return None
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    instructions = [(i * 4, w) for i, w in enumerate(words) if w]
    if len(instructions) != 12:
        return None
    def fields(w): return w >> 26, w >> 21 & 31, w >> 16 & 31, (w & 0x7fff) - (w & 0x8000)
    (lui, fill, cursor, count, advance, store, decrement, branch, step, reset, ret, tail) = instructions
    op, rs, base, high = fields(lui[1])
    if op != 15 or rs or base in (0, 29, 31): return None
    op, rs, fillreg, value = fields(fill[1])
    if op != 9 or rs: return None
    op, rs, cursorreg, low = fields(cursor[1])
    if op != 9 or rs != base: return None
    op, rs, countreg, countvalue = fields(count[1])
    if op != 9 or rs or not 0 <= countvalue <= 4095: return None
    if len({base, fillreg, cursorreg, countreg, 0, 29, 31}) != 7: return None
    op, rs, rt, offset = fields(advance[1])
    if (op, rs, rt) != (9, cursorreg, cursorreg): return None
    if fields(store[1]) != (43, cursorreg, fillreg, 0): return None
    if fields(decrement[1]) != (9, countreg, countreg, -1): return None
    op, rs, rt, target = fields(branch[1])
    if (op, rs, rt) != (1, countreg, 1) or branch[0] + 4 + target * 4 != store[0]: return None
    if step[0] != branch[0] + 4 or fields(step[1]) != (9, cursorreg, cursorreg, -4): return None
    if fields(reset[1]) != (9, base, cursorreg, low): return None
    if ret[1] != 0x03e00008 or tail[0] != ret[0] + 4 or tail[0] + 4 != len(raw): return None
    op, rs, rt, finaloffset = fields(tail[1])
    if (op, rs, rt) != (43, cursorreg, 0): return None
    arrayoffset = offset - countvalue * 4
    arrayend = offset + 4
    if arrayoffset < 0 or arrayoffset % 4 or finaloffset < arrayend or finaloffset % 4:
        return None
    address = (((lui[1] & 0xffff) << 16) + low) & 0xffffffff
    if address % 4: return None
    symbol = f'D_{address:08X}'
    prefix = f'    u8 prefix[0x{arrayoffset:X}];\n' if arrayoffset else ''
    gap = f'    u8 gap[0x{finaloffset - arrayend:X}];\n' if finaloffset > arrayend else ''
    source = f'''#include "common.h"
/* Automatic structural reconstruction: descending word fill and final reset.
 * Derived from every original instruction; still requires full byte/ABI review. */
typedef struct {{
{prefix}    s32 slots[{countvalue + 1}];
{gap}    s32 flag;
}} Rac4FillState;
extern Rac4FillState {symbol};
void {name}(void)
{{
    s32 value = {value};
    s32 index = {countvalue};
    do {{
        {symbol}.slots[index] = value;
    }} while (--index >= 0);
    {symbol}.flag = 0;
}}
'''
    return {'source': source, 'rule': 'reverse-word-fill', 'facts': {
        'entry': start, 'global': symbol, 'elementBytes': 4, 'count': countvalue + 1,
        'firstArrayOffset': arrayoffset, 'lastArrayOffset': offset,
        'resetOffset': finaloffset, 'storedValue': value, 'coversWholeFunction': True}}


def read_modify_reset(name, raw, start):
    """Recover a complete LW/SUBU/BLEZ setter whose delay slot stores the new value.

    The old load must remain before the store. This is not an algebraic rewrite
    of arbitrary C and never guesses a value or parameter from an address.
    """
    if len(raw)!=36:return None
    w=struct.unpack('<9I',raw)
    fields=lambda x:(x>>26,x>>21&31,x>>16&31,(x&0x7fff)-(x&0x8000))
    op,base,temp,field=fields(w[0])
    if op!=35 or base!=4 or temp in (0,4,5,29,31):return None
    if w[1] != (5<<21)|(temp<<16)|(temp<<11)|35:return None
    if fields(w[2])!=(6,temp,0,4):return None
    if fields(w[3])!=(43,base,5,field):return None
    op,rs,rt,first=fields(w[4])
    if (op,rs,rt)!=(43,base,0):return None
    op,rs,rt,second=fields(w[6])
    if (op,rs,rt)!=(43,base,0):return None
    if w[5]!=0x03e00008 or w[7]!=0x03e00008 or w[8]:return None
    if len({field,first,second})!=3 or any(x<0 or x%4 for x in (field,first,second)):return None
    source=f'''#include "common.h"
/* Recovered from the entire setter, including its branch delay store. */
void {name}(void *object,s32 value){{
    s32 previous=*(s32 *)((u8 *)object+0x{field:X});
    s32 difference=(s32)((u32)value-(u32)previous);
    if(difference<=0){{*(s32 *)((u8 *)object+0x{field:X})=value;return;}}
    *(s32 *)((u8 *)object+0x{field:X})=value;
    /* Disjoint ordinary RAM fields: source order lets the compiler fill JR. */
    *(s32 *)((u8 *)object+0x{second:X})=0;
    *(s32 *)((u8 *)object+0x{first:X})=0;
}}
'''
    return dict(source=source,rule='read-before-delay-slot-store',facts=dict(entry=start,
        oldValueOffset=field,resetOffsets=[first,second],differenceBits=32,branch='signed >0 after word wrap',
        oldReadBeforeNewStore=True,earlyExit=True,disjointResetSpans=True,coversWholeFunction=True))


def bounded_word_read(name, raw, start):
    """Decode an entire signed upper-bound check and a word-indexed load.

    Negative indices are deliberately not rejected: the native SLT tests only
    the upper bound. The address shift/add has target word wrapping semantics.
    """
    if len(raw) != 36:
        return None
    w = struct.unpack('<9I', raw)
    fields = lambda x: (x >> 26, x >> 21 & 31, x >> 16 & 31,
                        (x & 0x7fff) - (x & 0x8000))
    op, base, bound, count_offset = fields(w[0])
    if op != 35 or base != 4 or bound != 2:
        return None
    # SLT v0,a1,v0; BNE v0,zero,+3; SLL v1,a1,2.
    if w[1:4] != (0x00A2102A, 0x14400003, 0x00051880):
        return None
    if w[4:8] != (0x03E00008, 0x0000102D, 0x00831821, 0x03E00008):
        return None
    op, rs, rt, array_offset = fields(w[8])
    if (op, rs, rt) != (35, 3, 2):
        return None
    if any(offset < 0 or offset % 4 for offset in (count_offset, array_offset)):
        return None
    source = f'''/* cflags: @ee29 */
#include "common.h"
/* Whole-function reconstruction; the native function checks no lower bound. */
s32 {name}(const u8 *object,s32 index)
{{
    if(index>=*(const s32 *)(object+0x{count_offset:X})) return 0;
    return *(const s32 *)((u32)object+((u32)index<<2)+0x{array_offset:X}U);
}}
'''
    return dict(source=source, rule='signed-upper-bound-word-read', facts=dict(
        entry=start, countOffset=count_offset, arrayOffset=array_offset,
        indexBits=32, elementBytes=4, signedUpperBound=True, checksLowerBound=False,
        wordAddressWrap=True, coversWholeFunction=True))


def byte_or_word_range(name, raw, start):
    """Entire byte predicate OR unsigned word-range test; decode word wrapping.

    Reconstruct from native instructions, not a speculative rewrite of signed C.
    Every return, branch, delay slot, offset and constant must match the pattern.
    """
    if len(raw)!=64:return None
    w=struct.unpack('<16I',raw)
    fields=lambda x:(x>>26,x>>21&31,x>>16&31,(x&0x7fff)-(x&0x8000))
    if fields(w[0])[:3]!=(36,4,2):return None
    first=fields(w[0])[3]
    if fields(w[1])[:3]!=(9,0,3):return None
    excluded=fields(w[1])[3]
    if w[2]!=0x10430004 or fields(w[3])[:3]!=(9,0,2):return None
    equal=fields(w[3])[3]
    if fields(w[4])[:3]!=(36,4,3):return None
    second=fields(w[4])[3]
    if w[5]!=0x10620006 or w[6] or fields(w[7])[:3]!=(35,4,2):return None
    field=fields(w[7])[3]
    if fields(w[8])[:3]!=(9,2,2) or fields(w[9])[:3]!=(11,2,2):return None
    base=-fields(w[8])[3];width=fields(w[9])[3]
    if w[10:]!=(0x10400003,0,0x03e00008,0x24020001,0x03e00008,0x0000102d):return None
    if not (0<=excluded<=255 and 0<=equal<=255 and min(first,second,field)>=0 and field%4==0 and 0<=base<=32767 and 0<width<=32767):return None
    source=f'''#include "common.h"
/* Entire native predicate; unsigned subtraction retains ADDIU word wrap. */
s32 {name}(const u8 *object)
{{
    if((object[0x{first:X}]!=0x{excluded:X} && object[0x{second:X}]==0x{equal:X}) ||
       (*(const u32 *)(object+0x{field:X})-0x{base:X}U)<0x{width:X}U)return 1;
    return 0;
}}
'''
    return dict(source=source,rule='byte-predicate-or-word-range',facts=dict(entry=start,
        byteOffsets=[first,second],excludedByte=excluded,equalByte=equal,wordOffset=field,
        rangeBase=base,rangeWidth=width,wordWrapBits=32,coversWholeFunction=True))


def little_endian_word_writer(name,raw,start):
    """A complete four-byte serializer consumes all32 input bits, not just a byte."""
    if len(raw)!=56:return None
    w=struct.unpack('<14I',raw)
    if w!=(0x00051a02,0x00053e02,0x00053402,0x10800008,0x24020002,
           0xa0850000,0x24840001,0xa0830000,0x24840001,0xa0870001,
           0x0000102d,0xa0860000,0x03e00008,0):return None
    source=f'''#include "common.h"
/* SRL8/16/24 consume all incoming word bits before the byte stores. */
s32 {name}(u8 *output,u32 value)
{{
    if(output==0)return 2;
    output[0]=(u8)value;
    output[1]=(u8)(value>>8);
    output[3]=(u8)(value>>24);
    output[2]=(u8)(value>>16);
    return 0;
}}
'''
    return dict(source=source,rule='little-endian-word-writer',facts=dict(entry=start,
        inputBits=32,outputBytes=4,storeOffsets=[0,1,3,2],nullResult=2,successResult=0,coversWholeFunction=True))


def nullable_indexed_byte_lookup(name, raw, start, gp=None):
    """Recover a full word-index lookup and three native failure guards.

    The caller supplies GP only after validating the actual startup sequence.
    No unknown memory is defined or initialized by this reconstruction.
    """
    if len(raw)!=56 or gp is None:return None
    w=struct.unpack('<14I',raw)
    fixed={0:0x00052880,1:0x00852021,3:0x04a00007,5:0x10400005,
           6:0x00451021,9:0x14640002,10:0,11:0x2405ffff,12:0x03e00008,13:0x00a0102d}
    if any(w[i]!=value for i,value in fixed.items()):return None
    if w[2]&0xffff0000!=0x8c850000 or w[4]&0xffff0000!=0x8f820000:return None
    if w[7]&0xffff0000!=0x24040000 or w[8]&0xffff0000!=0x90430000:return None
    signed=lambda x:(x&0x7fff)-(x&0x8000)
    field=signed(w[2]);byte=signed(w[8]);excluded=signed(w[7])
    if field<0 or field%4 or byte<0 or not 0<=excluded<=255:return None
    address=(gp+signed(w[4]))&0xffffffff
    if address%4:return None
    symbol=f'D_{address:08X}'
    source=f'''#include "common.h"
/* Full native lookup; address arithmetic wraps in the target word width. */
extern u8 *{symbol};
s32 {name}(const u8 *object,s32 index)
{{
    s32 value=*(const s32 *)((u32)object+((u32)index<<2)+0x{field:X}U);
    if(value<0 || {symbol}==0 ||
       *(u8 *)((u32){symbol}+(u32)value+0x{byte:X}U)=={excluded})value=-1;
    return value;
}}
'''
    return dict(source=source,rule='nullable-indexed-byte-lookup',facts=dict(entry=start,
        globalPointer=symbol,gp=gp,wordOffset=field,byteOffset=byte,excludedByte=excluded,
        indexBits=32,wordWrapBits=32,negativeIndexResult=-1,nullPointerResult=-1,
        coversWholeFunction=True))


def signed_fractional_part(name,raw,start):
    """Decode both float branches, including in-place negation and subtraction.

    Negative integral inputs deliberately return1.0, matching the native code;
    this is not a replacement with floor/fmod or floating-point reassociation.
    """
    if len(raw)!=92:return None
    w=struct.unpack('<23I',raw)
    if w!=(0x44800000,0,0x46006034,0,0x45030008,0x46006307,
           0x46006024,0x44020000,0x44820000,0,0x46800020,0x03e00008,
           0x46006001,0x3c013f80,0x44810000,0x46006064,0x44020800,
           0x44820800,0,0x46800860,0x46016301,0x03e00008,0x460c0001):return None
    source=f'''#include "common.h"
/* Both native float paths, with the live input updated in the negative path. */
f32 {name}(f32 value)
{{
    if(value<0.0f){{
        value=-value;
        return 1.0f-(value-=(f32)(s32)value);
    }}
    return value-(f32)(s32)value;
}}
'''
    return dict(source=source,rule='signed-fractional-part',facts=dict(entry=start,
        inputBits=32,resultBits=32,integerConversionBits=32,negativeIntegralResult=1.0,
        keepsNativeFloatOperationOrder=True,coversWholeFunction=True))


def word_table_insert(name, raw, start):
    """Full word-table scan: remember first empty slot, then store one word.

    The final SLL2+ADDU scales the slot exactly once. Do not reproduce an
    m2c pointer expression that scales the byte displacement a second time.
    """
    if len(raw)!=108:return None
    w=struct.unpack('<27I',raw)
    expected=(0x8c8700ac,0x0000482d,0x2406ffff,0x0000202d,0x2408ffff,
        0x00e0182d,0x8c620000,0x14450003,0,0x03e00008,0x24020001,
        0x54480004,0x24840001,0x00061027,0x0082300a,0x24840001,
        0x28820010,0x1440fff4,0x24630004,0x2402ffff,0x10c20004,
        0x00061080,0x24090001,0x00e21021,0xac450000,0x03e00008,0x0120102d)
    if any(value!=expected[i] for i,value in enumerate(w) if i not in (0,16)):return None
    if w[0]&0xffff0000!=0x8c870000 or w[16]&0xffff0000!=0x28820000:return None
    field=(w[0]&0x7fff)-(w[0]&0x8000);count=w[16]&0xffff
    if field<0 or field%4 or not 1<=count<=32767:return None
    source=f'''#include "common.h"
s32 {name}(void *object,s32 identifier)
{{
    s32 *table;
    s32 *cursor;
    s32 value,index,slot,result;
    table=*(s32 **)((u8 *)object+0x{field:X});
    result=0;
    slot=-1;
    index=0;
    cursor=table;
scan:
    value=*cursor;
    if(value==identifier)return 1;
    if(value==-1)slot=(~slot==0)?index:slot;
    index+=1;
    cursor+=1;
    if(index>={count}){{
        if(slot!=-1){{result=1;table[slot]=identifier;}}
        return result;
    }}
    goto scan;
}}
'''
    return dict(source=source,rule='word-table-first-empty-insert',facts=dict(entry=start,
        tablePointerOffset=field,slots=count,elementBytes=4,indexScaleCount=1,
        emptySentinel=-1,existingResult=1,fullResult=0,coversWholeFunction=True))


def strided_mask_scan(name,raw,start):
    """Full native bounded scan; consume the current entry before advancing.

    A native negative starting index is not replaced with an invented guard.
    SLLV uses the low five shift bits, and addresses wrap at target word width.
    """
    if len(raw)!=104:return None
    w=struct.unpack('<26I',raw)
    expected=(0x00a0302d,0x28c20020,0x10400015,0x24020020,0x00061040,
        0x24070001,0x00451021,0x00021100,0x3c05001b,0x24a579c8,
        0x00a22821,0x8ca20000,0x10400005,0x24a50030,0x00c71804,
        0x00831824,0x14600007,0x00c0102d,0x24c60001,0x28c20020,
        0,0x5440fff6,0x8ca20000,0x24020020,0x03e00008,0)
    if any(value!=expected[i] for i,value in enumerate(w) if i not in (8,9)):return None
    if w[8]&0xffff0000!=0x3c050000 or w[9]&0xffff0000!=0x24a50000:return None
    address=(((w[8]&0xffff)<<16)+(w[9]&0x7fff)-(w[9]&0x8000))&0xffffffff
    if address%4:return None
    symbol=f'D_{address:08X}'
    source=f'''#include "common.h"
extern u8 {symbol}[];
s32 {name}(u32 mask,s32 index)
{{
    if(index<32){{
        const u8 *cursor=(const u8 *)((u32){symbol}+(u32)index*48U);
        do {{
            if(*(const s32 *)cursor!=0 && (mask&(1U<<((u32)index&31U))))return index;
            cursor=(const u8 *)((u32)cursor+48U);
            index=(s32)((u32)index+1U);
        }} while(index<32);
    }}
    return 32;
}}
'''
    return dict(source=source,rule='read-before-stride-mask-scan',facts=dict(entry=start,
        tableAddress=symbol,strideBytes=48,limit=32,wordWrapBits=32,shiftMask=31,
        readsCurrentEntryBeforeAdvance=True,negativeStartNotClamped=True,coversWholeFunction=True))


def derive(name, raw, start, gp=None):
    result = reverse_word_fill(name, raw, start)
    if result is None:
        result = read_modify_reset(name,raw,start)
    if result is None:
        result = bounded_word_read(name,raw,start)
    if result is None:
        result = byte_or_word_range(name,raw,start)
    if result is None:
        result = little_endian_word_writer(name,raw,start)
    if result is None:
        result = nullable_indexed_byte_lookup(name,raw,start,gp)
    if result is None:
        result = signed_fractional_part(name,raw,start)
    if result is None:
        result = word_table_insert(name,raw,start)
    if result is None:
        result = strided_mask_scan(name,raw,start)
    if result is None:
        from constant_seed import derive as constants
        result = constants(name,raw,start)
    return [result] if result else []
