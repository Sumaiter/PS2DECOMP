"""Game data must never enter a tool distribution, even through adapter assets."""
from pathlib import Path
import os
import tempfile
import unittest

from package_local import GAME_SUFFIXES, validate_distribution


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, contents=b'placeholder'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def test_game_images_and_archives_in_adapter_assets_are_rejected(self):
        for suffix in sorted(GAME_SUFFIXES | {'.zip', '.7z'}):
            with self.subTest(suffix=suffix):
                path = self.file('RAC3_PAL/game' + suffix.upper())
                with self.assertRaises(ValueError):
                    validate_distribution([path], self.root)

    def test_reference_directories_and_disguised_elf_are_rejected(self):
        for name, data in [('examples/baserom/input.txt', b'reference'),
                           ('media/reference.png', b'\x7fELF' + bytes(64))]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_distribution([self.file(name, data)], self.root)

    def test_source_configuration_and_original_logo_are_allowed(self):
        files = [self.file('Example_PAL/engine.py'), self.file('examples/Game_PAL/ps2decomp.json'),
                 self.file('examples/Game_PAL/include/common.h'),
                 self.file('media/logo.png', b'\x89PNG\r\n\x1a\n'), self.file('vendor/LICENSE')]
        validate_distribution(files, self.root)

    @unittest.skipUnless(os.name == 'nt', 'Windows short-path aliases')
    def test_short_path_root_and_files_use_the_same_lexical_base(self):
        import ctypes
        from ctypes import wintypes
        directory = self.root / 'Long distribution directory name'
        directory.mkdir()
        (directory / 'engine.py').write_text('pass\n', encoding='utf-8')
        short_path = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        short_path.restype = wintypes.DWORD
        size = short_path(str(directory), None, 0)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size)
        if not short_path(str(directory), buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())
        alias = Path(buffer.value)
        if alias == directory.resolve():
            self.skipTest('This volume does not provide a distinct short-path alias.')
        self.assertEqual(alias.resolve(), directory.resolve())
        validate_distribution([alias / 'engine.py'], alias)

    def test_source_outside_the_distribution_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            source = Path(other) / 'engine.py'
            source.write_text('pass\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                validate_distribution([source], self.root)
