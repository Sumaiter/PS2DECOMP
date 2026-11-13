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

Submit RAC3 PAL-specific improvements as PRs in this folder. General rules belong
in shared code only after evidence establishes applicability across games.

### Native-evidenced search recovery

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

For named `D_LNN_XXXXXXXX` globals in other levels, `check_worker.py` can now
reuse the project's unchanged `level_symbols.derive` policy on one supported
HI16/LO16 group. It requires the actual named primary address, unchanged
instruction/register fields, complete hash-checked catalogue placements, GP
evidence and valid data accesses. Conflicting targets, defined symbols,
unsupported formats and incomplete groups remain unresolved. Other instruction
bytes, literal pools and all strict placement checks still have to pass.

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
