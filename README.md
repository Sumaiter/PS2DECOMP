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

GPL-3.0-only. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
