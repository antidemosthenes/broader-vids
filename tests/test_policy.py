import unittest

from deepvids.policy import Policy


class PolicyTests(unittest.TestCase):
    def test_duration_cap_plateau_and_unknowns(self):
        policy = Policy()
        for value in (None, True, 0, -1, float("nan"), float("inf"), 5401):
            self.assertFalse(policy.duration_eligible(value))
            self.assertIsNone(policy.duration_prior(value))
        self.assertTrue(policy.duration_eligible(5400))
        self.assertEqual(policy.duration_prior(3600), 5)
        self.assertEqual(policy.duration_prior(5400), 5)
        self.assertEqual(policy.duration_prior(1800), 2.5)
