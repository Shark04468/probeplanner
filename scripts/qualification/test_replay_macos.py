import copy
import unittest
from replay_macos import CASES, validate_summary


class TestReplayGate(unittest.TestCase):
    def fixture(self):
        return dict(reproductionPassed=True, integrityPostflightPassed=True,
                    completed=3, positive=2, negative=1, fullRequestedCohorts=False,
                    newUntouchedEvidence=False, cases=[dict(case=c, passed=True) for c in CASES])

    def test_accepted_summary(self):
        validate_summary(self.fixture())

    def test_mismatched_roles_rejected(self):
        for field, value in (("positive", 3), ("negative", 0), ("completed", 2),
                             ("integrityPostflightPassed", False), ("newUntouchedEvidence", True),
                             ("reproductionPassed", False), ("fullRequestedCohorts", True)):
            row = self.fixture()
            row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_summary(row)

    def test_duplicate_or_failed_case_rejected(self):
        row = self.fixture()
        row["cases"][0] = copy.deepcopy(row["cases"][1])
        with self.assertRaises(ValueError):
            validate_summary(row)
        row = self.fixture()
        row["cases"][0]["passed"] = False
        with self.assertRaises(ValueError):
            validate_summary(row)


if __name__ == "__main__":
    unittest.main()
