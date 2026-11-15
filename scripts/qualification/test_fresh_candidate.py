import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fresh_candidate_identity as identity


class FreshCandidateTests(unittest.TestCase):
    def runtime(self):
        c = identity.contract()
        return dict(optimizerSha256=c['optimizerSha256'], profileContractSha256=c['profileContractSha256'],
            manifestEnforced=True, pathConsistencyPassed=True, cacheInitiallyEmpty=True, workers=1,
            riskQueryPolicySha256=c['files']['apps/backend-compat/src/backend/risk_query.py'],
            riskQueryPolicy=dict(requestedWorkers=4, effectiveWorkers=3, cpuLimit=3, runtimeThreadLimit=3,
                                 exact=True, k=1, p=2, eps=0))

    def test_accept_actual_cpu_cap_without_claiming_four_workers(self):
        value = self.runtime()
        self.assertIs(identity.verify_runtime(value), value)
        self.assertEqual(value['riskQueryPolicy']['effectiveWorkers'], 3)

    def test_reject_wrong_runtime_identity(self):
        for field in ['optimizerSha256', 'profileContractSha256', 'riskQueryPolicySha256']:
            with self.subTest(field=field):
                value = self.runtime()
                value[field] = '0'*64
                with self.assertRaises(ValueError):
                    identity.verify_runtime(value)

    def test_reject_unbound_or_nonempty_runtime(self):
        for field in ['manifestEnforced', 'pathConsistencyPassed', 'cacheInitiallyEmpty']:
            with self.subTest(field=field):
                value = self.runtime()
                value[field] = False
                with self.assertRaises(ValueError):
                    identity.verify_runtime(value)

    def test_reject_approximation_and_oversubscription(self):
        for field, bad in [('eps', .1), ('exact', False), ('p', 1), ('k', 2),
                           ('effectiveWorkers', 4), ('requestedWorkers', 8),
                           ('cpuLimit', 0), ('runtimeThreadLimit', True)]:
            with self.subTest(field=field):
                value = self.runtime()
                value['riskQueryPolicy'][field] = bad
                with self.assertRaises(ValueError):
                    identity.verify_runtime(value)

    def test_distinct_empty_runtime_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preparation = root/'preparation'
            preparation.mkdir()
            identity.validate_fresh_roots(preparation, root/'fresh')
            for target in [preparation, preparation/'child', root]:
                with self.assertRaises(ValueError):
                    identity.validate_fresh_roots(preparation, target)
            (root/'fresh').mkdir()
            with self.assertRaises(ValueError):
                identity.validate_fresh_roots(preparation, root/'fresh')

    def test_mesh_donor_not_rewritten(self):
        p = Path(__file__).with_name('warm_response_contract.json')
        original = json.loads(p.read_text())
        before = copy.deepcopy(original)
        derived = identity.derived_mesh_contract(original)
        self.assertEqual(before, original)
        self.assertEqual(derived['assets'], original['assets'])
        self.assertNotEqual(derived['optimizerSha256'], original['optimizerSha256'])
        self.assertEqual(derived['profileContractSha256'], original['profileContractSha256'])

    def test_reject_wrong_mesh_donor(self):
        original = json.loads(Path(__file__).with_name('warm_response_contract.json').read_text())
        for field in ['sourceCommit', 'optimizerSha256', 'profileContractSha256']:
            bad = dict(original, **{field: 'invalid'})
            with self.assertRaises(ValueError):
                identity.derived_mesh_contract(bad)

    def test_source_hash_and_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'file.txt').write_bytes(b'test')
            c = dict(sourceCommit='candidate', files={'file.txt': identity.sha(root/'file.txt')})
            with patch.object(identity, 'contract', return_value=c):
                with patch.object(identity.subprocess, 'check_output', side_effect=['candidate', '', 'file.txt']):
                    self.assertEqual(identity.verify_source(root)['filesVerified'], 1)
                (root/'file.txt').write_bytes(b'changed')
                with patch.object(identity.subprocess, 'check_output', side_effect=['candidate', '', 'file.txt']):
                    with self.assertRaises(ValueError):
                        identity.verify_source(root)
                with patch.object(identity.subprocess, 'check_output', side_effect=['candidate', '', 'different.txt']):
                    with self.assertRaises(ValueError):
                        identity.verify_source(root)

    def test_source_dirty_or_wrong_commit(self):
        for outputs in [['wrong'], [identity.contract()['sourceCommit'], ' M file']]:
            with patch.object(identity.subprocess, 'check_output', side_effect=outputs):
                with self.assertRaises(ValueError):
                    identity.verify_source(Path('.'))


if __name__ == '__main__':
    unittest.main()
