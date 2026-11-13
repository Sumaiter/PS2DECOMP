# Incoming boundary evidence

An exact compiler object and an internally closed control-flow graph can still
describe a fragment of another function. The new shared diagnostic reads direct
control-flow edges from verified, executable, file-backed ELF32 little-endian
MIPS sections at every placement. It holds an otherwise exact result for manual
boundary review when an external non-call branch enters its start or when any
external direct edge enters its interior. It never rewrites code, joins catalogue
rows, changes declarations or increases search budgets.

## Observed compiler and reference evidence

A local PAL review reproduced an automatic technical match for a 12-byte float
zero-return window at all 14 catalogue placements. Its compiler object contained
no relocations, and the outgoing CFG returned normally. At each placement, two
conditional branches from the preceding 92-byte catalogue entry entered that
window. The combined minimum body was 104 bytes and included VU operations.
Compiling the standalone return did not establish the original function boundary
or its parameters. The diagnostic now holds all 14 windows for boundary review.

The same review independently compiled an unchanged 56-byte object method. Its
11 placements matched every byte, without relocation masks or extra data. The
native method-table and caller evidence established a separate entry, and no
external non-call or interior entry was found. This remains eligible. These
observations use original SN GCC 2.95.3 v1.36 through the game's existing build
pipeline; no compiler-output changes or new flags were introduced. Proprietary
objects, game bytes and private local logs are intentionally excluded here.

## Counterexamples and scope

The synthetic tests independently exercise ordinary JAL calls, direct entry tail
jumps, REGIMM branch-and-link calls, internal loops, impossible branches,
branch-likely/COP branches, negative displacements and calls into an interior.
An otherwise identical leaf reached by JAL remains eligible; one entered by an
external conditional branch needs review. A conditional tail call to a real
entry can also be legitimate: the finding is ambiguous boundary evidence, not a
claim that every such catalogue row is wrong.

The generic manifest fixture checks a different header/ABI and compiler protocol
without game-specific names, addresses, GP values or types in the shared rule.
All placements are mandatory. A changed reference hash rebuilds the edge index;
missing executable context fails closed. A split-ELF adapter fixture verifies
that the placement resolver, rather than just the logical program name, selects
the reference and cache identity. The exact bytes inspected must match the
manifest's expected hash, even if an earlier reference read passed. Relocatable
inputs are held rather than scanning unresolved instruction targets.

Architecture is an explicit adapter/manifest contract. Executable VU microcode
can contain words that resemble EE branches into real EE functions; ELF execute
flags do not distinguish them. Only the evidenced `eeSections` are decoded.
The mixed-architecture fixture proves that a non-EE executable section is
excluded without a shared section-name or address exception.

This is direct incoming-edge evidence only. Indirect entries, fallthrough,
embedded data in executable sections and actual caller contracts still require
independent review. Absence of a finding never certifies a boundary or semantics.
The diagnostic does not infer data aliases or relax relocation-target checks.
