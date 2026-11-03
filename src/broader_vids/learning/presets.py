from importlib.resources import files

from broader_vids.learning.transfer import parse_bundle


def starter_preferences():
    bundle = parse_bundle(
        files("broader_vids")
        .joinpath("data/starter-preferences.json")
        .read_text(encoding="utf-8")
    )
    if (
        bundle["watched_video_ids"]
        or bundle["creator_exclusions"] != {"youtube": [], "bilibili": []}
        or bundle["exploration_summary"]
        != {"version": 1, "sequence": 0, "nodes": [], "usage": {}}
    ):
        raise ValueError(
            "The starter preset must contain no watched IDs, creator exclusions, or exploration progress."
        )
    return bundle
