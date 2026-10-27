import math
from statistics import NormalDist
from contextvars import ContextVar
from contextlib import contextmanager

_WORK_CHECK = ContextVar("deepvids_learning_work_check", default=None)


def check_work():
    callback = _WORK_CHECK.get()
    if callback is not None:
        callback()


@contextmanager
def work_scope(callback):
    token = _WORK_CHECK.set(callback)
    try:
        check_work()
        yield
    finally:
        _WORK_CHECK.reset(token)


def sigmoid(value):
    return (
        1 / (1 + math.exp(-value))
        if value >= 0
        else math.exp(value) / (1 + math.exp(value))
    )


def log1pexp(value):
    return max(value, 0) + math.log1p(math.exp(-abs(value)))


def cholesky(matrix):
    size = len(matrix)
    result = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            value = matrix[i][j] - sum(result[i][k] * result[j][k] for k in range(j))
            if i == j:
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(
                        "The posterior precision is not positive definite."
                    )
                result[i][j] = math.sqrt(value)
            else:
                result[i][j] = value / result[j][j]
    return result


def solve(factor, vector):
    size, forward = len(vector), []
    for i in range(size):
        forward.append(
            (vector[i] - sum(factor[i][j] * forward[j] for j in range(i)))
            / factor[i][i]
        )
    result = [0.0] * size
    for i in range(size - 1, -1, -1):
        result[i] = (
            forward[i] - sum(factor[j][i] * result[j] for j in range(i + 1, size))
        ) / factor[i][i]
    return result


def validate_problem(specs, rows):
    names = {s["name"] for s in specs}
    if (
        any(
            not math.isfinite(s["mean"])
            or not math.isfinite(s["sd"])
            or s["sd"] <= 0
            or "lower" in s
            or "upper" in s
            for s in specs
        )
        or len(names) != len(specs)
        or any(
            row["y"] not in (0, 1)
            or row.get("weight", 1) != 1
            or not math.isfinite(row.get("offset", 0))
            or not set(row["x"]).issubset(names)
            or any(not math.isfinite(value) for value in row["x"].values())
            for row in rows
        )
    ):
        raise ValueError(
            "Bayesian updates require declared finite features, distinct parameters, full-support proper priors and one unweighted Bernoulli label per observation."
        )


def fit_logistic(specs, rows):
    check_work()
    if len(specs) > 96:
        return fit_sparse(specs, rows)
    validate_problem(specs, rows)
    names = [s["name"] for s in specs]
    indices = {name: i for i, name in enumerate(names)}
    data = [
        (
            {indices[k]: float(v) for k, v in row["x"].items() if k in indices and v},
            row["y"],
            row.get("offset", 0),
            row.get("weight", 1),
        )
        for row in rows
    ]
    prior = [s["mean"] for s in specs]
    precision = [1 / s["sd"] ** 2 for s in specs]
    beta = list(prior)

    def objective(values):
        return -0.5 * sum(
            p * (v - m) ** 2 for p, v, m in zip(precision, values, prior)
        ) + sum(
            w
            * (
                y * (offset + sum(values[k] * v for k, v in x.items()))
                - log1pexp(offset + sum(values[k] * v for k, v in x.items()))
            )
            for x, y, offset, w in data
        )

    def derivatives(values):
        gradient = [-p * (v - m) for p, v, m in zip(precision, values, prior)]
        hessian = [
            [precision[i] if i == j else 0.0 for j in range(len(beta))]
            for i in range(len(beta))
        ]
        for x, y, offset, weight in data:
            check_work()
            probability = sigmoid(offset + sum(values[k] * v for k, v in x.items()))
            for i, vi in x.items():
                gradient[i] += weight * (y - probability) * vi
                for j, vj in x.items():
                    hessian[i][j] += weight * probability * (1 - probability) * vi * vj
        return gradient, hessian

    converged = False
    for iteration in range(80):
        check_work()
        gradient, hessian = derivatives(beta)
        if max((abs(value) for value in gradient), default=0) < 1e-6:
            converged = True
            break
        step = solve(cholesky(hessian), gradient)
        if sum(g * d for g, d in zip(gradient, step)) <= 0:
            step = [g / hessian[i][i] for i, g in enumerate(gradient)]
        scale, previous = 1.0, objective(beta)
        while scale > 1e-10:
            check_work()
            proposal = [v + scale * d for v, d in zip(beta, step)]
            if objective(proposal) >= previous - 1e-12:
                break
            scale *= 0.5
        change = max((abs(v - old) for v, old in zip(proposal, beta)), default=0)
        beta = proposal
        if change < 1e-7:
            projected = [abs(g / hessian[i][i]) for i, g in enumerate(gradient)]
            converged = max(projected, default=0) < 1e-5 / math.sqrt(1 + len(rows))
            if converged:
                break
    if not converged:
        raise ValueError(
            "Bayesian preference fitting did not converge; prior scoring remains available."
        )
    _, hessian = derivatives(beta)
    factor = cholesky(hessian)
    covariance = [[0.0] * len(beta) for _ in beta]
    for j in range(len(beta)):
        column = solve(factor, [float(i == j) for i in range(len(beta))])
        for i, value in enumerate(column):
            covariance[i][j] = value
    basis = []
    for x, _, _, _ in data:
        check_work()
        if len(basis) == len(beta):
            break
        vector = [x.get(i, 0) for i in range(len(beta))]
        for _ in range(2):
            for previous in basis:
                scale = sum(a * b for a, b in zip(vector, previous))
                vector = [a - scale * b for a, b in zip(vector, previous)]
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 1e-8:
            basis.append([v / norm for v in vector])
    return {
        "names": names,
        "mean": beta,
        "covariance": covariance,
        "specs": specs,
        "observations": len(rows),
        "iterations": iteration + 1,
        "converged": True,
        "approximation": "MAP_and_Laplace",
        "information_rank": len(basis),
        "parameter_count": len(beta),
        "identifiable_on_observed_design": len(basis) == len(beta),
    }


