from datetime import datetime, timezone
import math
import re

from broader_vids.http import AccessPaused
from broader_vids.parsing import embedded_json, walk
from broader_vids.storage import utc_now


METRICS = {"youtube": ("likes",), "bilibili": ("likes", "coins", "favorites")}
BASELINES = {"likes": 0.03, "coins": 0.01, "favorites": 0.02}


def integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        return int(value)
    return None


def fresh(snapshot, max_hours=24):
    try:
        return (
            0
            <= (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(snapshot["observed_at"])
            ).total_seconds()
            < max_hours * 3600
        )
    except (ValueError, KeyError, TypeError):
        return False


def metadata_fresh(snapshot, platform):
    return fresh(snapshot) and (
        platform != "bilibili"
        or (
            snapshot.get("metadata_version") == 2
            and snapshot.get("provenance_status") == "complete"
        )
    )


def parse_youtube(body, identifier):
    player = embedded_json(body, r"(?:var\s+)?ytInitialPlayerResponse\s*=\s*")
    details = player.get("videoDetails", {})
    if not isinstance(details, dict) or details.get("videoId") != identifier:
        raise AccessPaused("YouTube metadata identity was not verified.")
    try:
        initial = embedded_json(body, r"(?:var\s+)?ytInitialData\s*=\s*")
    except AccessPaused:
        initial = {}
    likes = None
    for node in walk(initial.get("contents", {})):
        for key in ("toggleButtonRenderer", "likeButtonViewModel"):
            button = node.get(key)
            if not isinstance(button, dict):
                continue
            if (
                key == "toggleButtonRenderer"
                and button.get("defaultIcon", {}).get("iconType") != "LIKE"
            ):
                continue
            for child in walk(button):
                labels = [
                    child.get("accessibility", {}).get("label"),
                    child.get("accessibilityData", {}).get("label"),
                    child.get("accessibilityText"),
                ]
                for label in labels:
                    match = re.search(
                        r"(?i)([\d,]+)\s+likes?\b|like this video along with\s+([\d,]+)\s+other",
                        label or "",
                    )
                    if match:
                        likes = integer((match[1] or match[2]).replace(",", ""))
    micro = player.get("microformat", {}).get("playerMicroformatRenderer", {})
    return {
        "creator_id": details.get("channelId"),
        "creator_name": details.get("author", ""),
        "views": integer(details.get("viewCount")),
        "likes": likes,
        "coins": None,
        "favorites": None,
        "duration_seconds": integer(details.get("lengthSeconds")),
        "published_at": micro.get("publishDate"),
        "tags": details.get("keywords", [])
        if isinstance(details.get("keywords"), list)
        and all(isinstance(tag, str) for tag in details["keywords"])
        else [],
        "tags_status": "observed"
        if isinstance(details.get("keywords"), list)
        else "unknown",
        "scope": "video",
        "identity_verified": True,
    }


def parse_bilibili(value, identifier):
    data = value.get("data")
    tags = data.get("Tags") if isinstance(data, dict) else None
    if isinstance(data, dict) and "View" in data:
        data = data["View"]
    if (
        value.get("code") != 0
        or not isinstance(data, dict)
        or data.get("bvid") != identifier
    ):
        raise AccessPaused(
            f"Bilibili count metadata unavailable (code {value.get('code')}); access was not treated as an empty result."
        )
    stat = data.get("stat") or {}
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    pages = data.get("pages")
    if (
        not isinstance(stat, dict)
        or not isinstance(pages, list)
        or not all(isinstance(p, dict) for p in pages)
    ):
        raise AccessPaused("Bilibili count metadata has an unrecognized schema.")
    return {
        "creator_id": str(owner.get("mid") or "") or None,
        "creator_name": owner.get("name", ""),
        "views": integer(stat.get("view")),
        "likes": integer(stat.get("like")),
        "coins": integer(stat.get("coin")),
        "favorites": integer(stat.get("favorite")),
        "scope": "archive",
        "identity_verified": True,
        "duration_seconds": None,
        "published_at": data.get("pubdate"),
        "metadata_version": 2,
        "title": data.get("title", ""),
        "description": data.get("desc", ""),
        "declared_copyright": data.get("copyright"),
        "declared_copyright_status": "recognized"
        if type(data.get("copyright")) is int and data["copyright"] in (1, 2)
        else "unrecognized"
        if type(data.get("copyright")) is int
        else "missing",
        "tags": [
            t["tag_name"]
            for t in tags
            if isinstance(t, dict) and isinstance(t.get("tag_name"), str)
        ]
        if isinstance(tags, list)
        else [],
        "tags_status": "observed" if isinstance(tags, list) else "unknown",
        "provenance_status": "complete"
        if (
            isinstance(data.get("title"), str)
            and data["title"].strip()
            and isinstance(data.get("desc"), str)
            and type(data.get("copyright")) is int
            and isinstance(tags, list)
            and all(
                isinstance(t, dict) and isinstance(t.get("tag_name"), str) for t in tags
            )
        )
        else "incomplete",
        "pages": [
            {"page": p.get("page"), "cid": p.get("cid"), "duration": p.get("duration")}
            for p in pages
        ],
    }


def collect_snapshot(http, item):
    if item["platform"] == "youtube":
        value = parse_youtube(http.get(item["url"] + "&hl=en"), item["video_id"])
    else:
        value = parse_bilibili(
            http.get_json(
                f"https://api.bilibili.com/x/web-interface/view/detail?bvid={item['video_id']}"
            ),
            item["video_id"],
        )
        if value["provenance_status"] != "complete":
            raise AccessPaused(
                "Bilibili metadata is missing its title, description, authorship declaration or tags; screening remains paused."
            )
    return {
        **value,
        "observed_at": utc_now(),
        "source": "public_video_metadata",
        "receipt": http.last_receipt,
        "canonical_id": item["canonical_id"],
    }


def rate_scores(items):
    result = {}
    for platform in METRICS:
        group = [i for i in items if i["platform"] == platform]
        baselines = {}
        for metric in METRICS[platform]:
            observed = [i.get("engagement", {}) for i in group]
            observed = [
                s
                for s in observed
                if integer(s.get("views")) and integer(s.get(metric)) is not None
            ]
            rates = sorted(s[metric] / s["views"] for s in observed)
            baselines[metric] = (
                rates[len(rates) // 2] if len(rates) >= 10 else BASELINES[metric]
            )
        for item in group:
            snapshot, scores, features = item.get("engagement", {}), [], {}
            views = integer(snapshot.get("views"))
            for metric in METRICS[platform]:
                count, baseline = (
                    integer(snapshot.get(metric)),
                    max(1e-6, baselines[metric]),
                )
                if not views or count is None:
                    features[metric] = {
                        "raw_rate": None,
                        "adjusted_rate": None,
                        "score": 50,
                        "status": "missing",
                    }
                    scores.append(50)
                    continue
                adjusted = (count + 1000 * baseline) / (views + 1000)
                score = 100 / (
                    1
                    + math.exp(
                        -max(-8, min(8, math.log(max(adjusted, 1e-12) / baseline)))
                    )
                )
                features[metric] = {
                    "raw_rate": count / views,
                    "adjusted_rate": adjusted,
                    "score": round(score, 3),
                    "baseline": baseline,
                    "prior_views": 1000,
                    "status": "observed",
                }
                scores.append(score)
            result[item["canonical_id"]] = {
                "score": round(sum(scores) / len(scores), 3),
                "features": features,
                "baseline_scope": "retrieved_platform_buffer",
            }
    return result
