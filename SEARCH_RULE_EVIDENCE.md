# Private-local and terminal-load search rules

These rules offer bounded C candidates. They do not approve a function, infer a
missing ABI, edit compiler flags, change a budget or bypass negative history.
The normal compiler, every-placement relocation checks and independent review
remain mandatory. A search score is not a correctness percentage.

## Shared C rules

| Rule | Preserved behavior | Important exclusions |
| --- | --- | --- |
| Private scalar definition retarget | Both assignment conversions and the existing arithmetic tree; a temporary's sole consumer reuses an equivalent private local | Different actual scalar types, address escape, live temporary, earlier target use, calls, memory consumers, control-flow entry, attributes and qualifiers |
| Private integer default after callback | The same call and arguments are evaluated once, before an unobservable private literal assignment; comparison and branch body stay intact | Non-`int` return/default types, unsigned/wide/pointer results, shadowed call names, escaping/static locals, effectful defaults, hidden typedef attributes and literals outside the guaranteed C signed-int range |
| Explicit primitive load in the existing terminal-return join | The chosen branch still performs its original load/address evaluation once, after its guard | Inferred pointer types, aggregate/pointer-to-pointer loads, qualified types, address mutation, line/builtin macros, nonterminal arms and extra branch effects |

Actual typedef definitions are read from the selected project's header. A name
such as `Real`, `word32` or `f32` does not establish its width or precision.
The new rules accept only a complete typedef-only `common.h`, optionally with
an ordinary include guard, `#pragma once` and the standard null-pointer macro.
Token-changing macros, conditional definitions, nested includes, extra header
declarations or a prelude that differs from the header keep these cases manual.
This deliberately misses some valid headers; it does not guess preprocessing.

The parser also rejects displaced/duplicate compiler-profile comments and line
splices. Otherwise regenerating C could activate a previously inactive profile.
Unknown attributes, alias cycles and ambiguous aliases fail closed.

The private integer callback rule and terminal-return join inspect identifiers
throughout the translation unit before offering a candidate. Source-context
builtins such as `__LINE__`, `__FILE__` and `__COUNTER__` keep the case manual,
including when they occur in global initializers or array bounds: regeneration
can change their values outside the function too. The same spellings inside
comments or string literals do not trigger this identifier guard.

## Adapter-owned native evidence

`RAC4_PAL` additionally shortens the lifetime of a private signed-integer literal
when all current native placements put that literal after the same first direct
call. The source transformation is independently guarded: the local cannot
escape or participate in the call, and types, values, accesses and call order
are unchanged. The adapter validates its complete primary/placement set, every
reference and actual symbol target before accepting the native pattern. A call
delay slot executes before the callee and cannot count as a late assignment.

The shared engine invokes an optional `additional_search_children(name, source,
variants)` hook. Other adapters do not inherit RAC4's native policy or region.
The pure source shape is checked before additional catalogue/ELF reads. See
[`RAC4_PAL`](RAC4_PAL/README.md) for the integration and its limits.

## Compiler evidence

Two kinds of evidence are intentionally separate:

1. [Synthetic compiler examples](tests/evidence/private-local-compiler.json):
   eight before/after pairs, compiled with an unchanged SN EE GCC 2.95.3 v1.36
   pipeline. Sixteen real compiler invocations cover float/double alias headers.
   Six pairs retain identical code. The integer-default pairs shrink from
   60 to 48 bytes in each context. External relocation symbols/types are
   unchanged. These are tool-authored examples, not game functions or a retail
   comparison. Object/file hashes are observations of that run; LF-normalized
   source hashes allow cross-platform source reproduction.
2. [Retained PAL review metadata](RAC4_PAL/evidence/review20.json): four actual
   reviewed compiler pairs, with source/object/result hashes and unchanged
   relocation inventories. The retarget pair reaches an exact instruction match;
   the other three improve heuristic scores and still need function review.
   The native lifetime evidence includes 15 checked PAL placements/targets.
   No proprietary source snapshots, objects, extracted executables or logs are
   distributed here. Final native ownership/type approval is separate.

The synthetic examples can be reproduced with a user-supplied compiler wrapper:

```text
python tests/compile_rule_examples.py --command-json command.json --cwd /path/to/compiler-project --output build/new-compiler-check
```

`command.json` is an argv array containing `{source}` and `{object}` placeholders,
for example `['bash', 'tools/cc.sh', '{source}', '{object}']` written as valid JSON
with double quotes. The script generates only synthetic headers/C, never loads
a ROM, never executes the compiled code, and refuses an existing output folder.
Keep its objects and logs local. Use the project's fixed wrapper/flags and
record their identity when reproducing; equal hashes across different wrappers
are not promised.

## Validation

The tests cover different header names and underlying types, hidden qualifiers
and typedef attributes, callback shadowing, macro-altered headers, line-sensitive
expressions, assignment conversions, live values, every-copy native disagreement,
wrong targets, incomplete CFGs and call-delay ordering. A persistent-queue test
uses an explicitly synthetic compiler to check priority within a two-build
budget. Generic projects never gain the PAL native hook. Existing budgets,
claims and explicit review handoff are retained.

The CLI also emits ASCII-safe JSON so a strict Windows output code page cannot
crash after a completed search. Tests round-trip Unicode and a large response;
the decoded values and stored evidence are unchanged.
