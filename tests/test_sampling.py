import random
import unittest

from deepvids.diagnostics import diagnose
from deepvids.models import Policy
from deepvids.providers import prefix_matches
from deepvids.sampling import Kernel, acceptance, simulation_report, transition_matrix


class SamplingTests(unittest.TestCase):
    def test_multipart_stationarity_and_jump_chain_bias(self):
        result = simulation_report()
        self.assertTrue(result["passed"])
        self.assertGreater(result["experiments"][1]["accepted_moves_only_bias"], 0.001)

    def test_transition_includes_boundary_and_missing_id_rejections(self):
        states, matrix = transition_matrix({1: 1, 2: 4, 12: 1}, 12)
        self.assertEqual(states, [1, 2, 12])
        self.assertTrue(all(0 < row[i] < 1 for i, row in enumerate(matrix)))
        self.assertAlmostEqual(matrix[0][1] / matrix[1][0], 4)
        self.assertAlmostEqual(sum(matrix[0]), 1)

    def test_proposal_is_symmetric(self):
        kernel = Kernel(12)
        for source in range(1, 13):
            for target in range(1, 13):
                self.assertEqual(kernel.probability(source, target), kernel.probability(target, source))
        self.assertEqual(kernel.probability(1, 0), 0)
        self.assertEqual(acceptance(4, 1), 0.25)

    def test_duration_cap_plateau_and_unknowns(self):
        policy = Policy()
        for value in (None, True, 0, -1, float("nan"), float("inf"), 5401):
            self.assertFalse(policy.duration_eligible(value))
            self.assertIsNone(policy.duration_prior(value))
        self.assertTrue(policy.duration_eligible(5400))
        self.assertEqual(policy.duration_prior(3600), 5)
        self.assertEqual(policy.duration_prior(5400), 5)
        self.assertEqual(policy.duration_prior(1800), 2.5)

    def test_prefix_support_is_not_mislabeled_global(self):
        self.assertTrue(prefix_matches("AbcDE-aaaaA", "abcde"))
        self.assertFalse(prefix_matches("AbcDE_aaaaA", "abcde"))
        self.assertFalse(prefix_matches("AbcDE-aaaaB", "abcde"))
        self.assertFalse(prefix_matches("xbcDE-aaaaA", "abcde"))

    def test_diagnostics_detect_seed_trapping(self):
        rng = random.Random(37)
        trapped = [[center + rng.gauss(0, 0.1) for _ in range(300)] for center in (1, 10, 100, 1000)]
        result = diagnose(trapped)
        self.assertGreater(result["rhat"], 1.1)
        self.assertLess(result["bulk_ess"], 100)

    def test_diagnostics_on_independent_samples_and_constants(self):
        rng = random.Random(7)
        independent = [[rng.gauss(0, 1) for _ in range(300)] for _ in range(4)]
        result = diagnose(independent)
        self.assertLess(result["rhat"], 1.05)
        self.assertGreater(result["bulk_ess"], 400)
        self.assertIsNone(diagnose([[1] * 100 for _ in range(4)])["rhat"])


if __name__ == "__main__":
    unittest.main()