NORMAL_POINTS = tuple(NormalDist().inv_cdf((i + 0.5) / 31) for i in range(31))


def predict(model, features, offset=0, extra_variance=0):
    check_work()
    active = [
        (i, features[name])
        for i, name in enumerate(model["names"])
        if features.get(name, 0)
    ]
    mean = offset + sum(v * model["mean"][i] for i, v in active)
    if model["covariance"] is None:
        x = [features.get(name, 0) for name in model["names"]]
        solved = solve_precision(model, x)
        parameter_variance = sum(value * solved[i] for i, value in active)
    else:
        parameter_variance = sum(
            vi * vj * model["covariance"][i][j] for i, vi in active for j, vj in active
        )
    variance = max(
        0,
        extra_variance + parameter_variance,
    )
    sd = math.sqrt(variance)
    return {
        "probability": sum(sigmoid(mean + sd * point) for point in NORMAL_POINTS)
        / len(NORMAL_POINTS),
        "interval90": [sigmoid(mean - 1.645 * sd), sigmoid(mean + 1.645 * sd)],
        "logit_mean": mean,
        "logit_variance": variance,
    }


def precision_product(prior, rows, vector):
    result = [p * v for p, v in zip(prior, vector)]
    for x, weight in rows:
        check_work()
        scale = weight * sum(vector[i] * v for i, v in x)
        for i, value in x:
            result[i] += scale * value
    return result


def cg(prior, rows, vector, diagonal=None):
    check_work()
    result = [0.0] * len(vector)
    if diagonal is None:
        diagonal = precision_diagonal(prior, rows)
    residual = list(vector)
    z = [r / d for r, d in zip(residual, diagonal)]
    direction, product = list(z), sum(a * b for a, b in zip(residual, z))
    target = max(1e-20, sum(v * v for v in vector) * 1e-16)
    if sum(v * v for v in residual) <= target:
        return result
    for _ in range(max(100, 2 * len(vector))):
        check_work()
        hd = precision_product(prior, rows, direction)
        denominator = sum(a * b for a, b in zip(direction, hd))
        if not math.isfinite(denominator) or denominator <= 0:
            raise ValueError("Posterior precision solve lost positive curvature.")
        scale = product / denominator
        result = [v + scale * d for v, d in zip(result, direction)]
        residual = [v - scale * d for v, d in zip(residual, hd)]
        if sum(v * v for v in residual) <= target:
            return result
        z = [r / d for r, d in zip(residual, diagonal)]
        updated = sum(a * b for a, b in zip(residual, z))
        direction = [a + (updated / product) * b for a, b in zip(z, direction)]
        product = updated
    raise ValueError(
        "Sparse posterior precision solve did not converge; no feedback was discarded."
    )


def solve_precision(model, vector):
    return cg(
        model["prior_precision"],
        model["likelihood_precision"],
        vector,
        model.get("precision_diagonal"),
    )


def precision_diagonal(prior, rows):
    diagonal = list(prior)
    for x, weight in rows:
        check_work()
        for i, value in x:
            diagonal[i] += weight * value * value
    return diagonal


def fit_sparse(specs, rows):
    check_work()
    validate_problem(specs, rows)
    names = [s["name"] for s in specs]
    indices = {name: i for i, name in enumerate(names)}
    data = [
        (
            [(indices[k], v) for k, v in row["x"].items() if k in indices and v],
            row["y"],
            row.get("offset", 0),
        )
        for row in rows
    ]
    prior = [1 / s["sd"] ** 2 for s in specs]
    means = [s["mean"] for s in specs]
    beta = list(means)

    def objective(values):
        return -0.5 * sum(
            p * (v - m) ** 2 for p, v, m in zip(prior, values, means)
        ) + sum(
            y * (offset + sum(values[i] * v for i, v in x))
            - log1pexp(offset + sum(values[i] * v for i, v in x))
            for x, y, offset in data
        )

    for iteration in range(80):
        check_work()
        gradient = [-p * (v - m) for p, v, m in zip(prior, beta, means)]
        precision = []
        for x, y, offset in data:
            check_work()
            probability = sigmoid(offset + sum(beta[i] * v for i, v in x))
            precision.append((x, probability * (1 - probability)))
            for i, value in x:
                gradient[i] += (y - probability) * value
        if max((abs(value) for value in gradient), default=0) < 1e-6:
            break
        step = cg(prior, precision, gradient)
        previous, scale = objective(beta), 1.0
        while scale > 1e-10:
            check_work()
            proposed = [v + scale * d for v, d in zip(beta, step)]
            if objective(proposed) >= previous - 1e-12:
                break
            scale *= 0.5
        beta = proposed
    else:
        raise ValueError("Sparse preference fitting did not converge.")
    precision = []
    for x, _, offset in data:
        probability = sigmoid(offset + sum(beta[i] * v for i, v in x))
        precision.append((x, probability * (1 - probability)))
    return {
        "names": names,
        "mean": beta,
        "specs": specs,
        "covariance": None,
        "prior_precision": prior,
        "likelihood_precision": precision,
        "precision_diagonal": precision_diagonal(prior, precision),
        "observations": len(rows),
        "iterations": iteration + 1,
        "converged": True,
        "approximation": "matrix_free_MAP_and_Laplace",
        "parameter_count": len(names),
        "information_rank": None,
        "identifiable_on_observed_design": False if len(rows) < len(names) else None,
    }
