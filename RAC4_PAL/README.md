# RAC4 PAL adapter

Existing integration extracted from RAC4 Werkbank 0.7.21. It includes the PAL
main/core/network mapping, overlay catalogue and aliases, original-byte and
relocation checks, and the independently gated original compiler profiles.

The private-local rules reviewed in Werkbank 0.7.22 are now integrated. Shared
source-only rules live in the common engine; this adapter alone supplies the
native-backed constant-lifetime rule through `additional_search_children`.
Its lazy gate verifies the complete placement set, PAL reference identity and
each actual direct-call target before reading the ordering hint. Unknown
targets, a disagreement in any copy, an incomplete CFG or a constant in the
call's delay slot rejects the candidate. No original types/values or compiler
profiles are inferred. See [rule evidence](../SEARCH_RULE_EVIDENCE.md) and
[PAL metadata](evidence/review20.json); hashes are evidence identities, not
bundled game data. Native block reordering remains unimplemented because a
general compiler-call/inline provenance contract is not yet established.

An optional original EE296 whole-file alternative now uses the same adapter
hook for a conservative complete, read-only pointer-loop form. It preserves
the C source and existing explicit flags. Profile files, headers and support
code must be pinned before the automatic session starts; hashes, a function-free
ABI probe, every placement and a witnessed first execution gate availability.
Explicit manual EE296 files use an existing own manual claim and fresh profile
validation. See [setup, compiler evidence and limits](EE296_EVIDENCE.md).
The existing shared worker's cumulative resume-time issue remains unchanged;
only this new alternative is disabled when its first-run budget is unproven.

Configure a **prepared RAC4 decompilation repository** with:

```json
{
  "schemaVersion": 1,
  "name": "Ratchet: Gladiator PAL",
  "version": "pal",
  "adapter": "RAC4_PAL",
  "coordination": { "mode": "native" }
}
```

The adapter requires the original repository's `tools/cc.sh`, Python checking,
layout, matching-map, retail, level and assembly/catalogue helpers; PAL program
tables; `include/common.h`; source mappings; local reference ELFs and the exact
original compiler installations. Optional profiles retain their original file
hash and ABI probes. No toolchain or game data is included in PS2DECOMP.

`manual_engine.py` and `engine.py` preserve the existing native integration.
`compiler_variants.py`, `native_sd16_profile.py`, `ee296_profile.py` and `line_map.py` hold the
game-specific compiler and debug-line logic. `inputs.py` pins its dependencies.
`public_reservations.py` and `upstream_matches.py` read issue 112 and fresh
upstream `main` coverage. **Public comment writing and the original user's
account/comment identity are not inherited.** Conflicts fail closed.

Loading new candidates lists free functions outside the current contributor's
public reservation. It still excludes foreign reservations, verified results
awaiting integration (including the contributor's own), fresh upstream matches
and every physically overlapping placement. Local claims, drafts and previous
attempts retain their normal filters. Listing functions does not claim them or
start compilation. With the default `public-first` workflow, starting or
resuming matching requires the contributor's current public scope in issue 112,
plus fresh conflict and upstream coverage checks.

## Explicit local matching workflow

With the contributor's explicit authorization to use local matching before
public reservation, configure the game workspace's `ps2decomp.json` with:

```json
"coordination": { "mode": "native", "workflow": "local-first" }
```

Free functions can then be claimed and compiled locally without an existing
own public reservation. Fresh foreign reservations, unresolved scopes,
integrated functions and overlapping placements still block matching. An
ambiguous own entry also blocks an otherwise valid reservation for the same
function. The queue retains its atomic shared local claims until explicit review
handoff; manual compilation, resume and retry use the same current public checks.
Listing candidates and opening a workspace do not start work or post comments.

After independent review, reserve only the results actually selected for a PR.
`RAC4_PAL.assert_publication_ready(engine, selected_names)` rechecks the ticket
and upstream coverage, requires an own public reservation for every selected
function and enforces the 500-function capacity across existing scopes and
versions. Missing ownership produces a local comment proposal and rejects
publication; it never posts a comment. Check conflicts again after publishing
the authorized reservation, then complete the project's full verification and
build requirements. Local claims and search scores do not approve a function
or grant priority over a later public reservation.

The default remains `public-first`. Use `local-first` only when explicitly
authorized in place of the game's usual reservation-before-matching workflow.
Other adapters do not inherit this option. Unknown workflow values, network
failures and incomplete coordination fail closed.

NTSC-U remains unavailable: the PAL adapter never relabels PAL bytes as US data.
Submit RAC4 PAL-specific improvements as PRs in this folder. Shared improvements
need evidence of applicability across games.
