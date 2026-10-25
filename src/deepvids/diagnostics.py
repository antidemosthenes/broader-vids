import math
from statistics import NormalDist, mean, median, variance


def rank_normalize(chains):
    length = len(chains[0])
    flat = [v for chain in chains for v in chain]
    order = sorted(range(len(flat)), key=flat.__getitem__)
    ranks = [0.0] * len(flat)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and flat[order[end]] == flat[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2
        for index in order[start:end]:
            ranks[index] = rank
        start = end
    normal = NormalDist()
    transformed = [normal.inv_cdf((r - 0.375) / (len(flat) + 0.25)) for r in ranks]
    return [transformed[i:i + length] for i in range(0, len(flat), length)]


def rhat_ess(chains):
    n = len(chains[0])
    m = len(chains)
    means = [mean(c) for c in chains]
    within = mean(variance(c) for c in chains)
    between = n * variance(means)
    var_plus = (n - 1) / n * within + between / n
    if within <= 0 or var_plus <= 0:
        return None, None
    rhat = math.sqrt(var_plus / within)
    centered = [[v - center for v in c] for c, center in zip(chains, means)]
    previous = float("inf")
    pair_sum = 0.0
    for t in range(0, n - 1, 2):
        pair = 0.0
        for lag in (t, t + 1):
            if lag == 0:
                rho = 1.0
            else:
                acov = mean(sum(c[i] * c[i + lag] for i in range(n - lag)) / n for c in centered)
                rho = 1 - (within - acov) / var_plus
            pair += rho
        if pair <= 0:
            break
        pair = min(pair, previous)
        pair_sum += pair
        previous = pair
    tau = max(1.0, -1 + 2 * pair_sum)
    return rhat, min(m * n, m * n / tau)


def diagnose(chains):
    if len(chains) < 4 or min((len(c) for c in chains), default=0) < 16:
        return {"rhat": None, "bulk_ess": None, "reason": "Need four chains with at least 16 retained steps each."}
    length = min(len(c) for c in chains)
    half = length // 2
    split = [part for chain in chains for part in (chain[:half], chain[half:2 * half])]
    normalized = rank_normalize(split)
    center = median(v for c in split for v in c)
    folded = rank_normalize([[abs(v - center) for v in c] for c in split])
    rhat, ess = rhat_ess(normalized)
    folded_rhat, _ = rhat_ess(folded)
    if rhat is None or folded_rhat is None:
        return {"rhat": None, "bulk_ess": ess, "reason": "Constant or trapped chains; convergence is not established."}
    return {"rhat": max(rhat, folded_rhat), "bulk_ess": ess,
            "reason": "Rank-normalized split/folded R-hat and initial-positive monotone-pair bulk ESS; necessary diagnostics, not coverage proof."}
