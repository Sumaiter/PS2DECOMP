"""Generated handoffs must not impose exhausted automatic budgets on reviewers."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auto_jobs


class ReviewExportPolicy(unittest.TestCase):
    def test_exhausted_automatic_job_exports_manual_policy_without_mutating_history(self):
        for language in ['en', 'de-DE']:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                name = 'func_00001000'
                directory = root / 'jobs' / name
                directory.mkdir(parents=True)
                session = {'owner': 'synthetic-automatic-owner', 'names': [name],
                           'options': {'maxAttempts': 30, 'seconds': 90}}
                job = {'name': name, 'status': 'handed_off', 'attempts': 30,
                       'deferred': False, 'handedOff': 'synthetic-handoff',
                       'reason': 'automatic time/attempt limit',
                       'timeAccounting': {'spent': 90, 'attempts': 30},
                       'best': {'score': 85, 'technicalMatch': False,
                                'snapshot': 'synthetic/candidate.c', 'sourceHash': '0' * 64}}
                auto_jobs.write(root / 'session.json', session)
                auto_jobs.write(directory / 'job.json', job)
                (directory / 'attempts.jsonl').write_text('{"attempt":30}\n', encoding='utf-8')
                before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                manager = auto_jobs.Manager.__new__(auto_jobs.Manager)
                manager.folder = lambda _: root
                with patch.dict(os.environ, {'PS2DECOMP_LANGUAGE': language}):
                    manager.export('synthetic-session')
                text = (root / 'REVIEW.md').read_text(encoding='utf-8')
                self.assertIn('synthetic-automatic-owner', text)
                self.assertIn('attempts.jsonl', text)
                self.assertIn('synthetic/candidate.c', text)
                if language == 'en':
                    self.assertIn('released candidates scoring at least 80', text)
                    self.assertIn('including candidates with previous automatic attempts', text)
                    self.assertIn('not to manual review or manual decompilation', text)
                    self.assertIn('exhausted automatic budget is not a reason to stop', text)
                    self.assertIn('record manual attempts separately', text)
                    self.assertNotIn('scoring at least 70', text)
                else:
                    self.assertIn('freigegebene Kandidaten ab Suchwert 80', text)
                    self.assertIn('nicht für manuelle Review oder manuelle Decompilierung', text)
                    self.assertIn('kein Abbruchgrund für die manuelle Nacharbeit', text)
                    self.assertIn('manuelle Versuche getrennt protokollieren', text)
                    self.assertNotIn('ab Suchwert 70', text)
                for path, blob in before.items():
                    self.assertEqual(path.read_bytes(), blob, str(path))
                self.assertEqual(json.loads((directory / 'job.json').read_text()), job)


if __name__ == '__main__':
    unittest.main()
