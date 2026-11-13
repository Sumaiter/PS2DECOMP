# Setting up a PS2 game in PS2DECOMP

This guide is in English. PS2DECOMP's interface follows VS Code's display
language: German for a German display language, English for all other languages.
Use **Configure Display Language** in VS Code and reload to change it.

PS2DECOMP is a workbench around a game's evidence and compiler. It does not
identify the original compiler or establish function boundaries from an ISO
automatically. Set up the game's extraction, references and toolchain first.

## Required: your own game ROM / disc image

You must supply your own game ROM or disc image (for example an ISO). PS2DECOMP
does not include a ROM for any game and does not download game images or provide
links to ROM downloads. This applies to the repository, example projects,
`RAC3_PAL` / `RAC4_PAL` adapters, VSIX packages and GitHub release attachments.

Use your game project's extraction tools to create the required local reference
ELFs and other inputs from your own image. Keep the image, extracted executables
and game assets outside the PS2DECOMP source repository and release packages.
Paths such as `baserom/main.elf` below describe files you supply locally; they
are not included downloads. Test fixtures contain synthetic instructions only.

## 1. Install the extension and Python dependencies

Download `ps2decomp-<version>.vsix` from the project's GitHub Releases. In VS
Code, choose **Extensions: Install from VSIX…**, install it, then reload.
Alternatively build a VSIX from this repository with `python package_local.py`.

Create a Python 3.10+ environment **inside your game repository**:

```powershell
cd C:/projects/my-game
python -m venv venv
./venv/Scripts/python.exe -m pip install -r C:/tools/PS2DECOMP/requirements.txt
```

On Linux/macOS use `venv/bin/python`. A release also includes `requirements.txt`
inside the extension; you can install `pyelftools==0.33 rabbitizer==1.16.2`
directly if you installed only the VSIX. The reviewed C parser is bundled with
PS2DECOMP. You do not need a separately installed stock pycparser.

