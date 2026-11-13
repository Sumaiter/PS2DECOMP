# RAC4 PAL adapter

Existing integration extracted from RAC4 Werkbank 0.7.21. It includes the PAL
main/core/network mapping, overlay catalogue and aliases, original-byte and
relocation checks, and the independently gated original compiler profiles.

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
`compiler_variants.py`, `native_sd16_profile.py` and `line_map.py` hold the
game-specific compiler and debug-line logic. `inputs.py` pins its dependencies.
`public_reservations.py` and `upstream_matches.py` read issue 112 and fresh
upstream `main` coverage. **Public comment writing and the original user's
account/comment identity are not inherited.** Conflicts fail closed.

NTSC-U remains unavailable: the PAL adapter never relabels PAL bytes as US data.
Submit RAC4 PAL-specific improvements as PRs in this folder. Shared improvements
need evidence of applicability across games.
