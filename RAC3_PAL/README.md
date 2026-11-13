# RAC3 PAL adapter

Existing integration extracted from RAC3 Werkbank 0.2.2. It covers the PAL
front-end (`frontbin`), resident boot core, loader (`i5bootn`), level catalogue,
catalogue-proven copies, per-program GP and source-file compiler profiles.

Configure a **prepared RAC3 decompilation repository** with:

```json
{
  "schemaVersion": 1,
  "name": "Ratchet & Clank 3 PAL",
  "version": "pal",
  "adapter": "RAC3_PAL",
  "coordination": { "mode": "native" }
}
```

The adapter expects the original integration's `config/inventory.tsv`, target
tables, level catalogue, `tools/{inventory,level_catalogue,levels,targets,gp,
srcfiles,check,asm_filter}.py`, headers, source lists and locally prepared
`baserom/*.elf` and `build/toolchains/{sn,ee29}`. The original project's extraction
and toolchain setup must be completed first. This folder does not supply them.

`rac3_engine.py` handles repository/ABI integration; `check_worker.py` isolates
compilation and real relocation resolution using the project's checker.
`build_native.py` provides the existing full native build helper.
The comparison's relocation verdict requires every checked target to agree at
its native instruction offset as well as having no unresolved relocations.
Resolved symbol names alone do not pass that verdict. A relocation pass can
coexist with different non-relocation instructions; byte equality and the
every-placement strict check remain separate requirements.
`rac3_coordination.py` reads upstream issue 3 and fresh `master` coverage. No
comments are posted. By default (`coordination.workflow: "public-first"`),
establish your own authorized public scope before starting work.

Loading candidates also shows functions in unclaimed scopes when your contributor
comment has no active reservations. It still excludes fresh upstream matches,
other contributors' source units and overlapping placements. Loading the list
does not claim functions, compile drafts or publish a comment. Starting or
resuming work separately requires your own current public scope.

### Explicit local matching workflow

A contributor who explicitly authorizes local matching before public reservation
can configure this alternative in their game workspace's `ps2decomp.json`:

```json
"coordination": { "mode": "native", "workflow": "local-first" }
```

1. Load functions. Fresh upstream coverage, other contributors' source scopes,
   linked source units and overlapping placements are excluded.
2. Start the selected local search. The same public checks run again; the queue
   takes atomic local claims under shared `build/claims`. Your own public scope
   may be empty. Queued, paused and completed work retains its local claims until
   explicit handoff. Resume and retry check public coordination again.
3. Hand candidates off for independent review. Local claims and search scores
   do not approve results or create a public reservation.
4. Select the functions actually intended for a PR. Before integrating them,
   re-read the ticket and current upstream coverage. If another contributor has
   reserved a source unit meanwhile, leave that result out. Reserve only the
   selected results' source scopes (including linked copies) in the contributor's
   existing authorized comment; do not reserve the whole search queue.
5. `rac3_coordination.assert_publication_ready(engine, selected_names)` performs
   that fresh final check and requires public ownership of every selected source
   unit, even with `local-first`. Every selected function must belong to the
   current catalogue; iterators receive the same complete checks as lists.
   Missing ownership writes a local scope proposal
   and rejects publication. It never posts a comment. After the authorized
   reservation is published, run this check again, then complete independent
   byte/relocation/type/ABI/semantic review and the game's required full builds.

Use this option only with the contributor's explicit authorization to use this
workflow in place of the game's usual public-reservation-before-matching rule.
It changes no other adapter's policy. Network or coordination failures still
block local work; `coordination.mode` must remain `native`.

All generic rule guards remain in force. Source-file compiler flags are not
automatically changed to RAC4 profiles. NTSC-U remains unavailable.

### R5900 square-root decoding

Rough decompilation recovers single-format EE `SQRT.S` from raw `c1` or
`.word` lines only when the operand agrees with the original instruction-word
comment. R5900 uses FT (bits 20..16) as the source and FD (bits 10..6) as the
destination. The unused FS field (bits 15..11) must be zero for this recovery;
generic MIPS FS-form instructions and other formats remain unsupported.
The original listing is preserved in `original.s`; `input.s` contains the
decoded register operands and `instructionDecodings` records the evidence.
Other adapters and existing textual instructions retain their own policy.

The divider-padding diagnostic uses the same encoding check, accepting a
nonzero FT and rejecting a nonzero unused FS. Its counts do not change the
project's existing padding table or approve a configuration change.

