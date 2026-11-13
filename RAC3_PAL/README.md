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
