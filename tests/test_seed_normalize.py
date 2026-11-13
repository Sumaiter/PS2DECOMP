"""Normalization preserves type ownership; these tests do not infer an ABI."""
import struct
import unittest

from seed_normalize import normalize
from RAC4_PAL.compiler_variants import generate as compiler_variants


class ExtendedIntegerOwnership(unittest.TestCase):
    def unchanged(self, source):
        for generated in (False, True):
            with self.subTest(generated=generated):
                result, notes, unresolved = normalize(source, generated=generated)
                self.assertEqual(result, source)
                self.assertEqual(notes, [])
                self.assertEqual(unresolved, [])

    def test_missing_signed_and_unsigned_types_are_not_invented(self):
        # Missing definitions remain compiler errors, rather than silently
        # adopting a width from the host or a different compiler profile.
        for name in ('s128', 'u128'):
            with self.subTest(type=name):
                self.unchanged(name + ' example(' + name + ' value) { return value; }\n')

    def test_primitive_owner_definitions_keep_their_actual_meaning(self):
        for name in ('s128', 'u128'):
            for underlying in ('int', 'unsigned int', 'long', 'long long',
                               'unsigned long long', 'float', 'void *'):
                with self.subTest(type=name, underlying=underlying):
                    self.unchanged('typedef ' + underlying + ' ' + name + ';\n' +
                                   name + ' example(' + name + ' x) { return x; }\n')

    def test_compound_and_attributed_typedefs_never_receive_a_competing_alias(self):
        for declaration in (
            'typedef struct { unsigned int words[4]; } s128;',
            'typedef union { int signed_word; unsigned int word; } u128;',
            'typedef int s128 __attribute__((vector_size(16)));',
            'typedef unsigned int u128 __attribute__((aligned(16)));',
            'typedef int (*s128)(int);',
            'typedef int s128[4];',
        ):
            with self.subTest(declaration=declaration):
                self.unchanged(declaration + '\nint example(void) { return 0; }\n')

    def test_foreign_and_nested_header_contracts_are_left_to_the_compiler(self):
        for header in ('common.h', 'foreign.h', 'nested/owner.h'):
            with self.subTest(header=header):
                self.unchanged('#include "' + header + '"\n'
                    's128 example(u128 *p) { return (s128)*p; }\n')
                self.unchanged('#include "' + header + '"\n'
                    'typedef owner_integer s128;\ns128 example(s128 x) { return x; }\n')

    def test_macros_and_conditional_definitions_are_not_overridden(self):
        self.unchanged('#define s128 owner_integer\n'
                       's128 example(s128 x) { return x; }\n')
        self.unchanged('#if OWNER_WIDE\ntypedef long long s128;\n'
                       '#else\ntypedef int s128;\n#endif\n'
                       's128 example(s128 x) { return x; }\n')

    def test_comments_strings_and_unused_types_do_not_trigger_completion(self):
        self.unchanged('/* s128 u128 */\n// u128\n'
                       'const char *example(void) { return "s128 u128"; }\n')
        self.unchanged('typedef struct { int word; } s128;\n'
                       'int example(void) { return 0; }\n')

    def test_explicit_flags_and_raw_long_long_are_preserved(self):
        for flags in ('-O2 -G0', '@ee296 -O2 -G0', '@ee29-01 -O2 -G0 -Wa,-G0'):
            with self.subTest(flags=flags):
                self.unchanged('/* cflags: ' + flags + ' */\n'
                    '#include "common.h"\nlong long example(long long x) { return x; }\n')
                self.unchanged('/* cflags: ' + flags + ' */\n'
                    '#include "common.h"\ns128 example(s128 x) { return x; }\n')

    def test_normalized_seed_cannot_carry_an_invented_alias_to_a_profile_child(self):
        source = '#include "common.h"\ns128 example(s128 x) { return x; }\n'
        normalized, _, _ = normalize(source, generated=True)
        # Synthetic COP1 div.s only activates the existing profile selector.
        children = list(compiler_variants(normalized, None, struct.pack('<I', 0x46000003),
                        status=dict(available=True, abiVerified=True)))
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0][1],
            '/* cflags: @ee29-01 -O2 -G0 -Wa,-G0 */\n' + source)
        self.assertEqual(normalize(children[0][1], generated=True)[0], children[0][1])

    def test_field_translation_preserves_the_declared_type_without_defining_it(self):
        for name in ('s128', 'u128'):
            source = 'void example(void *p) { M2C_FIELD(p, ' + name + ' *, 16) = 0; }\n'
            result, notes, unresolved = normalize(source, generated=True)
            self.assertEqual(result,
                'void example(void *p) { (*(' + name + ' *)((s8 *)(p) + (16))) = 0; }\n')
            self.assertEqual(len(notes), 1)
            self.assertEqual(unresolved, [])

    def test_unresolved_unknowns_still_block(self):
        result, _, unresolved = normalize('M2C_UNK example(void) { return M2C_ERROR; }\n', generated=True)
        self.assertEqual(unresolved, ['M2C_ERROR', 'M2C_UNK'])
        self.assertNotIn('typedef', result)


if __name__ == '__main__':
    unittest.main()
