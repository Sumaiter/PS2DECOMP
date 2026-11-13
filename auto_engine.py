"""Game-neutral entry point used by editor and persistent search workers."""
from ee_common import complete_cfg, similarity
from project_config import FUNCTION
from pathlib import Path
from types import MethodType
import hashlib
import importlib
import json
import project_config


def adapter(repository):
    settings = project_config.load(repository)
    name = settings.get('adapter', 'generic')
    return settings, None if name == 'generic' else importlib.import_module(name)


class AutoEngine:
    @staticmethod
    def version_availability(repository):
        settings, module = adapter(repository)
        if module is None:
            from project_engine import Engine
            return Engine.version_availability(repository)
        return module.version_availability(repository)

    def __new__(cls, repository, version=None):
        settings, module = adapter(repository)
        if module is None:
            from project_engine import Engine
            return Engine(repository, version)
        engine = module.create_engine(repository, settings, version)
        if engine.version != settings['version']:
            raise ValueError('Adapter version and ps2decomp.json version differ.')
        engine.config = settings
        # Both old adapters now honor every foreign claim, including newer
        # PS2DECOMP owners. No reservation is reclaimed by age or dead PID.
        from project_engine import Engine
        engine.assert_access = MethodType(Engine.assert_access, engine)
        engine.manual_lease = MethodType(Engine.manual_lease, engine)
        original_pin = engine.pin_inputs
        def pins(self, names):
            data = original_pin(names)
            app = Path(__file__).resolve().parent
            paths = {self.root / 'ps2decomp.json'} | set(app.glob('*.py'))
            paths |= set((app / settings['adapter']).rglob('*.py'))
            paths |= set((app / 'vendor').rglob('*.py'))
            for p in paths:
                data[str(p.resolve())] = dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                    size=p.stat().st_size, mtime=p.stat().st_mtime_ns)
            return data
        engine.pin_inputs = MethodType(pins, engine)
        return engine
