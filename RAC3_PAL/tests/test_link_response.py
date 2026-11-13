"""Linker transport preserves arguments, order, failures and file lifetime."""
from pathlib import Path
import shlex
import tempfile
import unittest

from RAC3_PAL.link_response import quote_argument, response_runner


class LinkResponseTests(unittest.TestCase):
    def test_gnu_quoting_round_trip(self):
        args = ['-r', '-o', 'an output.o', r'C:\a dir\input.o', 'quote"name.o', '']
        self.assertEqual(shlex.split('\n'.join(map(quote_argument, args))), args)

    def test_long_link_uses_only_one_response_file_preserving_all_arguments(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            linker = root / 'ee-ld.exe'
            args = [str(linker), '-r', '-o', 'out.o', 'input with space.o', r'C:\a\b.o']
            seen = []
            def run(command, **kwargs):
                self.assertEqual(command[:4], args[:4])
                path = Path(command[4])
                self.assertTrue(path.is_file())
                text = path.read_text()
                self.assertTrue(text.startswith('INPUT(\n') and text.endswith('\n)\n'))
                self.assertEqual(shlex.split(text[len('INPUT(\n'):-len('\n)\n')]), args[4:])
                seen.append(path)
                self.assertEqual(kwargs, {'check': True})
                return 'native-result'
            self.assertEqual(response_runner(run, linker, root, limit=1)(args, check=True), 'native-result')
            self.assertEqual(len(seen), 1)
            self.assertFalse(seen[0].exists())

    def test_small_and_other_program_commands_are_unchanged(self):
        seen = []
        runner = response_runner(lambda command, **kwargs: seen.append(command), 'ee-ld.exe', '.')
        small = ['ee-ld.exe', '-r', 'a.o']
        runner(small)
        other = ['ee-gcc2953.exe', 'x' * 40000]
        runner(other)
        self.assertIs(seen[0], small)
        self.assertIs(seen[1], other)

    def test_native_failure_keeps_its_exception_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            class NativeFailure(Exception):
                pass
            def run(command):
                self.assertTrue(Path(command[4]).exists())
                raise NativeFailure('link failed')
            with self.assertRaisesRegex(NativeFailure, 'link failed'):
                response_runner(run, root/'ld.exe', root, limit=1)([str(root/'ld.exe'), '-r', '-o', 'out.o', 'a.o'])
            self.assertEqual(list(root.glob('*.ld')), [])

    def test_other_linker_forms_remain_unchanged(self):
        seen = []
        runner = response_runner(lambda command: seen.append(command), 'ee-ld.exe', '.', limit=1)
        for command in [
            ['ee-ld.exe', '-T', 'game.ld', '-o', 'game.elf'],
            ['ee-ld.exe', '-r', '-o', 'out.o', '--whole-archive'],
            ['ee-ld.exe', '-r', '-o', 'out.o', '-an-option.o'],
        ]:
            runner(command)
            self.assertIs(seen[-1], command)

    def test_control_characters_are_rejected(self):
        for bad in ['a\nb.o', 'a\rb.o', 'a\x00b.o']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                quote_argument(bad)
