# Extended integer type ownership

The shared rough-source normalizer previously inserted `typedef long long s128`
and its unsigned counterpart whenever it saw the corresponding name without a
simple source-local typedef. This assumed both the compiler ABI and ownership
of the name. It could also add a competing definition beside a header-defined
type or a structure typedef containing an inner semicolon.

Normalization now preserves these names and their existing definitions. A
missing definition reaches the existing compiler error gate. This deliberately
removes automatic completion of an unproved type. It does not infer a new type,
change a profile, certify an existing definition, or rewrite old snapshots.

## Local synthetic compiler evidence

Independently authored probes used the prepared RAC4 PAL repository's unchanged
whole-file compiler wrapper, actual `common.h`, and `-O2 -G0`. The header defines
`s64` as `long`. Explicit probe aliases used `long long` and `unsigned long long`;
their names were chosen by the probe and do not establish a general meaning for
any project-owned `s128` or `u128`.

| Measured type | SN 1.36 size / alignment | EE296 size / alignment | EE 2.9-991111-01 size / alignment |
| --- | --- | --- | --- |
| `int`, pointer | 4 / 4 | 4 / 4 | 4 / 4 |
| `long`, header `s64` | 8 / 8 | 8 / 8 | 8 / 8 |
| `long long`, signed and unsigned probe aliases | 16 / 16 | 8 / 8 | 8 / 8 |
| Structure containing `char`, then `long` | 16 / 8 | 16 / 8 | 16 / 8 |
| Structure containing `char`, then `long long` | 32 / 16 | 16 / 8 | 16 / 8 |

Each successful object supplied 18 independent `sizeof`/`__alignof__`
measurements through character-array symbol sizes. Signed and unsigned alias
copies used 16-byte loads/stores under SN and 8-byte loads/stores under the two
EE profiles. All actual relocation sections were inspected: these probes have
no relocations. The unchanged source compiled without warnings on all three
profiles. This is a positive example for explicit SN aliases and a counterexample
to treating the same alias spelling as a portable 128-bit contract.

A follow-up matrix compiled the normalized sources unchanged. Missing signed
and unsigned definitions failed on all three profiles (six expected errors).
Explicit 32-bit owner aliases, structure/union definitions, and `long long`
aliases compiled on all three profiles (nine objects); their measured layouts
were preserved. A foreign header with a nested alias also compiled under SN.
The two EE scratch wrappers could not access that private neighboring header,
including an EE296 retry with an explicit include directory. Those two header
cases remain unverified, rather than evidence against the owner definition.
All 19 invocations, including the extra header-path failure, remain recorded
locally; no wrapper changes or additional compiler fallbacks were introduced.

The probes establish these concrete layout and copy results. They do not prove
arbitrary parameter, return, callback, aggregate, or signed-arithmetic ABI
contracts. No game function, retail instruction stream, game artifact, compiler
binary or local verification log is distributed with this evidence.

## Regression scope and limits

Synthetic tests preserve primitive, structure, union, array, function-pointer,
attributed and macro-owned types, including a type named `s128` whose underlying
type is `unsigned int`. Includes and indirect header aliases remain unchanged;
the normalizer does not attempt to resolve arbitrary preprocessing. Comments,
strings and unused typedefs cannot cause an alias to be inserted. Both signed
and unsigned names, explicit compiler flags, and a normalized seed followed by
a profile child are covered. Unknown m2c markers remain blocked.

Compiler profiles still require their existing gates and independent ABI
review. In particular, an explicit `long long` or header alias is not made
portable by this correction, and a successful compile is not an ABI certificate.
No new positive adapter provider is added. A future provider needs a proven
header/compiler contract and checks that remain effective for every selected
profile. Existing cache identity binds the shared normalizer along with the
other pinned compiler inputs; changed code cannot reuse an older pass's object
as current evidence. Historical evidence and existing search budgets are retained.
