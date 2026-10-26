import re
from urllib.parse import parse_qs, urlsplit

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{10}[AEIMQUYcgkosw048]\Z")
BVID = re.compile(r"BV1[1-9A-HJ-NP-Za-km-z]{9}\Z")


def identify(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Evidence requires an ordinary HTTPS video URL.")
    query = parse_qs(parsed.query)
    if parsed.hostname == "www.youtube.com" and parsed.path == "/watch":
        identifier = query.get("v", [""])[0]
        if not YOUTUBE_ID.fullmatch(identifier):
            raise ValueError("Invalid YouTube video ID.")
        return {
            "platform": "youtube",
            "video_id": identifier,
            "part_index": 1,
            "canonical_id": f"youtube:{identifier}",
            "url": f"https://www.youtube.com/watch?v={identifier}",
        }
    if parsed.hostname == "www.bilibili.com":
        identifier = (
            parsed.path.strip("/").removeprefix("zh-Hans/").removeprefix("video/")
        )
        try:
            part = int(query.get("p", ["1"])[0])
        except ValueError:
            raise ValueError(
                "Bilibili part index must be a positive integer."
            ) from None
        if not BVID.fullmatch(identifier) or not 1 <= part <= 10000:
            raise ValueError("Invalid Bilibili video or part index.")
        return {
            "platform": "bilibili",
            "video_id": identifier,
            "part_index": part,
            "canonical_id": f"bilibili:{identifier}:p{part}",
            "url": f"https://www.bilibili.com/video/{identifier}?p={part}",
        }
    raise ValueError("Use a canonical YouTube watch URL or Bilibili video URL.")
