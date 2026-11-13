"""Game data must never enter a tool distribution, even through adapter assets."""
from pathlib import Path
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
