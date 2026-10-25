import math
import random

from .models import Archive
from .providers import MAX_AID


def tuples(value):
    return tuple(tuples(v) for v in value) if isinstance(value, (list, tuple)) else value


def restore_rng(state):
    rng = random.Random()
    rng.setstate(tuples(state))
    return rng


class Kernel:
    def __init__(self, maximum=MAX_AID):
        if maximum < 1:
            raise ValueError("The ID domain must be positive.")
        self.maximum = maximum
        self.max_scale = max(0, math.ceil(math.log2(maximum)))

    def propose(self, current, rng):
        draw = rng.random()
        if draw < 0.10:
            return current
        if draw < 0.15:
            return rng.randint(1, self.maximum)
        scale = rng.randint(0, self.max_scale)
        return current + rng.randint(-(1 << scale), 1 << scale)

    def probability(self, source, target):
        if not 1 <= target <= self.maximum:
            return 0.0
        result = 0.05 / self.maximum
        if source == target:
            result += 0.10
        delta = abs(target - source)
        for scale in range(self.max_scale + 1):
            if delta <= 1 << scale:
                result += 0.85 / (self.max_scale + 1) / ((1 << (scale + 1)) + 1)
        return result


def acceptance(current_parts, proposed_parts):
    if current_parts <= 0 or proposed_parts < 0:
        raise ValueError("Part counts must define positive current target mass.")
    return min(1.0, proposed_parts / current_parts)


def transition_matrix(masses, maximum):
    kernel = Kernel(maximum)
    states = sorted(masses)
    matrix = []
    for source in states:
        row = [0.0] * len(states)
        for index, target in enumerate(states):
            if target != source:
                row[index] = kernel.probability(source, target) * acceptance(masses[source], masses[target])
        row[states.index(source)] = 1.0 - sum(row)
        matrix.append(row)
    return states, matrix


def simulation_report():
    results = []
    for masses in ({1: 1, 2: 1, 9: 1, 12: 1}, {1: 1, 2: 4, 9: 2, 12: 1}):
        states, matrix = transition_matrix(masses, 12)
        total = sum(masses.values())
        stationary = [masses[s] / total for s in states]
        balance = max(abs(stationary[i] * matrix[i][j] - stationary[j] * matrix[j][i])
                      for i in range(len(states)) for j in range(len(states)))
        row_error = max(abs(sum(row) - 1.0) for row in matrix)
        marginal_error = max(abs(stationary[i] / masses[s] - 1 / total) for i, s in enumerate(states))
        jump_mass = [stationary[i] * (1 - matrix[i][i]) for i in range(len(states))]
        jump_total = sum(jump_mass)
        jump_bias = max(abs(jump_mass[i] / jump_total - stationary[i]) for i in range(len(states)))
        results.append({"masses": masses, "detailed_balance_error": balance, "row_sum_error": row_error,
                        "per_part_marginal_error": marginal_error, "accepted_moves_only_bias": jump_bias,
                        "passed": balance < 1e-12 and row_error < 1e-12 and marginal_error < 1e-12 and jump_bias > 1e-3})
    return {"passed": all(r["passed"] for r in results), "experiments": results,
            "interpretation": "Finite-domain algorithm checks; these do not establish live catalog coverage or mixing."}


def step_from_pending(chain, archive):
    current = Archive.from_dict(chain["archive"])
    pending = chain["pending"]
    accepted = archive is not None and pending["uniform"] < acceptance(len(current.parts), len(archive.parts))
    if accepted:
        chain["archive"] = archive.to_dict()
    chain["step"] += 1
    chain["pending"] = None
    return accepted

