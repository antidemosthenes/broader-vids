import html
import re
import unicodedata
import hashlib
import json
from urllib.parse import urlsplit, parse_qs

from broader_vids.tag_rules import tag_rules, normalize_tag


def provenance(item, profile=None):
    metadata = item.get("engagement", {})
    title = metadata.get("title") or item.get("title", "")
    description = metadata.get("description") or item.get("description", "")
    title = title if isinstance(title, str) else ""
    description = description if isinstance(description, str) else ""
    tags = metadata.get("tags", item.get("tags", []))
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    text = unicodedata.normalize("NFKC", html.unescape(title + "\n" + description))
    normalized_tags = {
        unicodedata.normalize("NFKC", t).strip().casefold() for t in tags
    }
    signals = []
    if "视频翻译" in normalized_tags:
        signals.append(
            {
                "id": "M1",
                "kind": "translated_video_tag",
                "source": "tags",
                "value": "视频翻译",
            }
        )
    if "中配" in title:
        signals.append(
            {
                "id": "M2",
                "kind": "chinese_dub_label",
                "source": "title",
                "value": "中配",
            }
        )
    if (
        re.search(r"(?i)(?<![a-z0-9_])youdub(?![a-z0-9_])", text)
        or "youdub" in normalized_tags
    ):
        signals.append(
            {
                "id": "M3",
                "kind": "translation_tool_disclosure",
                "source": "title_description_or_tags",
                "value": "YouDub",
            }
        )
    links = []
    for url in re.findall(r"https?://[^\s<>\"\u3000]+", html.unescape(description)):
        try:
            parts = urlsplit(url.rstrip(".,;，。；)）]"))
        except ValueError:
            continue
        identifier = None
        if parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            identifier = parse_qs(parts.query).get("v", [None])[0]
        elif parts.hostname == "youtu.be":
            identifier = parts.path.lstrip("/").split("/")[0]
        if identifier and re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
            links.append("https://www.youtube.com/watch?v=" + identifier)
    if links:
        signals.append(
            {
                "id": "M4",
                "kind": "youtube_source_link",
                "source": "description",
                "value": list(dict.fromkeys(links)),
            }
        )
    declared = metadata.get("declared_copyright")
    if declared in (1, 2):
        signals.append(
            {
                "id": "M5",
                "kind": "uploader_copyright_declaration",
                "source": "platform_metadata",
                "value": "original" if declared == 1 else "repost",
            }
        )
    rules = tag_rules(profile, item.get("platform"))
    excluded_user_tags = {
        normalize_tag(rule["tag"]) for rule in rules if rule["source"] == "manual"
    }
    user_matches = sorted(normalized_tags & excluded_user_tags)
    translation_blocked = "视频翻译" in normalized_tags and any(
        rule["source"] == "builtin" and normalize_tag(rule["tag"]) == "视频翻译"
        for rule in rules
    )
    return {
        "version": 1,
        "excluded": translation_blocked or bool(user_matches),
        "reason": "excluded_translation_tag"
        if translation_blocked
        else "excluded_user_tag"
        if user_matches
        else None,
        "excluded_user_tags": user_matches,
        "signals": signals,
        "tags_status": metadata.get("tags_status", "unknown"),
        "audio_detection_performed": False,
        "authorship_verified": False,
    }


def assessment_context(item):
    evidence = provenance(item)
    signals = evidence["signals"]
    if not any(s["id"] != "M5" or s["value"] == "repost" for s in signals):
        return None
    return {
        "signals": signals,
        "authorship_verified": False,
        "audio_detection_performed": False,
    }


def context_identity(context):
    return (
        {
            "originality_sha256": hashlib.sha256(
                json.dumps(context, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        }
        if context
        else {}
    )