For automatic rough decompilation, install a local
[m2c](https://github.com/matt-kempster/m2c) checkout at
`build/ext/m2c` (or `tools/ext/m2c`) in the game repository and install its
dependencies in the same venv. Preserve its license and record the revision you
use. Manual draft comparison does not require m2c. Nothing is downloaded or
started automatically by opening a game workspace.

For related-function analysis, open a function in the workbench and choose
**Decompile together**. Select up to seven direct callees; the current function
is always included. The selection must be available under your project's manual
coordination policy and use the same canonical program. PS2DECOMP temporarily
claims all selected functions, runs one bounded m2c process and opens a separate
unsaved preview. This action does not update drafts or start automatic searches.
See [m2c analysis coverage](README.md#m2c-analysis-coverage) for supported inputs
and current limitations. No additional m2c installation is needed.

## 2. Pick the integration

| Your repository | Integration |
| --- | --- |
| Prepared RAC3 PAL repository matching the original workbench layout | [`RAC3_PAL/README.md`](RAC3_PAL/README.md) |
| Prepared RAC4 PAL repository matching the original workbench layout | [`RAC4_PAL/README.md`](RAC4_PAL/README.md) |
| Another ELF32 little-endian MIPS EE game with isolated compilation | The generic manifest described below |
| Another compiler, custom relocations, special symbols or ownership | Add a separate `GameName_PAL` adapter; see step 8 |

Keep regions/revisions in separate workspaces. PAL references never stand in for
NTSC-U/NTSC-J. Do not mark an unverified version as available.

RAC4 PAL's optional original EE296 profile requires user-supplied compiler,
runtime, assembler and helper files installed before a new automatic session.
Follow the [EE296 setup and evidence guide](RAC4_PAL/EE296_EVIDENCE.md#required-local-setup)
for exact inputs and WSL configuration. Automatic trials require a witnessed
first execution; an explicit manual `@ee296 -O2 -G0` file requires an existing
own claim and the same file/ABI validation.

## 3. Prepare a generic game repository

Use this structure as a starting point:

```text
my-game/
  ps2decomp.json
  .vscode/settings.json
  config/functions.json
  include/common.h
  tools/compile.py
  toolchain/                 # local, never committed here
  baserom/main.elf           # your own extracted reference, local only
  build/ext/m2c/             # optional local m2c checkout
  build/claims/              # shared across this game's worktrees
  build/studio/              # drafts and immutable comparisons
  build/autodecomp/          # sessions, budgets and attempt history
  venv/
```

Copy the editable files from [`examples/Game_PAL`](examples/Game_PAL).
They are templates, not extracted game data. Replace every sample address,
hash, compiler argument and contract with evidence from your exact game.

Your `include/common.h` must provide the primitive types your drafts use, with
sizes established for the original compiler/ABI. Do not copy another game's
`long`, structure, pointer or calling-convention assumptions without proof.
Guarded rules may deliberately refuse a different header; do not weaken those
guards just to obtain more variants.

Rough-source normalization preserves existing type definitions and includes.
It does not define `s128` or `u128`: a type name alone establishes neither its
width nor its kind or signedness, and `long long` can differ between compiler
profiles. Supply an evidence-backed definition in the project's own source or
headers. Missing definitions remain compiler errors; normalization does not
guess header contents to make a draft compile. Successful compilation still
requires the normal type/ABI review for each selected profile.

## 4. Describe original references, compiler inputs and functions

A generic `ps2decomp.json` looks like this (replace the placeholder hashes):

```json
{
  "schemaVersion": 1,
  "name": "My Game PAL",
  "version": "pal-revision-1",
  "adapter": "generic",
  "mainProgram": "main",
  "programs": {
    "main": {
      "elf": "baserom/main.elf",
      "sha256": "REPLACE_WITH_64_HEX_DIGITS",
      "eeSections": [".text"],
      "eeSectionsEvidence": "REPLACE_WITH_EVIDENCE_FOR_ALL_EE_CODE_SECTIONS",
      "symbols": { "D_00300000": "0x00300000" }
    }
  },
  "functions": "config/functions.json",
  "compileCommand": ["{python}", "tools/compile.py", "{source}", "{object}", "{name}", "{program}"],
  "inputs": {
    "tools/compile.py": "REPLACE_WITH_64_HEX_DIGITS",
    "include/common.h": "REPLACE_WITH_64_HEX_DIGITS",
    "toolchain/bin/ee-gcc.exe": "REPLACE_WITH_64_HEX_DIGITS"
  },
  "coordination": { "mode": "local" }
}
```

Calculate hashes, for example with PowerShell:

```powershell
(Get-FileHash baserom/main.elf -Algorithm SHA256).Hash.ToLower()
```

For each reference program, `eeSections` lists all of its evidenced EE code
sections; `eeSectionsEvidence` records how their architecture was established.
Replace the sample `.text` list and evidence placeholder with your game's
actual section inventory. ELF execute flags alone do not distinguish EE code
from executable VU microcode. Include only EE sections in this list.

Incoming-boundary checks require these fields and a statically placed `ET_EXEC`
reference at every function placement. Missing architecture evidence or a
relocatable reference holds an otherwise exact result for manual review.
External conditional branches into a candidate and direct entries into its
interior also require review. See [incoming boundary evidence](BOUNDARY_EVIDENCE.md)
for supported entry calls, tail transfers and the diagnostic's limits.

`inputs` is a map of repository-relative files to their SHA-256 hashes. Include
the compiler, assembler, compiler stages, specs, wrapper scripts, linker inputs
and other files that can affect compilation. Headers and adapter sources are
also pinned into each session. A changed input stops an existing search; make
an explicit new session after a deliberate toolchain change.

`compileCommand` is an argument array. It runs without a shell, from the game
root. Supported placeholders are `{python}`, `{root}`, `{source}`, `{object}`,
`{name}` and `{program}`. The wrapper must:

1. Compile exactly the supplied candidate file with evidenced game/file flags.
2. Write a fresh **ELF32 little-endian MIPS relocatable object** at `{object}`.
3. Preserve the requested function symbol and its size.
4. Emit compiler output normally and return a failing exit code on any error.
5. Leave game sources, references, existing drafts and toolchain files unchanged.

The sample `tools/compile.py` demonstrates the argument contract. Adapt the
compiler path, assembler stages, include directories and flags to your game.
Do not assume modern GCC can match an older PS2 compiler. Historical `cflags`
comments are accepted only if they exactly match the manifest's `sourceFlags`;
they are not executed as arbitrary command-line options.

`config/functions.json` is a JSON array:

```json
[
  {
    "name": "func_00100000",
    "placements": [
      { "program": "main", "section": ".text", "address": "0x00100000", "size": 12 }
    ]
  }
]
```

Names currently use `func_XXXXXXXX` or `func_LNN_XXXXXXXX`. Addresses and sizes
must be aligned to 4 bytes. Establish the **complete** extent, including delay
slots and all exits, from native evidence; a nearby symbol is not enough.
Use `"remnant": true` for fragments. Add `"status": "match"` or `"published"`
and `"source": "src/path.c"` only for functions already independently verified
by your game project; this excludes them from new automatic work.

An evidenced declaration may be stored as `"declaration": "...;"` together
with `"evidence": ["path or description of caller/callee evidence"]`. Missing
contracts stay unresolved. The workbench never proves an ABI from a name alone.

For overlays/clones, add each real executable under `programs` and every
catalogue-proven copy under the function's `placements`. Symbol addresses are
resolved separately per program. Configure explicit external data symbols under
each program's `symbols`; a name such as `D_00300000` is not automatically treated
as proof of an address. For GP-relative code, add both `gp` and `gpEvidence` to
that program after validating its startup/loader context. Missing GP evidence
blocks those operations.

The generic relocation gate supports `R_MIPS_32`, `R_MIPS_26`, paired
`R_MIPS_HI16`/`R_MIPS_LO16`, and external `R_MIPS_GPREL16`. RELA, local GPREL16
requiring original object GP, compiler-specific relocations and unsupported
layouts fail closed. Use a dedicated game adapter for them. Additional allocated
data/code outside the single function prevents a strict technical match and
requires explicit ownership handling.

## 5. Configure claims and upstream coordination

For a standalone project with no public reservation system, explicitly choose
`"coordination": { "mode": "local" }`. This still enforces shared local claims.
Do not choose local-only mode to bypass a collaborative project's policy.

For shared upstream work, use a read-only project hook:

```json
{
  "mode": "command",
  "command": ["{python}", "tools/check_reservations.py"]
}
```

The command must read the current upstream rules, all active reservations,
integrated function coverage and your authorized contributor scope, then print:

```json
{
  "complete": true,
  "version": "pal-revision-1",
  "blocked": ["func_00100020"],
  "allowed": ["func_00100000"]
}
```

Return failure or `complete: false` when freshness, permissions or coverage is
uncertain. The version must match the manifest exactly. Every selectable name
must be in `allowed`; omitted names and physically overlapping reservations are
blocked. The hook must not publish comments. PS2DECOMP carries no authorization
to change anyone's GitHub comments. RAC adapters use their native read-only
coordination gates instead of this generic hook.

Before parallel worktree use, make every worktree's `build/claims` refer to the
main repository's `build/claims` through a directory junction/symlink. The tool
checks this and refuses disconnected worktrees. Never release another owner,
including a paused/completed automatic search. The CLI
`python claims_cli.py --repo <game> claim <owner> <function>` is available for
manual contributors; `release` accepts only the recorded owner and refuses
automatic owners, which must be released through the application.

## 6. Open the workspace and check the integration

Open the game folder in VS Code and trust it before running compilers. Optional
workspace-only `.vscode/settings.json`:

```json
{
  "ps2decomp.repository": ".",
  "ps2decomp.openAutomaticOnStartup": false
}
```

For a nonstandard venv set `ps2decomp.python` to its executable. Use a workspace
or workspace-folder setting; a global repository path is deliberately ignored.
The repository must be inside an open workspace. With several game folders,
configure exactly one repository or open separate windows. Reload after changing
the selected project or adapter.

From a PS2DECOMP source checkout, validate the setup without starting a search:

```powershell
C:/projects/my-game/venv/Scripts/python.exe project_setup.py --repo C:/projects/my-game
```

Check a known public matching function in an isolated draft first. Change its
body and an external symbol target as negative controls: both must be rejected.
For multiple placements, validate every copy. Compare full game builds using the
game's own tooling; PS2DECOMP's technical check is only one part of verification.

## 7. Use the workbench

Open **Ctrl+Shift+P → PS2DECOMP**. The manual workbench creates drafts under
`build/studio/<function>/draft.c`. **Compare** (`Ctrl+Alt+B`) snapshots the current
editor contents and invokes the configured compiler. Original game sources are
not overwritten. Applying a new rough draft requires an explicit action and
backs up the previous editor contents.

In the automatic panel, use **New search** to choose game versions, filter and
load functions, select candidates and set the per-function budget. **Searches &
review** shows saved sessions, progress and results; existing sessions open here.
An empty workspace opens on **New search**. **Deferred** collects cases retained
for later work. Switching tabs does not start or resume a search.

In each result row, expand **Diagnostics & history** for detailed evidence.
**More actions & reservations** contains retry and bulk handoff controls. The
tabs support Left/Right, Home and End keys; both panels follow the editor theme
and adapt to narrow editor splits.

Automatic search starts only after selecting functions and pressing Start. The
default budget remains 16 workers, 30 compiler attempts and 90 seconds per
function. Sessions and `REVIEW.md` are stored in `build/autodecomp/<session>`.
Claims persist for queued, paused and completed jobs. Use **Hand off for review**
explicitly before a person/agent claims an automatic result for further work.

To combine new work and released retries, select **Unclaimed and open results
including deferred cases / compiler errors** under **Functions**, then load and
mark the desired rows. The load count covers both groups together: open results
are listed first, followed by new unclaimed functions. Reserved cases and
technical matches are excluded; release an unfinished case through its review
handoff before retrying it here. An explicit Start creates one session and
copies the retry histories and best sources while keeping their original runs.

Set **Function size from (bytes)** and **to (bytes)** before loading functions.
Both bounds are inclusive; the default remains **8–640 bytes**. The workspace
remembers the range. It filters new free functions, released results loaded for
a new retry session, and the manual buildable-candidate picker. Changing a bound
invalidates the current list: reload it before starting a selection. Existing
sessions retain their saved range and budgets when resumed or retried in place.

**Size preset** offers these editable starting points:

| Category | From (bytes) | To (bytes) |
| --- | ---: | ---: |
| Tiny | 8 | 64 |
| Small | 65 | 256 |
| Medium | 257 | 640 |
| Big | 641 | 2048 |
| Giant | 2049 | 32768 |

These are size recommendations, not difficulty or success ratings. Actual MIPS
instruction windows still require alignment and at least 8 bytes. Larger ranges
can take longer to screen and search; control-flow preflight supports windows up
to **32768 bytes** and keeps its existing safety checks. Selecting Giant does not
make unsupported code eligible. Choose **Custom range** or edit either bound to
use your own interval.

Paused jobs never restart simply because VS Code opens. Deferred/blocked cases
are skipped in ordinary follow-up runs. Existing drafts and recorded unsuccessful
variants are preserved. Logs for your independent reviews belong under
`build/verifylog/<batch>/<function>.log` and stay local.

A pause preserves the current pass's consumed time and attempt count. All seeds,
variants and compiler/availability subprocesses share its remaining budget;
resuming does not grant another full time limit. The queue uses completed
monotonic segments, so time spent paused is excluded. Old executed jobs with an
unknown time history, interrupted active segments and inconsistent records cannot
resume automatically. Their claims and evidence remain intact. Use an explicit
retry to archive that pass and begin a new one with the same configured limits.
Only an old queue with no execution markers/artifacts can start from zero.
Each function's pass stays bound to its original pinned inputs. Retrying other
selected jobs does not authorize that pass to resume with newer compiler/checker
inputs. An unknown or changed provenance requires its own explicit retry; old
snapshots and negative history remain available.

Review released candidates scoring at least 70 first. Scores are search hints,
not percentages of correctness. A technical match still needs independent byte,
relocation, type/ABI, ownership, boundary and semantic review and full game builds.

## 8. Add a game-specific adapter and contribute improvements

Create a folder such as `MyGame_PAL` in PS2DECOMP when manifest integration is
insufficient. Its `__init__.py` exports:

- `version_availability(repository)`: version rows with `id`, `label`,
  `available` and `reason`, based on actual local evidence.
- `create_engine(repository, settings, version=None)`: a compatible engine
  implementing the editor/search contract. Prefer deriving from
  `project_engine.Engine` and overriding the smallest necessary methods.
- `coordination_state(engine)`: a fresh `(blocked_names, allowed_names)` pair;
  `allowed_names=None` is appropriate only when the project's policy permits it.
- Optional `candidate_coordination_state(engine)`: a fresh pair for read-only
  candidate discovery when a project's policy permits browsing unclaimed scopes.
  Without this hook, discovery uses `coordination_state` unchanged. Session
  creation, manual compilation and resume always use `coordination_state`.
- An engine may implement `additional_search_children(name, source, variants)`
  to supply bounded adapter-owned candidates. These enter the ordinary source
  deduplication, history and budget queue; the hook cannot approve a function.
  Check the source shape before reading additional references, and bind every
  native placement/target used as evidence. Other adapters do not inherit it.

Set `adapter` to the folder name and `coordination.mode` to `native`. Keep
compiler profiles, addresses, symbol mapping, GP, catalogue/overlay and region
rules inside that folder. Existing `RAC3_PAL` and `RAC4_PAL` provide concrete
integrations; their exact repository layouts are prerequisites, not templates
whose values should be blindly reused.

`RAC3_PAL` and `RAC4_PAL` additionally support an explicitly authorized
`coordination.workflow: "local-first"` workflow; see the
[RAC3 guide](RAC3_PAL/README.md#explicit-local-matching-workflow) and
[RAC4 guide](RAC4_PAL/README.md#explicit-local-matching-workflow).
Free functions can be claimed and compiled locally before public
reservation. Fresh foreign scopes and integrated functions still block work.
Only independently reviewed results selected for a PR are reserved publicly,
after checking the ticket again. The default remains `public-first`; other
adapters do not inherit this option.

Send game-specific additions as PRs for that folder. Contribute shared
improvements through PRs only when they are usable across all games, supported
by real compiler/relocation evidence and meaningful counterexamples. Follow
[AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Only the setup command appears | Open the game folder containing `ps2decomp.json`, configure exactly one workspace repository and reload |
| Python missing | Create the game-local venv or set `ps2decomp.python` |
| Hash differs | Verify exact revision/region and compiler files; correct deliberately and start a new session |
| No automatic rough draft | Install local m2c and its dependencies; inspect the saved m2c log |
| No selectable functions | Check catalogue completeness, claims, current upstream scopes, existing drafts and deferred history |
| A byte score of 100 does not pass | Inspect unresolved targets, all placements, extra definitions, data sections and independent review obligations |
| A transformation is absent | Its header/type/ABI/effect guards may deliberately exclude this game |
| Interface language did not change | Change VS Code's display language and reload the window |

For backend errors open **Output → PS2DECOMP Workbench** (German: **PS2DECOMP
Werkbank**). Raw compiler and evidence logs preserve their original contents.
