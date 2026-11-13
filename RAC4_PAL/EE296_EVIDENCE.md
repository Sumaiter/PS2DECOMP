# Optional original EE296 profile for RAC4 PAL

The default compiler produces 80 bytes for the retained source of
`func_01EB66E8`; the original `2.96-ee-001003-1` compiler produces the native
92 bytes with no relocations. Only the whole-file compiler marker changes.
The C body, declarations and expressions are identical. Fifteen other retained
NET-section EE296 trials fail. These observations support a small optional
search alternative, rather than compiler attribution for the NET section.

[Artifact metadata](evidence/ee296-leaf-walk.json) records source/object hashes,
the default-versus-EE296 result, actual native bytes by hash, relocations and
all catalogue placements. No source snapshots, game bytes or compiler binaries
are distributed. The audit re-read existing sources and ELF objects; it compiled
no game functions. Independent semantic review and complete game builds remain
separate requirements for promoting any function.

## Selection and compilation

`ee296_leaf_walk.py` uses the existing adapter `additional_search_children`
hook. It offers one extra whole-file candidate, preserving the complete input
text and adding only `/* cflags: @ee296 -O2 -G0 */` as its first line. A source
with any explicit compiler-flags comment receives no alternative. Selection
uses C structure and native instructions, never function names, address ranges,
section identity, scores or a table of known results. The normal workbench
source keys, negative history, attempt limit and search queue handle the child.

The current evidence gate requires:

- One complete function with one `void *` argument, a `void *` return, plain
  `void *` locals, one do/while loop and one return of an existing pointer.
- A real, plain typedef-only `common.h` with no ambiguous aliases, hidden
  qualifiers, unknown macros, includes or attributes. The source includes that
  header exactly once. No globals, definitions of data, inferred structures,
  calls, writes, absolute pointer literals, intrinsics, volatile accesses or
  changed types are introduced.
- Reads at existing aligned signed-16-bit offsets, assignments to local
  pointers, and existing null guards. The native load-offset multiset must
  agree with the source.
- A complete native CFG containing only word loads, register copies, null
  branches, exactly one backward conditional branch, nops and `jr ra`. Calls,
  stores, stack/GP/AT use, address construction, FP/VU instructions and
  fragments reject the profile.
- The explicit primary placement exactly once, no duplicate placements, fresh
  PAL reference identities, complete boundaries, and identical bytes satisfying
  the gate at every placement.

The 15 actual failed EE296 artifacts all fail the source gate. Synthetic tests
also reject changed names/types of memory, calls, stores, global addresses,
different load offsets or placements, hidden qualifiers, incomplete functions
and explicit flags. A renamed synthetic function at a different address remains
eligible; neither evidence identity is an algorithm rule.

## Required local setup

Users supply their own compiler files and prepared game repository. Nothing is
downloaded or installed by the adapter. The existing game `tools/cc.sh` must
already support `@ee296` through its unchanged original compiler helper and SN
assembler route. The default profile directory is
`build/isolated/ee-gcc2.96` in that repository; `EE296_ROOT` can select another
local directory. `ee296_profile.py` lists the five exact original compiler and
private-runtime hashes required by this existing game profile.

The game helper's SN assembly pass uses the driver-selected
`ee/bin/as.exe` backend and `lib/gcc-lib/ee/2.95.3/specs`, as established by the
original driver's `-print-prog-name=as` and `-print-file-name=specs` queries.
The differently hashed `bin/ee-as.exe` is not assumed to be an alias. The
actual driver, backend and specs have explicit hashes and join the required
inputs, session pins and probe-cache key. Unverified compiler/specs/include
environment overrides disable this profile instead of changing its execution.

The hashed `local_process.py` transport also binds its actual absolute
`search_time` import, if present in that transport version. Python AST inspection
distinguishes real imports from comments or strings; the imported neighboring
`search_time.py` is mandatory even when missing. Legacy transports without the
import need no new module. Changes invalidate the availability-probe cache.
Missing imported files or a syntactically invalid import contract fail closed
before a probe.

On Windows, choose the registered WSL distribution either with the existing
`EE296_WSL_DISTRO` environment variable or a private `workbench.json` in the
profile directory:

```json
{"wslDistro": "YourRegisteredDistribution"}
```

Create the configuration and install all profile inputs **before an explicitly
started new automatic session**. The session pins the compiler/runtime files,
transport and assembler helpers, headers, adapter sources and configuration.
Files installed later, changed hashes or disappeared inputs disable the
alternative. Existing sessions are never repinned, restarted or extended by
this feature. Environment overrides and the C locale apply only to compiler
child processes.

Availability requires fresh hashes and a function-free ABI object. The probe
contains typedef assertions only: int/pointers are 4 bytes, long is 8 bytes,
and the existing signed/unsigned scalar and FP typedef widths agree. The
result must be ELF32 little-endian MIPS ET_REL, with no function/object symbols,
allocated code/data or relocations. Probe reuse requires the same input and
environment hashes plus unchanged probe-source and object hashes.

## Claims and time limits

For automatic candidates, the current holder must be the exact active automatic
owner and the PAL job must be running and non-deferred, with every required
input present in the original session pins. The adapter never acquires,
reclaims, hands off or releases any reservation.

The shared worker retains cumulative monotonic time across pause/resume
segments. This optional adapter profile additionally requires a witnessed
first execution; it does not reconstruct a budget from historical display
fields. During engine construction, before the worker starts the
job, it records only identities of fresh queued automatic jobs with no previous
execution, retry, elapsed-time or worker markers. At the child hook that
process-local witness must match the active job and worker PID. Resumed jobs,
retry passes and jobs with an unknown first-execution witness receive no EE296
alternative, even if earlier time consumption left no compiler attempt.
This conservative exclusion remains in place alongside the shared cumulative
budget and applies only to the new EE296 alternative.

The witness also records monotonic time before the original job timer starts.
Remaining time is bounded by both that monotonic witness and UTC job time, so
a clock rollback cannot refund time. An ABI probe uses at most 15 seconds and
the available original rest budget. Profile-validation time is subtracted from
the existing comparison timeout; the resulting compilation cannot extend the
job limit. No time/attempt setting, history record or paused schedule changes.

An **explicit manual whole-file** `@ee296 -O2 -G0` profile is supported under
the caller's existing own manual claim, or the editor's current lease. It needs
the same fresh file and ABI validation and exact three-token flag contract,
but no automatic session or first-run witness. A foreign, missing or automatic
claim cannot be treated as a manual lease. Existing public coordination,
handoff and independent manual-review requirements still apply.

## Limits

This is a RAC4 PAL adapter feature. It adds no shared safe flag, compiler
assignment, address exception, ABI guess, assembly fixup or source rewrite.
Manual files with additional or reordered flags fail closed. EE296 debug-line
mapping is unavailable; the canonical comparison object remains authoritative.
The source/native gates identify a supported search form, not correctness.
Other compiler families, C forms, regions and games need their own evidence.
