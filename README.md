<p align="center">
  <img src="media/logo.png" alt="PS2DECOMP logo" width="340">
</p>

<h1 align="center">PS2DECOMP</h1>
<p align="center">From PS2 machine code to evidence-backed C.</p>

PS2DECOMP is a local VS Code decompilation workbench for PlayStation 2 projects.
It combines a C editor, native assembly comparison, bounded C variant search,
persistent queues and explicit review handoff. **No AI service is required.**

The interface follows the VS Code display language: German for `de` locales,
English otherwise. Open **Ctrl+Shift+P** and search for **PS2DECOMP**.

**Bring your own game ROM / disc image.** No game ROMs, ISOs, extracted game
executables or game assets are included in this repository, the extension or
GitHub releases. Users must provide their own game image and extract the required
references locally. PS2DECOMP does not download game images or provide ROM links.

## Get started

1. Download the `.vsix` from [Releases](https://github.com/Sumaiter/PS2DECOMP/releases).
2. In VS Code, run **Extensions: Install from VSIX…**, then reload the window.
3. Follow **[einrichtungs.md — game setup guide (English)](einrichtungs.md)**.
4. Open a trusted game workspace containing `ps2decomp.json`. Use
   **PS2DECOMP: Open workbench / select function** or
   **PS2DECOMP: Open automatic decompilation**.

The setup-guide command is available even before a game is configured. Each
workspace owns its game paths and sessions; user-level repository paths cannot
silently attach an unrelated game.

## What is included

- A manual C workbench with assembly, declarations, byte/relocation comparison
  and separate immutable candidate snapshots.
- Automatic rough decompilation through an optional local
  [m2c](https://github.com/matt-kempster/m2c) installation, followed by guarded
  C transformations and compilation with the game's configured toolchain.
- Shared atomic claims, persistent budgets, negative attempt history, source
  and object deduplication, best-candidate retention and explicit handoff.
- An inclusive workspace byte range (default 8–640) with editable Tiny, Small,
  Medium, Big and Giant recommendations for loading functions and released retries.
- A manifest-driven ELF32 little-endian MIPS adapter for new EE projects.
- Existing game integrations in **[`RAC3_PAL`](RAC3_PAL)** and
  **[`RAC4_PAL`](RAC4_PAL)**, with separate compiler, catalogue and coordination
  logic. These require their corresponding prepared game repositories.
- English/German commands, settings and panels, plus an original project logo.

This repository contains the tool, adapter source and synthetic tests. Supply
your own game repository, original references and appropriate toolchain locally.
Adding an ELF alone does not establish function boundaries, ABI or compiler flags.

Shared guard files acquire their OS lock before initializing an empty file.
[The lock evidence](FILE_LOCK_EVIDENCE.md) covers Windows contention, cleanup
failures, process crashes and retained claim ownership.

## Workbench navigation

The automatic panel separates **Searches & review**, **New search** and
**Deferred** into keyboard-accessible tabs. Existing sessions open on the progress
view; an empty workspace opens on the selection view. Switching tabs preserves
the selection and does not start or resume work. Use the arrow keys, Home or End
while a tab is focused to navigate between views.

The progress view shows processed jobs, technical matches and search-score
counts. These are not correctness percentages or independent approvals. Expand
**Diagnostics & history** for each result's compiler evidence and search details;
open details and focus on available result actions survive status refreshes.
Retry and bulk handoff actions are grouped under **More actions & reservations**. In **New search**,
filters, selection and the per-function budget are grouped before the explicit
start button. Both panels follow the editor theme and adapt to narrow splits.

The **Functions** filter also offers **Unclaimed and open results including
deferred cases / compiler errors**. It lists released unfinished cases first,
then fills the remaining load count with new unclaimed functions. Each row
identifies whether it is a retry or a new function. Only explicitly handed-off,
currently unclaimed results qualify for a retry; technical matches go to review.
Pressing Start creates one session for the selection, retaining previous best
sources and attempt histories in its copied archives. Listing alone starts no
work. Every selected case is checked again while acquiring the new session's
claims, and each job receives the configured budget for its new search pass.

## m2c analysis coverage

The adapter uses `mipsee-gcc-c`, `--valid-syntax`, `--no-cache` and evidenced
primitive prototypes through `--context`. m2c's two translation passes, unknown
type inference, control-flow reconstruction, switch detection and stack-spill
detection are enabled by default. Single-function previews pass one `-f` name.
RAC3-specific decoding and GP diagnostics are dispatched through its adapter;
RAC4 uses the shared m2c adapter with its own engine's reference and GP services.

Use **Decompile together** in the manual workbench, or the corresponding command,
to add up to seven known direct callees in the same canonical program. All selected
functions are passed to one m2c invocation with repeated `-f` flags and two passes.
Each function receives its normal adapter preprocessing. The action acquires
temporary manual claims for the whole selection, checks all reference windows,
rejects overlaps and checks pinned inputs again after decompilation. Reserved,
integrated or out-of-scope functions cannot be added. Input is capped at 64 KiB
and the group shares one 45-second preparation/decompilation deadline.

The result opens as a separate unsaved C document; the output, inputs, log and
provenance remain under `build/studio`. It does
not create search jobs, replace drafts, change contracts or approve inferred
types/ABI. A failed group can contain partial output and is labelled incomplete.
Cross-function inference is limited: a better helper prototype does not prove
that m2c recovered every forwarded caller argument.

Remaining integration gaps are full preprocessed project context (custom structs,
globals and typedefs), evidenced `.data`/`.rodata`/`.bss` input including jump
tables, explicit stack-struct context, and CFG visualization. m2c supports these,
but this workbench does not yet supply their required inputs or UI. Default
`--globals=used` emits inferred declarations; without data sections it does not
recover their initial values. Formatting switches and diagnostic options are
also not exposed as arbitrary flags. They are not prerequisites for ordinary
decompilation, and unsafe assumptions such as forcing `--void` are not enabled.

## Search budgets and resume

Each function's search pass shares its configured time and attempt limits across
all seeds, variants and pause/resume segments. Completed segments retain their
full-precision monotonic running time; paused wall time is excluded. Immutable
comparisons from the current pass can be traversed again without rebuilding or
refunding attempts. Tool setup, availability probes and compilation share the
same process-local deadline, including additional placement workers.

Each pass also retains the identity of its original pinned inputs. A selective
retry cannot transfer newer session pins to another function's earlier pass.
Current and archived cached comparisons need that job's own pin provenance,
unchanged shared checker/compiler code and intact source/object/result hashes.
Older unbound comparisons remain historical evidence rather than trusted cache
hits; unsuccessful-source history and retained best snapshots are preserved.
Pin paths are compared in full, so moving the extension installation also
invalidates its compiler/review cache conservatively.

Older executed jobs have no reliable cumulative time provenance. Resuming them,
an interrupted active segment or an inconsistent accounting record fails closed:
claims, best candidates and historical evidence remain available. A pristine old
queue with no execution markers or artifacts can start from zero. To run an
unknown/exhausted pass again, explicitly choose the existing retry action; it
archives the previous pass and creates a new budget with the same configured
limits. Opening VS Code never starts or retries a paused job.

An expired deadline prevents another tool launch and terminates an active tool
through the shared process runner. OS process startup, bounded cleanup and local
diagnostics can finish after that deadline; all elapsed segment time is retained
and no later segment receives the overrun as fresh time.

## Verification comes first

The bounded [private-local and terminal-load rules](SEARCH_RULE_EVIDENCE.md)
preserve declared types, effects and compiler flags. Their source-only part is
shared; native ordering evidence is supplied separately by `RAC4_PAL`. Unknown
header preprocessing or effects keep the case manual. Real compiler examples
and meaningful counterexamples accompany the rules.

A search score, including 100, is **not an approved decompilation**. Technical
checks are followed by independent review of types, ABI, initial values,
semantics, ownership and function boundaries, plus the game's full build checks.
Unknown relocations and incomplete coordination fail closed.

Review reports include a separate `sourceFidelity.integerPointerConversions`
diagnostic for integer expressions assigned to known local or parameter pointers.
It follows lexical declarations and typedef scopes; block-scope `extern` targets,
unknown expressions, true pointer arithmetic and potentially null constants are
outside this diagnostic. Findings do not change scores, strict checks, existing
`violations` or reviewed-seed eligibility. An empty list is not semantic approval;
`integerPointerAnalysisLimitation` records incomplete diagnostic analysis.
Read-only AST diagnostics inspect the complete supported translation unit,
including ordinary `static inline` helpers and declarations. Search mutations
and reviewed search seeds still require exactly one function. Unsupported
preprocessor inputs retain their explicit analysis limitation. A complete
diagnostic scan does not approve types, ABI, ownership, semantics or a match.

Exact objects also receive [incoming-boundary checks](BOUNDARY_EVIDENCE.md)
against every placement's verified reference ELF. External non-call branches
into the window and direct entries into its interior require manual boundary
review. Ordinary entry calls and direct entry tail jumps do not block a window.
This diagnostic does not establish ABI, ownership or complete boundaries.

Adapters whose logical programs span multiple ELFs can provide
`reference_identity(place)` to select the same file used by their byte reader.
It returns `path`, `algorithm` (`sha1` or `sha256`) and the expected `digest` from
the project's reference manifest. The diagnostic hashes the exact immutable
bytes it inspects; a separate earlier file check is insufficient. All included
adapters implement this contract. Missing identity evidence fails closed.
The identity also supplies `eeSections` and `eeSectionsEvidence`: a complete
list of the file's EE code sections and its architecture evidence. Generic
projects put these fields under each `programs` entry. Executable VU sections
must not be classified as EE merely because ELF marks them executable.
Only statically placed `ET_EXEC` references are supported by this diagnostic.

The generic adapter currently supports ELF32 little-endian MIPS EE objects and
`R_MIPS_32`, `R_MIPS_26`, paired `R_MIPS_HI16`/`R_MIPS_LO16`, and external
`R_MIPS_GPREL16` with evidenced GP. Other relocations, compiler-specific layouts,
IOP/VU code and unusual data ownership need a game adapter and evidence.

## Contribute

Read **[AGENTS.md](AGENTS.md)** and **[CONTRIBUTING.md](CONTRIBUTING.md)**.
Game projects should contribute improvements back through PRs. Shared changes
must work across games; game-specific logic belongs in `GameName_PAL` (or a
separate regional adapter). Do not submit game data or proprietary toolchains.

## Develop and package

Requires Python 3.10+ and VS Code 1.96+. The supplied game adapters currently
target the original Windows toolchains; the generic adapter accepts portable
argument-array compiler wrappers.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/*.test.js
python package_local.py
code --extensionDevelopmentPath=. C:/path/to/your/game
```

On Linux/macOS use `.venv/bin/python` for the Python commands. Packaging is
offline and reads its own VSIX metadata; no older RAC extension is required.
See the [VS Code packaging documentation](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
for the alternative `vsce` workflow.

For optional UI regression checks, install `playwright-core` without changing
the package manifest, and point `PS2DECOMP_BROWSER` at a local Chrome/Chromium
executable. For example, in PowerShell:

```powershell
npm install --no-save --package-lock=false playwright-core
$env:PS2DECOMP_BROWSER = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
node tests/webview_preview.cjs
```

This uses the actual panel HTML, scripts, styles, localization and CSP with a
synthetic VS Code message bridge and theme colors. It checks navigation, selection,
polling, handoff navigation and responsive layouts in English/German and light/dark
themes, saving screenshots under `build/webview-preview`. It does not start searches
or access a game workspace. Browser checks supplement the real extension-host
check in `tests/run_vscode.py`.

GPL-3.0-only. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
