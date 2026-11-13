# PS2DECOMP contributor and agent rules

PS2DECOMP is a game-independent PS2 decompilation workbench. Keep the shared
engine reusable across games and put game-specific integration in its own folder.

## Users must supply their own ROMs

- Never distribute a game ROM, disc image, extracted game executable or game
  asset with PS2DECOMP, including in game adapter folders, examples, test
  fixtures, VSIX packages or GitHub release attachments.
- Users must supply their own game image and extract references locally. Do not
  add ROM downloaders, download URLs, mirrors or automatic fetching of game data.
- Game adapter contributions contain integration logic and configuration only.
  Local paths and hashes may describe required inputs; they do not supply them.

## Contribute improvements through pull requests

- When a game project discovers a useful improvement for PS2DECOMP, contribute
  it back to this repository as a pull request. Include the problem, evidence,
  implementation, validation and limitations. Agents should prepare the change
  and PR as part of the authorized task; publishing a PR still follows the user's
  authorization and the contributing project's policy.
- Changes to the shared UI, queue, search rules, history, claims and verification
  framework must be usable by **all games**. Do not introduce game names,
  addresses, compiler hashes, types, GP values, initial values, region assumptions
  or project-specific source paths into shared logic.
- Put other logic in a folder named **`GameName_PAL`**, for example `RAC3_PAL`
  or `RAC4_PAL`, and submit a PR for that folder. Other regions use distinct
  `GameName_NTSC_U` / `GameName_NTSC_J` folders. Region identities are never
  interchangeable. Do not present an untested region as supported.
- Move a game-specific finding into shared code only after establishing its
  general applicability with real compiler/relocation evidence and meaningful
  counterexamples, including a different game/header/ABI context where relevant.
  A higher search score alone is not evidence of correctness or generality.
- Preserve English project documentation and English/German interface coverage.
  Update setup and adapter documentation when the integration contract changes.

## Claims, drafts and persistent searches

- Follow the game repository's current `AGENTS.md` and upstream coordination
  policy before new function analysis, editing or compilation. Public comments
  need explicit user authorization; a previous user's permission is not portable.
- Atomically claim a function under the project's shared `build/claims` before
  function work. All worktrees must use the main repository's same claim folder.
  Respect physical overlap, all placements and other contributors' public scopes.
- Automatic owners (`ps2decomp-auto-`, `rac3-auto-`, `rac4-auto-` or a project
  equivalent) retain queued, paused and completed cases until the application
  explicitly hands them off for review. Never reclaim them by age, score, process
  termination or a missing PID. Release only your own manual claims.
- Preserve manual `build/studio/<function>/draft.c` files, unsaved editor contents,
  immutable source/object snapshots, best candidates and attempt histories.
  Searches may take a manual draft only after explicit start from the editor.
- Do not restart paused batches or deleted schedules when an application starts.
  Skip `blocked` / `deferred=true` cases in normal follow-up work; retain their
  evidence and resume them only on an explicit targeted request after handoff
  and a new personal claim. Do not repeat recorded unsuccessful variants.
- Keep time and attempt limits effective across all seeds and variants. A plateau
  or a score of 80–99 does not stop a search while supported variants and budget
  remain. A score is a heuristic, never a correctness percentage.
- For manual review selection, use released candidates scoring at least 70.
  Lower scores return to later automatic search; preserve their evidence and
  release only your own manual claims. This local selection rule must not appear
  in public reservation comments.

## Verification and learning from reviews

- Technical equality does not approve a function. Before promotion or a game PR,
  verify every byte, relocation and actual symbol target at **every placement**,
  the complete function boundary, types, ABI, register/FP/stack parameters,
  return values, initial values, data ownership, aliases and runtime semantics.
  Run the applicable complete game builds and required project checks.
- Fail closed on missing/changed input hashes, unknown GP, unsupported relocation
  formats, incomplete coordination, mismatched versions or ambiguous ownership.
  Never mask a relocation as a substitute for checking its real target.
- Never invent types, values, declarations, initialization, ABI changes, address
  exceptions or assembly hacks to increase a score. Preserve floating-point
  order, NaN behavior, observable accesses and effects unless evidence proves
  the specific transformation valid.
- Every requested local-session review also examines whether demonstrated
  corrections can improve the AI-free algorithm. Implement only rules backed by
  compiler/relocation evidence and counterexamples. If no safe general rule is
  established, document that result instead of inventing one.
- New local verification logs belong in
  `build/verifylog/<batch>/<function>.log`, never beside game sources or in PRs.
  Keep referenced older logs, immutable search snapshots and historical wave
  logs at their original paths so evidence links remain valid.
- Report independently verified functions, unresolved/deferred cases and
  actually implemented algorithm improvements separately.
- Never commit game binaries, disc images, proprietary compilers, private paths,
  credentials, claims, session files, drafts or local verification logs. Keep
  license notices and third-party attribution with redistributed tool sources.
