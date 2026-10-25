import json
import re
from urllib.parse import urlencode

from .http import AccessPaused
from .models import Archive, Outcome, Part, Resolution, SearchPage


YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{10}[AEIMQUYcgkosw048]\Z")
MAX_AID = (1 << 51) - 1


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
        raise AccessPaused("The expected public metadata object is missing; the response needs review.")
    try:
        value, _ = json.JSONDecoder().raw_decode(body[match.end():].lstrip())
    except json.JSONDecodeError:
        raise AccessPaused("The embedded metadata object could not be decoded.") from None
    if not isinstance(value, dict):
        raise AccessPaused("The embedded metadata object has an unexpected schema.")
    return value


def prefix_matches(video_id, prefix):
    return bool(YOUTUBE_ID.fullmatch(video_id)) and video_id[:5].lower() == prefix and video_id[5] == "-"


class YouTube:
    def __init__(self, http):
        self.http = http

    def bootstrap(self):
        body = self.http.get("https://www.youtube.com/?hl=en")
        match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"\s]+)"', body)
        if match is None:
            raise AccessPaused("YouTube did not expose its public client version; no search fallback was used.")
        return match.group(1)

    def search(self, prefix, client_version):
        response = self.http.post_json("https://www.youtube.com/youtubei/v1/search?prettyPrint=false", {
            "context": {"client": {"clientName": "WEB", "clientVersion": client_version, "hl": "en"}},
            "query": f'allinurl:"watch?v={prefix}"',
        })
        if "error" in response:
            raise AccessPaused("YouTube search returned an error; the prefix query remains pending.")
        contents = response.get("contents", {})
        if not isinstance(contents, dict) or "twoColumnSearchResultsRenderer" not in contents:
            raise AccessPaused("YouTube search schema is unrecognized; it was not treated as zero results.")
        ids = []
        continuation = False
        for node in walk(contents):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict) and isinstance(renderer.get("videoId"), str):
                ids.append(renderer["videoId"])
            continuation |= "continuationItemRenderer" in node
        return SearchPage(list(dict.fromkeys(ids)), continuation)

    def resolve(self, video_id):
        if not YOUTUBE_ID.fullmatch(video_id):
            raise ValueError("Invalid canonical YouTube video ID.")
        body = self.http.get("https://www.youtube.com/watch?" + urlencode({"v": video_id, "hl": "en"}))
        value = embedded_json(body, r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*')
        return self.parse_player(video_id, value)

    @staticmethod
    def parse_player(video_id, value):
        status = value.get("playabilityStatus", {}).get("status")
        if status in {"UNPLAYABLE", "LOGIN_REQUIRED", "LIVE_STREAM_OFFLINE"}:
            return Resolution(Outcome.UNAVAILABLE, reason=f"Public playability status: {status}")
        if status != "OK":
            return Resolution(Outcome.UNRESOLVED, reason=f"Ambiguous public playability status: {status}")
        details = value.get("videoDetails", {})
        if details.get("videoId") != video_id:
            return Resolution(Outcome.UNRESOLVED, reason="Player video ID did not match the requested ID.")
        duration = details.get("lengthSeconds")
        duration = int(duration) if str(duration).isdigit() else None
        views = details.get("viewCount")
        views = int(views) if str(views).isdigit() else None
        micro = value.get("microformat", {}).get("playerMicroformatRenderer", {})
        archive = Archive("youtube", video_id, str(details.get("title", "")),
                          (Part(video_id, str(details.get("title", "")), duration),),
                          details.get("channelId"), micro.get("publishDate"), views, "player_status_only")
        return Resolution(Outcome.VALID, archive)


class Bilibili:
    def __init__(self, http):
        self.http = http

    def resolve(self, aid):
        if isinstance(aid, bool) or not isinstance(aid, int) or not 1 <= aid <= MAX_AID:
            raise ValueError("AID lies outside the prototype encoding domain.")
        response = self.http.get_json(f"https://api.bilibili.com/x/web-interface/view?aid={aid}")
        return self.parse_archive(aid, response)

    @staticmethod
    def parse_archive(aid, response):
        code = response.get("code")
        if code in {-404, 62002, 62012}:
            return Resolution(Outcome.UNAVAILABLE, reason=f"Archive metadata unavailable (code {code}).")
        if code != 0:
            return Resolution(Outcome.UNRESOLVED, reason=f"Ambiguous platform response (code {code}).")
        data = response.get("data")
        if not isinstance(data, dict) or type(data.get("aid")) is not int or data["aid"] != aid or not isinstance(data.get("pages"), list):
            return Resolution(Outcome.UNRESOLVED, reason="Archive metadata schema or identifier did not match.")
        parts = []
        for page in data["pages"]:
            if not isinstance(page, dict) or type(page.get("cid")) is not int or page["cid"] <= 0:
                return Resolution(Outcome.UNRESOLVED, reason="The part list contains an unknown content ID.")
            duration = page.get("duration")
            if type(duration) is not int or duration < 0:
                duration = None
            parts.append(Part(f"bilibili:{aid}:{page['cid']}", str(page.get("part", "")), duration))
        if not parts or len({p.key for p in parts}) != len(parts):
            return Resolution(Outcome.UNRESOLVED, reason="The archive has an empty or duplicated part list.")
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        views = stat.get("view") if type(stat.get("view")) is int and stat["view"] >= 0 else None
        published = data.get("pubdate") if type(data.get("pubdate")) is int else None
        archive = Archive("bilibili", str(aid), str(data.get("title", "")), tuple(parts),
                          str(owner["mid"]) if "mid" in owner else None,
                          published, views, "metadata_only")
        return Resolution(Outcome.VALID, archive)

