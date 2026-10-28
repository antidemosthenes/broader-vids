import json
import re

from broader_vids.http import AccessPaused


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def embedded_json(body, marker):
    match = re.search(marker, body)
    if match is None:
        raise AccessPaused(
            "The expected public metadata object is missing; the response needs review."
        )
    try:
        value, _ = json.JSONDecoder().raw_decode(body[match.end() :].lstrip())
    except json.JSONDecodeError:
        raise AccessPaused(
            "The embedded metadata object could not be decoded."
        ) from None
    if not isinstance(value, dict):
        raise AccessPaused("The embedded metadata object has an unexpected schema.")
    return value
