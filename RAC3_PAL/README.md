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
`rac3_coordination.py` reads upstream issue 3 and fresh `master` coverage. No
comments are posted. Establish your own authorized scope before starting work.

All generic rule guards remain in force. Source-file compiler flags are not
automatically changed to RAC4 profiles. NTSC-U remains unavailable.

Submit RAC3 PAL-specific improvements as PRs in this folder. General rules belong
in shared code only after evidence establishes applicability across games.
