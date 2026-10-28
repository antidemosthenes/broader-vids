import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re


def load_bank(path=None):
    raw = (
        Path(path).read_bytes()
        if path
        else files("broader_vids").joinpath("data/queries.json").read_bytes()
    )
    if len(raw) > 1024 * 1024:
        raise ValueError("Query banks must be at most 1 MiB.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("A query bank must be a JSON object.")
    families = value.get("families", [])
    if (
        type(value.get("version")) is not int
        or not isinstance(families, list)
        or not 1 <= len(families) <= 200
    ):
        raise ValueError("A query bank requires a numeric version and 1–200 families.")
    seen = set()
    for family in families:
        if not isinstance(family, dict):
            raise ValueError("Each query family must be a JSON object.")
        identifier = family.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", identifier) or identifier in seen:
            raise ValueError("Query family IDs must be distinct lowercase identifiers.")
        seen.add(identifier)
        if family.get("intent") not in {
            "deep",
            "intersection",
            "horizon",
        } or not family.get("purpose"):
            raise ValueError(
                "Each query family needs a purpose and a supported discovery intent."
            )
        domains = family.get("domains")
        if (
            not isinstance(domains, list)
            or not domains
            or not all(isinstance(s, str) and s for s in domains)
        ):
            raise ValueError("Each query family requires explicit domain tags.")
        if family["intent"] == "intersection" and len(set(domains)) < 2:
            raise ValueError(
                "Intersection queries require at least two distinct domains."
            )
        for platform in ("youtube", "bilibili"):
            terms = family.get(platform)
            if (
                not isinstance(terms, list)
                or not 1 <= len(terms) <= 20
                or not all(isinstance(s, str) and 1 <= len(s) <= 200 for s in terms)
            ):
                raise ValueError(
                    "Each family needs 1–20 bounded query variants per platform."
                )
        groups = family.get("anchors", [])
        if not isinstance(groups, list) or not all(
            isinstance(g, list) and g and all(isinstance(s, str) and s for s in g)
            for g in groups
        ):
            raise ValueError("Query anchors must be nonempty groups of aliases.")
    return {**value, "sha256": hashlib.sha256(raw).hexdigest()}


def query_plan(bank, platform, round_number=0):
    if (
        platform not in {"youtube", "bilibili"}
        or type(round_number) is not int
        or round_number < 0
    ):
        raise ValueError(
            "Query plans require a supported platform and a nonnegative round."
        )
    lanes = {
        intent: [f for f in bank["families"] if f["intent"] == intent]
        for intent in ("intersection", "deep", "horizon")
    }
    for intent, families in lanes.items():
        if families:
            offset = round_number % len(families)
            lanes[intent] = families[offset:] + families[:offset]
    result = []
    while any(lanes.values()):
        for lane in lanes.values():
            if lane:
                family = lane.pop(0)
                result.append(
                    {
                        "family_id": family["id"],
                        "intent": family["intent"],
                        "domains": family["domains"],
                        "purpose": family["purpose"],
                        "anchors": family.get("anchors", []),
                        "query": family[platform][round_number % len(family[platform])],
                    }
                )
    return result


def anchor_hits(item, task):
    text = (item.get("title", "") + " " + item.get("description", "")).casefold()
    return sum(
        any(alias.casefold() in text for alias in group)
        for group in task.get("anchors", [])
    )
