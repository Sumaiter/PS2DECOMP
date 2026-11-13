# Contributing to PS2DECOMP

If your game project improves the workbench, send the improvement back as a PR.
Follow [AGENTS.md](AGENTS.md) and include enough evidence for independent review.

**Users must supply their own game ROM / disc image.** Never include a ROM,
ISO, extracted game executable or game asset in a PR, example, adapter, fixture,
package or release attachment. Do not add ROM download links or downloaders.
Contribute integration logic; derive reference data locally from your own image.

## Choose the correct scope

| Change | Location | Required scope |
| --- | --- | --- |
| UI, language support, queue, claims, history | Shared code | Usable by all games |
| C search transformation | Shared code only with general proof | Positive evidence and meaningful counterexamples |
| Compiler profile, GP, region, symbol or catalogue mapping | `GameName_PAL/` | The exact game and version |
| NTSC integration | `GameName_NTSC_U/` or `GameName_NTSC_J/` | Separate reference and ABI evidence |

`RAC3_PAL` and `RAC4_PAL` show how existing integrations stay separate from the
shared workbench. A game-specific discovery does not justify weakening a shared
guard. Preserve unsuccessful examples and document why a rule does not apply.

## PR checklist

- Explain the concrete problem and resulting behavior.
- State whether the change is shared or specific to a named game/version.
- For algorithm changes, include real compiler/relocation evidence and relevant
  positive/negative tests. Keep compiler flags, types and ABI constraints explicit.
- Run the Python suite, JavaScript tests and package verification. Run any
  applicable game builds locally; describe their outcomes without uploading data.
- Keep UI text available in English and German and update the setup guide.
- Include no game code snapshots, ELF/ISO files, proprietary tools, credentials,
  machine-specific paths, claims, drafts or local session/verification logs.

Do not automatically publish public coordination comments. Follow the game
project's upstream instructions, contributor scopes and authorization policy.
