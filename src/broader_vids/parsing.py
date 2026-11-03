import json
import re

from broader_vids.http import AccessPaused


def walk(value):
    stack = [iter((value,))]
    while stack:
        try:
            value = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(value, dict):
            yield value
            stack.append(iter(value.values()))
        elif isinstance(value, list):
            stack.append(iter(value))


def embedded_json(body, marker):
    match = re.search(marker, body)
    if match is None:
        raise AccessPaused(
            "The expected public metadata object is missing; the response needs review."
        )
    try:
        value, _ = json.JSONDecoder().raw_decode(body[match.end() :].lstrip())
    except (json.JSONDecodeError, RecursionError):
        raise AccessPaused(
            "The embedded metadata object could not be decoded."
        ) from None
    if not isinstance(value, dict):
        raise AccessPaused("The embedded metadata object has an unexpected schema.")
    return value
