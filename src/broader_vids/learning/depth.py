import math
import copy

from broader_vids.learning.inference import sigmoid, log1pexp, check_work


LEVELS = ("introductory", "intermediate", "advanced")
RESPONSES = ("deeper", "suitable", "lighter")
WIDTH = 0.75
PRIOR_MEAN, PRIOR_SD = 1.0, 1.5


def probabilities(preference, level):
    z = preference - LEVELS.index(level)
    deeper, lighter = sigmoid(z - WIDTH), sigmoid(-z - WIDTH)
    return [deeper, max(0, 1 - deeper - lighter), lighter]


def log_density(value, rows):
    result = -0.5 * ((value - PRIOR_MEAN) / PRIOR_SD) ** 2
    for row in rows:
        level, response = row[:2]
        count = row[2] if len(row) == 3 else 1
        z = value - LEVELS.index(level)
        if response == "deeper":
            result -= count * log1pexp(WIDTH - z)
        elif response == "lighter":
            result -= count * log1pexp(WIDTH + z)
        else:
            result += count * (math.log(math.expm1(2 * WIDTH)) + z - WIDTH)
            result -= count * (log1pexp(z + WIDTH) + log1pexp(z - WIDTH))
    return result


def integrate(rows):
    mode = PRIOR_MEAN
    for _ in range(80):
        check_work()
        gradient = -(mode - PRIOR_MEAN) / PRIOR_SD**2
        precision = 1 / PRIOR_SD**2
        for row in rows:
            level, response = row[:2]
            count = row[2] if len(row) == 3 else 1
            z = mode - LEVELS.index(level)
            if response == "suitable":
                a, b = sigmoid(z + WIDTH), sigmoid(z - WIDTH)
                gradient += count * (1 - a - b)
                precision += count * (a * (1 - a) + b * (1 - b))
            else:
                sign = 1 if response == "deeper" else -1
                p = sigmoid(sign * z - WIDTH)
                gradient += count * sign * (1 - p)
                precision += count * p * (1 - p)
        step = gradient / precision
        if abs(step) < 1e-10:
            break
        scale, previous = 1.0, log_density(mode, rows)
        while log_density(mode + scale * step, rows) < previous:
            scale *= 0.5
            if scale < 1e-10:
                break
        mode += scale * step
    else:
        raise ValueError("Depth preference fitting did not converge.")
    peak, radius = log_density(mode, rows), 4 / math.sqrt(precision)
    left, right = mode - radius, mode + radius
    while log_density(left, rows) > peak - 35:
        left = mode - 1.5 * (mode - left)
    while log_density(right, rows) > peak - 35:
        right = mode + 1.5 * (right - mode)
    points = [left + (i + 0.5) * (right - left) / 501 for i in range(501)]
    weights = [math.exp(log_density(point, rows) - peak) for point in points]
    total = sum(weights)
    mean = sum(p * w for p, w in zip(points, weights)) / total
    predictions = {
        level: [
            sum(probabilities(p, level)[i] * w for p, w in zip(points, weights)) / total
            for i in range(3)
        ]
        for level in LEVELS
    }
    return {
        "mean": mean,
        "sd": math.sqrt(
            sum(w * (p - mean) ** 2 for p, w in zip(points, weights)) / total
        ),
        "observations": sum(row[2] if len(row) == 3 else 1 for row in rows),
        "predictions": predictions,
        "approximation": "adaptive_one_dimensional_quadrature",
    }


PRIOR = integrate([])


def fit_depth(rows, baseline=None):
    grouped = {}
    for index, row in enumerate(rows):
        if index % 64 == 0:
            check_work()
        if (
            row["dimension"] == "depth"
            and row["split"] == "train"
            and row.get("level") in LEVELS
        ):
            counts = grouped.setdefault(row["target"], [[0] * 3 for _ in LEVELS])
            counts[LEVELS.index(row["level"])][RESPONSES.index(row["value"])] += 1
    result = {}
    for target in grouped.keys() | (baseline or {}).keys():
        check_work()
        previous = (baseline or {}).get(target)
        added = grouped.get(target)
        if previous and added is None:
            result[target] = copy.deepcopy(previous)
            continue
        counts = (
            copy.deepcopy(previous["counts"]) if previous else [[0] * 3 for _ in LEVELS]
        )
        if added is not None:
            for i in range(len(LEVELS)):
                for j in range(len(RESPONSES)):
                    counts[i][j] += added[i][j]
        samples = [
            (level, response, counts[i][j])
            for i, level in enumerate(LEVELS)
            for j, response in enumerate(RESPONSES)
            if counts[i][j]
        ]
        result[target] = {**integrate(samples), "counts": counts}
    return result


def suitability(models, topics, level):
    matches = [models[topic] for topic in dict.fromkeys(topics) if topic in models]
    if not matches or level not in LEVELS:
        return None
    p = sum(model["predictions"][level][1] for model in matches) / len(matches)
    p = min(1 - 1e-12, max(1e-12, p))
    prior = PRIOR["predictions"][level][1]
    adjustment = math.log(p / (1 - p)) - math.log(prior / (1 - prior))
    return {
        "suitability": p,
        "prior_suitability": prior,
        "level": level,
        "log_odds_adjustment": adjustment,
    }
