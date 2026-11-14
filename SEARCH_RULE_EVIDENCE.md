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

### Materializing an existing indexed pointer base

`index_base_variants.py` can extract the existing cast in a comparison such as
`value >= ((Element *)(bytes + offset))[index]` into a local `Element *`.
The assignment stays immediately before that comparison in the same compound
block, including inside an existing gate or loop. The cast, byte expression,
index, comparison and function signature are preserved. No element width,
array length, index range, ABI or data ownership is inferred. This is a search
candidate, not an approval rule.

The rule accepts only one indexed read in a plain comparison, a known local
byte-pointer base, a constant offset and a known scalar index. Short-circuits,
ternaries, calls or mutations in the comparison, escaped/shadowed bases,
qualified or attributed types, macro-altered headers, labels and ambiguous
insertion points are rejected. The generated local uses the input cast type
verbatim, including when the same alias names a different underlying type.
Function-local typedefs are rejected because the new declaration would be
hoisted outside their original scope or binding. Context-dependent reserved
identifiers are rejected throughout the translation unit, including global
initializers. Fresh local names also exclude enum constants.

[Compiler metadata](tests/evidence/index-base-compiler.json) records eight real
before/after compilations with four independent synthetic headers: `int`,
`short`, `float` and `double` element types. Actual call/data relocation symbols,
types and encoded addends are checked; the double comparison's extra helper is
retained. These examples are not retail comparisons. A retained reviewed pair
separately established that this source form removed a one-byte register-order
difference in an 80-byte function, with all three actual placements and targets
verified. The generated AST reproduces that reviewed form after renaming only
the new local; no new game compilation or claim transfer was needed.

Reproduce the synthetic evidence using your own unchanged compiler wrapper:

```text
python tests/compile_index_base_examples.py --command-json command.json --cwd /path/to/compiler-project --output build/new-index-base-check
```

The output folder must be new. Sources, objects and logs stay local. The
runner checks the documented SN pipeline's GP or HI/LO index relocation and,
for double, its comparison helper. Another compiler may require a separately
reviewed evidence runner; the search rule itself contains no compiler profile,
instruction, address or game special case. Existing search budgets, claims,
handoff, source-fidelity checks and independent promotion requirements remain
unchanged.

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

## Materialize an existing comparison value

`comparison_value_variants.py` offers `temporary = comparison; result = temporary`
for a standalone assignment to an unescaped, unqualified local integer. A C
comparison already has type `int` and value zero or one. The new temporary has
that existing type; the destination declaration and conversion, function ABI,
operand order, conditional gate and compiler flags are preserved. The rule
never changes a draft's return type to obtain a preferred register allocation.

The assignment stays in the same compound statement, including inside a guard
or loop. Unknown or shadowed destination bindings, qualifiers, attributes,
storage classes, local typedefs, labels, macros and side-effecting comparison
operands are rejected. Assignments used as expressions and unbraced insertion
sites are left for manual review. A generated temporary is not materialized
again. Existing search deduplication, budgets, claims and promotion checks apply.

Preprocessing-active compiler options are also rejected, including command-line
macros, forced headers, include-path overrides and response/profile/specs files.
A real counterexample is a preserved `"-Dint=volatile int"` option: the original
source need not contain an `int` declaration, but the inserted declaration would
become volatile after preprocessing and add observable accesses. Visible AST
qualifier checks alone cannot exclude this. Such flags are never removed or
rewritten to make the rule apply; they require a separate preprocessing contract.
Ordinary optimization, warning and code-generation flags remain verbatim.
The same check covers visible `sourceFlags` and `compileCommand` option vectors
in `ps2decomp.json`, including projects whose draft has no flags comment. Invalid
manifest/option data disables this variant. Project options are not rewritten.
Opaque compiler wrappers still require the project's independently established
preprocessing contract; this guard does not inspect or execute wrapper internals.

[Synthetic compiler metadata](tests/evidence/comparison-value-compiler.json)
records ten unchanged-pipeline builds across five independent headers and two
existing result contracts. Byte, halfword and single-precision comparisons with
the wide result retain an extra copy. The narrow-result and double cases
produce identical text: this is a candidate rule, not a promise of improvement.
Actual call relocations, including the double comparison helper, retain their
symbols, types and zero encoded addends. A retained 36/40-byte reviewed pair
separately demonstrated the missing-copy correction; its generated AST equals
the independently verified source after renaming only the new local. No ROM,
game source, objects or proprietary compiler is distributed.

```text
python tests/compile_comparison_value_examples.py --command-json command.json --cwd /path/to/compiler-project --output build/new-comparison-value-check
```

Use a fresh output directory and your own unchanged compiler wrapper. The runner
uses the documented SN header contract (`long` wide result, `int` narrow result);
it does not infer integer widths from typedef names or certify other ABIs.
