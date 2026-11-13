"""Include adapter-owned tests in the workbench's normal CI command."""
from pathlib import Path


def load_tests(loader, standard_tests, pattern):
    root = Path(__file__).resolve().parents[1]
    return loader.discover(str(root / 'RAC3_PAL/tests'), pattern='test_*.py',
                           top_level_dir=str(root))
