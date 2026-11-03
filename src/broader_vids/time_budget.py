import math


def effective_limit(settings):
    value = settings.get("max_seconds")
    return math.inf if value is None else value


def validate_optional_limit(value):
    if value is not None and (
        type(value) not in {int, float} or not math.isfinite(value) or value <= 0
    ):
        raise ValueError(
            "An optional time allowance must be a positive finite number of seconds."
        )


def report_limit(settings):
    value = effective_limit(settings)
    return None if math.isinf(value) else value
