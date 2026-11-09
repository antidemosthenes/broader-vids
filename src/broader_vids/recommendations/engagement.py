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


class YouTubeMetadataError(AccessPaused):
    def __init__(self, identifier, player, parse_error=None):
        from broader_vids.evidence.media import public_error

        details = player.get("videoDetails")
        actual = details.get("videoId") if isinstance(details, dict) else None
        playback = player.get("playabilityStatus")
        playback = playback if isinstance(playback, dict) else {}
        status = playback.get("status")
        status = status[:80] if isinstance(status, str) else None
        reason = playback.get("reason")
        reason = public_error(reason)[:500] if isinstance(reason, str) else None
        actual = (
            actual
            if isinstance(actual, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", actual)
            else "<invalid-video-id>"
            if actual is not None
            else None
        )
        temporary_error = status == "ERROR" and bool(
            re.search(
                r"(?i)try again|temporary|temporarily|something went wrong",
                reason or "",
            )
        )
        self.retryable = not actual and (status in (None, "OK") or temporary_error)
        if status == "LOGIN_REQUIRED":
            self.recovery = {
                "action": "review_youtube_access",
                "url": "https://www.youtube.com/watch?v=" + identifier,
                "message": "Stop automated YouTube requests. Review this video in your system browser for any required sign-in or verification. Continue the saved round only after access is restored. Browser sign-in is not imported automatically.",
            }
        self.metadata_diagnostic = {
            "requested_video_id": identifier,
            "returned_video_id": actual,
            "playability_status": status,
            "playability_reason": reason,
            "player_parse_error": public_error(parse_error) if parse_error else None,
        }
        if actual:
            message = f"YouTube metadata identity mismatch for {identifier} (returned {actual})."
        elif status and status != "OK":
            message = f"YouTube metadata for {identifier} is unavailable ({status}"
            message += f": {reason})." if reason else ")."
        else:
            message = f"YouTube metadata for {identifier} lacks a verified video ID."
        super().__init__(message)


def youtube_snapshot(http, item):
    for attempt in (1, 2):
        try:
            return parse_youtube(http.get(item["url"] + "&hl=en"), item["video_id"])
        except YouTubeMetadataError as error:
            diagnostic = {**error.metadata_diagnostic, "attempt": attempt}
            recorder = getattr(http, "metadata_failed", None)
            if recorder:
                recorder(diagnostic)
            if not error.retryable or attempt == 2:
                raise


def parse_youtube(body, identifier):
    try:
        player = embedded_json(body, r"(?:var\s+)?ytInitialPlayerResponse\s*=\s*")
    except AccessPaused as error:
        raise YouTubeMetadataError(identifier, {}, parse_error=error) from None
    details = player.get("videoDetails", {})
    if not isinstance(details, dict) or details.get("videoId") != identifier:
        raise YouTubeMetadataError(identifier, player)
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
    microformat = player.get("microformat")
    micro = (
        microformat.get("playerMicroformatRenderer")
        if isinstance(microformat, dict)
        else None
    )
    micro = micro if isinstance(micro, dict) else {}
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


class BilibiliVideoUnavailable(AccessPaused):
    def __init__(self, identifier):
        self.attempts = 1
        super().__init__(
            f"Bilibili video {identifier} returned metadata code -404; availability could not be verified."
        )


def bilibili_snapshot(http, item):
    from broader_vids.evidence.media import MAX_VIDEO_RETRIES

    limit = 1 + MAX_VIDEO_RETRIES
    reader = getattr(http, "metadata_failure_count", None)
    previous = reader(item["canonical_id"], -404) if reader else 0
    if previous >= limit:
        error = BilibiliVideoUnavailable(item["video_id"])
        error.attempts = previous
        raise error
    for attempt in range(previous + 1, limit + 1):
        try:
            return parse_bilibili(
                http.get_json(
                    f"https://api.bilibili.com/x/web-interface/view/detail?bvid={item['video_id']}"
                ),
                item["video_id"],
            )
        except BilibiliVideoUnavailable as error:
            error.attempts = attempt
            recorder = getattr(http, "metadata_failed", None)
            if recorder:
                recorder(
                    {
                        "requested_video_id": item["video_id"],
                        "canonical_id": item["canonical_id"],
                        "api_code": -404,
                        "attempt": attempt,
                    }
                )
            if attempt == limit:
                raise


def parse_bilibili(value, identifier):
    if not isinstance(value, dict):
        raise AccessPaused("Bilibili count metadata has an unrecognized schema.")
    if value.get("code") == -404:
        raise BilibiliVideoUnavailable(identifier)
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
        value = youtube_snapshot(http, item)
    else:
        value = bilibili_snapshot(http, item)
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
