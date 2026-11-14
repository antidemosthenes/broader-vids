import math
import re


_BILIBILI_CANONICAL_ID = re.compile(r"bilibili:(BV[0-9A-Za-z]+):p([1-9][0-9]*)")


def _positive_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return False
    return type(value) is int or math.isfinite(value)


def _valid_cid(value):
    if type(value) is int:
        return value > 0
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None


def long_series(item):
    """Return verified Bilibili archive series metadata only when it exceeds five hours."""
    if not isinstance(item, dict) or item.get("platform") != "bilibili":
        return None
    metadata = item.get("engagement")
    if not isinstance(metadata, dict):
        return None
    if not (
        metadata.get("identity_verified") is True
        and metadata.get("scope") == "archive"
        and metadata.get("source") == "public_video_metadata"
    ):
        return None

    video_id = item.get("video_id")
    if not isinstance(video_id, str) or not video_id.startswith("BV"):
        return None
    item_canonical_id = item.get("canonical_id")
    canonical_id = metadata.get("canonical_id")
    if canonical_id is not None and canonical_id != item_canonical_id:
        return None
    canonical_part = None
    if item_canonical_id is not None:
        match = _BILIBILI_CANONICAL_ID.fullmatch(item_canonical_id)
        if not match or match.group(1) != video_id:
            return None
        canonical_part = int(match.group(2))
    item_part = item.get("part_index")
    if item_part is not None and (type(item_part) is not int or item_part < 1):
        return None
    if (
        canonical_part is not None
        and item_part is not None
        and canonical_part != item_part
    ):
        return None

    pages = metadata.get("pages")
    if not isinstance(pages, list) or len(pages) < 2:
        return None
    page_numbers = []
    durations = []
    cids = []
    titles = []
    for page in pages:
        if not isinstance(page, dict):
            return None
        number = page.get("page")
        duration = page.get("duration")
        if type(number) is not int or number < 1 or not _positive_number(duration):
            return None
        page_numbers.append(number)
        durations.append(duration)
        if "cid" in page and page["cid"] is not None:
            if not _valid_cid(page["cid"]):
                return None
            cids.append(str(page["cid"]))
        title = page.get("part")
        if isinstance(title, str):
            titles.append((number, title[:120]))

    expected = list(range(1, len(pages) + 1))
    if sorted(page_numbers) != expected:
        return None
    selected_part = item_part if item_part is not None else canonical_part
    if selected_part is not None and selected_part not in page_numbers:
        return None
    if len(cids) != len(set(cids)):
        return None
    declared_count = metadata.get("declared_part_count")
    if declared_count is not None and (
        type(declared_count) is not int or declared_count != len(pages)
    ):
        return None

    total_duration = sum(durations)
    if total_duration <= 18000 or not _positive_number(total_duration):
        return None

    titles.sort()
    titles = titles[:6] + titles[-6:] if len(titles) > 12 else titles
    title = metadata.get("title")
    description = metadata.get("description")
    return {
        "source": "bilibili_parts",
        "video_id": video_id,
        "part_count": len(pages),
        "total_duration_seconds": total_duration,
        "title": title[:120] if isinstance(title, str) else "",
        "part_titles": [value for _, value in titles],
        "description": description[:800] if isinstance(description, str) else "",
    }