The [evidence summary](evidence/sqrt_ft_review20261113.json) covers a complete,
hash-checked native placement and an isolated existing `sqrt_asm` helper
compiled through the project's three assembler profiles. The FT source also
agrees with [PCSX2's EE FPU implementation](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/FPU.cpp).
Synthetic m2c recovery produces `sqrtf`; that rough expression does not prove
EE floating-point behavior or authorize replacing the project's helper.
Types, ABI, ownership, complete function semantics and retail-matching builds
still require independent review. This change supplies no new game source,
compiler profile, search candidate, search budget or verdict policy.

### Unrecovered GP operands

New rough-decompile results include `gpIdentityDiagnostics` when native GP
operand groups vary across canonical copies but remain outside the existing
identity recovery. The optional observation reuses that recovery's complete,
hash/ELF/GP/CFG-checked references and requires matching instruction/register
fields at the same offsets. It records signed native GP offsets, body hashes
and whether the existing entry-to-use GP prefix proof passes. Successfully
recovered groups and fixed native SDA displacements do not produce warnings.

Branches, calls, possible aliases and every other recovery restriction remain
in force. Static operand variation does not establish runtime GP preservation,
addresses, object identity, types, values, extent or ownership. In particular,
the presence of fallback `_sda` names in a rough draft is not identity proof.
English/German warnings direct the reviewer to this distinction without
changing the normalized listing, source, declarations, ABI checks, relocation
verdict or search policy. Observation failures leave existing recovery intact.

The [read-only evidence summary](evidence/gp_diagnostics_review20261113.json)
includes three newly preserved worker cases, a mixed varying/fixed-slot
counterexample and existing compiler REL objects. Those objects remain
byte/offset-inexact; no game function was compiled, decompiled or approved by
this diagnostic replay.

Submit RAC3 PAL-specific improvements as PRs in this folder. General rules belong
in shared code only after evidence establishes applicability across games.

### Incoming registers omitted by rough context

`decompile` adds structured `contextInputs` evidence and English/German warnings
when native entry prefixes read GPR inputs under a zero-parameter declaration,
or FP inputs under a primitive declaration with no floating-value parameters.
This is a deliberately narrow diagnostic. It does not map arbitrary C types to
ABI slots, alter signatures, append arguments, omit declarations or change the
existing `abiProblems` gate, candidate sources, compiler profiles or budgets.

Positive findings require every canonical copy, freshly hash-checked ELF bytes,
a complete local CFG and eligible incoming direct-boundary evidence. Reads and
unchanged register copies are followed only through a small decoded prefix.
Branches, returns, indirect calls and unsupported instructions end the scan.
A direct call's delay slot is executed before checking incoming-register
forwarding, bounded to two calls. There is no propagation after a call or through
memory spills/reloads. Unknown callees, clobbers, changed references, missing
copies and ambiguous boundaries do not supply positive evidence.

The primary-prefix applicability filter avoids checking every reference for an
unaffected declaration; `primaryOnlySkipped` explicitly lists those cases.
Absence of findings does not validate any uninspected copy or establish an ABI.
GPR reads beyond the zero-arity case and declarations with floating formals,
varargs or custom types remain outside this diagnostic. Incoming direct-edge
checks do not exclude all indirect entries or fallthrough. Findings require
manual context review and never approve data extents, initial values or types.

The read-only native replay found two zero-arity helpers with incoming `a1/a2`
in all 51 copies each, and a pointer-only helper forwarding `f12/f13` in all
49 copies. Existing immutable m2c input, context, output and compiler objects
were unchanged. No new decompilation or compiler attempt was made. Metadata
and limitations are recorded in
[`evidence/context_inputs_review20261113.json`](evidence/context_inputs_review20261113.json).
The initial native audit hashes and scans all relevant program sections; this
can add diagnostic time independently of the unchanged m2c timeout.

### Native-evidenced search recovery

New rough-decompile results include a read-only `divisionPadding` diagnostic
and an English/German warning when the existing level `div.s`/`sqrt.s` table
is missing, malformed or inconsistent with native evidence. The diagnostic
does not insert NOPs or edit the table. It records each operation's original
offset, consecutive standalone NOP count and excluded control delay-slot NOP.
Every relevant canonical placement must pass the existing hash/ELF/GP/CFG
checks and agree on the ordered counts. A missing entry, a different count,
a different operation count and disagreement between copies are distinct
states; none is a match or configuration approval.

A primary function with no relevant operation and no table entry skips the
expensive all-copy inspection; its result explicitly records that limited
applicability scope. Missing inputs remain unavailable evidence. The m2c
input, saved drafts, sources, assembly, compiler profiles and normal search
budgets are unchanged. The same diagnostic is available without compilation:

```text
python -m RAC3_PAL.division_padding --repo C:/projects/rac3-decomp --name func_LNN_XXXXXXXX --owner YOUR_MANUAL_OWNER
```

The existing game's `tools/asm_filter.py` and canonical
`config/levels/divs_nops.txt` remain the build authority. Any table correction
needs separate project review and complete builds; a missing row alone does
not prove why every instruction differs. The actual 22-copy observation and
counterexamples are summarized in
[`evidence/division_padding_review20261113.json`](evidence/division_padding_review20261113.json).

`cop1_branches.py` and `branch_operands.py` give new rough listings explicit
local labels for supported raw COP1 and nonlink integer branches. Rabbitizer
can print their operand as a relative expression that m2c cannot parse; the
adapter now takes the mnemonic, register operands and signed PC-relative
target from the original word. COP1 false/true/likely forms and the integer
conditions supported by the existing CFG screen retain their condition,
register order, likely behavior and delay instruction. Every native word and
the subsequent EE register alias normalization remain unchanged.

The entire function must pass the existing CFG screen, and each target must
be inside its complete catalogue window. Unsupported condition-code/register
fields, link branches, external targets and incomplete/control delay slots
receive no recovered labels. Other branches and calls retain their existing
handling. Saved drafts, types, ABI, compiler profiles and budgets are unchanged.

Three immutable native input/context pairs originally failed on relative
operands. Changing only the evidenced operands made each same m2c command
emit the function, including normal, likely and mixed integer/COP1 examples.
Nineteen native placements were independently checked, and two existing real
compiler objects corroborate normal and likely COP1 encodings. VU/type
placeholders and the objects' other byte differences remain. Summary evidence
and limitations are in
[`evidence/branch_operand_review20261113.json`](evidence/branch_operand_review20261113.json).

The adapter's `additional_search_children` hook offers a bounded set of extra
search roots. They use the normal queue, negative history, claims, compiler
profiles, time limit and attempt limit. A root is not a semantic approval.

- `quadword_zero.py` recovers the project's existing `vu0.h` `qzero` helper
  only for complete 16-byte zero stores corroborated at every placement. It
  checks the actual helper, typedef/header context, fixed SN source profile,
  native pointer provenance and every zero site. It does not introduce new
  assembly. A separate, narrowly recognized whole-function LQ/clear/SQ dataflow
  captures the input before either output clear, retaining possible aliases.
  Other store shapes, 64-bit values, nonzero values, changed macros, unsupported
  profiles and unclear pointer origins stay outside this rule.
- `symbolic_memory.py` replaces existing typed literal-pointer accesses with
  named level globals or proven GP slots. The access type, runtime value
  expression and statement order remain unchanged. Every placement must prove
  the corresponding native instruction and allocated data/BSS range. Unknown
  register definitions, escaped pointers, overlapping objects, conflicting
  aliases and hidden preprocessing contexts reject the candidate. Object
  extent, allocation ownership and initial runtime values are not inferred.
- `extern_sizes.py` offers an early, permitted `.extern` size hint for an
  unchanged scalar `f32` read under the owning file's `@ps2as` profile. Its size
  must come from a hash-bound immutable object and a compatible pinned search
  record; every native placement must prove the same GP access. It never
  changes a type, compiler flag or initial value. Changed headers, compilers,
  source context or checker identity require fresh compatible evidence. Older
  sessions are not silently relabeled after a checker update.

The hook can compose independently proven zero-store recovery and scalar size
metadata from the same original source. This happens before the helper include
and metadata leave the shared AST parser's deliberately limited input subset.
The parser and search budgets are not relaxed.

Before m2c generates a new rough draft, `gp_identity.py` can name a moving
level object used by direct GP-relative byte, halfword or word memory
instructions. It reuses the checker's complete hash/ELF/GP/CFG-checked
catalogue references and requires the original opcode, base and destination
at every instruction offset. Every use of one primary address must have one
consistent target in each copy, with valid width, alignment and data rights.
Both absolute targets and GP displacements must move; a stable GP slot or a
resident address seen through different GPs does not qualify.

Only the evidenced memory operands change. GP address formation/copies,
unsupported memory widths, mixed access identities and primary or secondary
overlaps exclude recovery. A LUI high half that could form an observed target
through ORI or signed-low addressing also excludes it, as does an immediate
absolute address into that access. Calls, branches, unknown instructions and possible
GP writes before a use exclude that identity. A mismatched listing or missing
reference leaves the original rough-input path intact. Existing drafts and
history are not rewritten. The new `gpIdentities` result field records each
placement and reference hash; `gpResolutions` retains its symbol-list API.
No type, declaration, runtime value, statement order, compiler profile or search
budget is chosen by this recovery. Actual compiler relocations and every
placement still need normal verification. Two unchanged compiled objects and
the symbol-only compiler-pair counterexample are summarized in
[`evidence/gp_identity_review0896.json`](evidence/gp_identity_review0896.json).

For named `D_LNN_XXXXXXXX` globals in other levels, `check_worker.py` can now
reuse the project's unchanged `level_symbols.derive` policy on one supported
HI16/LO16 group. It requires the actual named primary address, unchanged
instruction/register fields, complete hash-checked catalogue placements, GP
evidence and valid data accesses. Conflicting targets, defined symbols,
unsupported formats and incomplete groups remain unresolved. Other instruction
bytes, literal pools and all strict placement checks still have to pass.

Direct REL `GPREL16` accesses to named level globals use the same project
policy with a separate, narrow proof. A level object can occupy different GP
slots in its copies even when their GP is identical. The checker preserves
the object's signed REL addend, verifies the primary address in its name and
checks every actual byte/halfword/word memory target, width, alignment and
write permission. Both the object and every native copy must retain the GP
value from entry through the use; branches, calls or possible GP writes in
that span are conservatively rejected. Opcode, base and destination fields
must agree at the original relocation offset.

This path requires an ELF32 little-endian MIPS relocatable object, a complete
aligned function symbol, undefined global data symbols and REL sections using
the resolver's symbol table. Locally defined or section symbols, mixed or
hidden relocation groups, allocated object data/literal pools and unsupported
formats stay outside the proof. Fixed `D_XXXXXXXX_sda` aliases retain their
slot meaning and are never remapped as moving level objects. This checker
does not rename source symbols or produce new search candidates.

Per-function target evidence does not establish allocation size, initial or
runtime values, cross-function data ownership, types/ABI or semantic safety.
Other word differences remain visible, and the complete project's symbol
derivation, links and retail comparisons remain necessary before promotion.
The two actual named objects, their fixed-SDA counterexample and all-placement
replays are summarized in [`evidence/review0896.json`](evidence/review0896.json).

Synthetic positive and negative tests live in `RAC3_PAL/tests`; the regular
`python -m unittest discover -s tests` command includes them. The local native
compiler observations and limitations are summarized in
[`evidence/review604.json`](evidence/review604.json). No game bytes, extracted
objects or proprietary compiler tools are included in those fixtures.

### Complete native Windows builds

`build_native.py` runs the prepared game's complete Python build pipeline with
its native Windows toolchain, including derived level symbols and data layout.
For example:

```text
python RAC3_PAL/build_native.py --repo C:/projects/rac3-decomp --target level_31 -j 8
```

When a project's `ld -r` object list exceeds the Windows process argument
limit, `link_response.py` passes that ordered list in an implicit GNU `INPUT`
script. The old SN linker does not accept modern argv-style `@response` files.
The implicit input script preserves the default layout, output options and
object order. Other linker forms and compiler commands are unchanged. Native
direct-link versus input-script comparison, including paths with spaces,
produced identical object bytes before complete level builds were checked.

These helpers neither supply the game/toolchain nor promote a search result.
Independent type, ABI, ownership, boundary, relocation and full-build review
is still required before a game contribution.

### m2c entry points

Editor preparation, buildability probes and automatic searches dispatch through
this adapter's `decompile` method, including its EE instruction fixes and GP
identity diagnostics. Shared `rough.decompile` must not bypass this method.
Its internal `prepare_only` mode writes the usual normalized assembly and context
without launching m2c; the shared manual group preview uses this for every selected
function before one joint invocation. Group analysis remains a separate unverified
preview and does not change recorded contracts or search sessions.
