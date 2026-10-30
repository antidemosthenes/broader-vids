import copy

from broader_vids.learning.inference import cholesky, solve, check_work


def invert(matrix):
    factor = cholesky(matrix)
    size = len(matrix)
    columns = [solve(factor, [float(i == j) for i in range(size)]) for j in range(size)]
    return [[columns[j][i] for j in range(size)] for i in range(size)]


def portable_model(model):
    result = {
        key: copy.deepcopy(model[key]) for key in ("names", "mean", "observations")
    }
    if model.get("covariance") is not None:
        result["covariance"] = copy.deepcopy(model["covariance"])
    elif model.get("posterior_precision") is not None:
        result["precision"] = copy.deepcopy(model["posterior_precision"])
    else:
        size = len(model["names"])
        matrix = [
            [model["prior_precision"][i] if i == j else 0.0 for j in range(size)]
            for i in range(size)
        ]
        for row, weight in model["likelihood_precision"]:
            check_work()
            for i, a in row:
                for j, b in row:
                    matrix[i][j] += weight * a * b
        result["precision"] = matrix
    return result


def restored_model(posterior):
    result = {
        key: copy.deepcopy(posterior[key]) for key in ("names", "mean", "observations")
    }
    result.update(
        iterations=0,
        converged=True,
        approximation="imported_Gaussian_posterior",
        parameter_count=len(result["names"]),
        information_rank=None,
        identifiable_on_observed_design=None,
    )
    if "covariance" in posterior:
        result["covariance"] = copy.deepcopy(posterior["covariance"])
    else:
        result.update(
            covariance=None,
            posterior_precision=copy.deepcopy(posterior["precision"]),
            precision_factor=cholesky(posterior["precision"]),
        )
    return result


def aligned_prior(specs, posterior):
    means = [s["mean"] for s in specs]
    size = len(specs)
    precision = [
        [1 / specs[i]["sd"] ** 2 if i == j else 0.0 for j in range(size)]
        for i in range(size)
    ]
    if posterior is None:
        return means, precision
    positions = {s["name"]: i for i, s in enumerate(specs)}
    indices = [positions[name] for name in posterior["names"]]
    matrix = posterior.get("precision")
    if matrix is None:
        matrix = invert(posterior["covariance"])
    for i, destination in enumerate(indices):
        means[destination] = posterior["mean"][i]
        for j, target in enumerate(indices):
            precision[destination][target] = matrix[i][j]
    return means, precision
