"""Retry sessions retain the configured game identity through the launch gate."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auto_jobs
from tests import test_project_engine as fixtures


class RetryVersionTests(unittest.TestCase):
    def fixture(self, version):
        fixture = fixtures.ProjectTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.settings['version'] = version
        fixture.save()
        return fixture

    def retry(self, version):
        fixture = self.fixture(version)
        manager = auto_jobs.Manager(fixture.root)
        limits = dict(workers=1, maxAttempts=2, seconds=5)
        original = manager.create([fixture.name], limits)
        manager.handoff(original['id'], fixture.name)
        old_path = manager.folder(original['id']) / 'session.json'
        old_bytes = old_path.read_bytes()
        retried = manager.create_retry([fixture.name], limits)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(retried['options'], original['options'])
        return fixture, manager, retried

    def test_retry_persists_exact_configured_version_instead_of_a_pal_default(self):
        for version in ('synthetic-eu', 'synthetic-us'):
            with self.subTest(version=version):
                fixture, manager, retried = self.retry(version)
                data = auto_jobs.read(manager.folder(retried['id']) / 'session.json')
                self.assertEqual(data.get('version'), version)
                self.assertEqual(retried.get('version'), version)
                self.assertEqual(auto_jobs.claim_api(manager.engine).holder(fixture.name), retried['owner'])

    def test_valid_retry_reaches_worker_launch_without_changing_version_checks(self):
        fixture, manager, retried = self.retry('synthetic-us')
        with patch('auto_jobs.subprocess.Popen', return_value=SimpleNamespace(pid=123456789)) as launch, \
                patch('auto_jobs.process_birth', return_value=1), \
                patch('auto_jobs.live_process', return_value=False):
            result = manager.start(retried['id'])
        launch.assert_called_once()
        self.assertEqual(result['launchedPid'], 123456789)
        self.assertEqual(result['version'], 'synthetic-us')

    def test_changed_or_missing_version_is_still_rejected_before_launch(self):
        for version in ('different-version', None):
            with self.subTest(version=version):
                fixture, manager, retried = self.retry('synthetic-eu')
                path = manager.folder(retried['id']) / 'session.json'
                data = auto_jobs.read(path)
                if version is None:
                    data.pop('version', None)
                else:
                    data['version'] = version
                auto_jobs.write(path, data)
                with patch('auto_jobs.subprocess.Popen') as launch:
                    with self.assertRaisesRegex(ValueError, 'Spielversion'):
                        manager.start(retried['id'])
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
