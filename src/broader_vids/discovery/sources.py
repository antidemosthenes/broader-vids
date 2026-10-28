from dataclasses import dataclass, field
from html.parser import HTMLParser
import html
import re
from urllib.parse import urlencode, urlsplit

from broader_vids.http import AccessPaused
from broader_vids.identity import BVID, YOUTUBE_ID
from broader_vids.parsing import embedded_json, walk
from broader_vids.discovery.state import pinia_state


CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")


def duration_seconds(label):
    if not isinstance(label, str) or not re.fullmatch(
        r"\d+(?::\d{1,2}){1,2}", label.strip()
    ):
        return None
    parts = [int(p) for p in label.strip().split(":")]
    if any(p >= 60 for p in parts[1:]):
        return None
    result = 0
    for part in parts:
        result = result * 60 + part
    return result or None


def text_value(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    return (
        value.get("simpleText")
        or value.get("content")
        or "".join(r.get("text", "") for r in value.get("runs", []))
    )


def youtube_page(value, creator_id=None):
    roots = (
        value.get("contents")
        or value.get("onResponseReceivedActions")
        or value.get("onResponseReceivedCommands")
    )
    if not isinstance(roots, (dict, list)):
        raise AccessPaused("YouTube returned an unrecognized discovery schema.")
    items, seen, continuation = [], set(), None
    for node in walk(roots):
        command = node.get("continuationEndpoint", {}).get("continuationCommand", {})
        if command.get("token"):
            continuation = command["token"]
        renderer = node.get("videoRenderer") or node.get("gridVideoRenderer")
        lockup = node.get("lockupViewModel")
        if renderer:
            identifier = renderer.get("videoId", "")
            title = text_value(renderer.get("title"))
            duration = duration_seconds(text_value(renderer.get("lengthText")))
            published = text_value(renderer.get("publishedTimeText"))
            byline = renderer.get("ownerText") or renderer.get("longBylineText") or {}
            author = text_value(byline)
            owner = next(
                (
                    n["browseEndpoint"].get("browseId")
                    for n in walk(byline)
                    if "browseEndpoint" in n
                ),
                creator_id,
            )
        elif lockup and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            identifier = lockup.get("contentId", "")
            metadata = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = text_value(metadata.get("title"))
            labels = [
                n["text"]
                for n in walk(lockup.get("contentImage", {}))
                if isinstance(n.get("text"), str)
            ]
            duration = next(
                (d for label in labels if (d := duration_seconds(label))), None
            )
            rows = [
                text_value(n.get("text"))
                for n in walk(metadata.get("metadata", {}))
                if "text" in n
            ]
            published = next(
                (
                    s
                    for s in rows
                    if "ago" in s or re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)
                ),
                "",
            )
            owner, author = creator_id, ""
        else:
            continue
        if not YOUTUBE_ID.fullmatch(identifier) or identifier in seen or not title:
            continue
        seen.add(identifier)
        if creator_id and owner != creator_id:
            continue
        items.append(
            {
                "platform": "youtube",
                "video_id": identifier,
                "canonical_id": f"youtube:{identifier}",
                "url": f"https://www.youtube.com/watch?v={identifier}",
                "title": title,
                "creator_id": owner,
                "creator_name": author,
                "duration_seconds": duration,
                "published_label": published,
                "access_evidence": "public_listing",
                "part_index": 1,
            }
        )
    if (
        not items
        and not continuation
        and not any(
            key in node
            for node in walk(roots)
            for key in (
                "twoColumnSearchResultsRenderer",
                "twoColumnBrowseResultsRenderer",
                "richGridRenderer",
                "appendContinuationItemsAction",
            )
        )
    ):
        raise AccessPaused(
            "YouTube did not expose a recognizable video-listing or empty-results schema."
        )
    return items, continuation


class YouTubeDiscovery:
    def __init__(self, http):
        self.http = http

    def bootstrap(self):
        body = self.http.get("https://www.youtube.com/?hl=en")
        match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"\s]+)"', body)
        if match is None:
            raise AccessPaused("YouTube did not expose its public web client version.")
        return match.group(1)

    def search(self, query, version, continuation=None):
        payload = {
            "context": {
                "client": {"clientName": "WEB", "clientVersion": version, "hl": "en"}
            }
        }
        payload.update(
            {"continuation": continuation} if continuation else {"query": query}
        )
        return youtube_page(
            self.http.post_json(
                "https://www.youtube.com/youtubei/v1/search?prettyPrint=false", payload
            )
        )

    def creator(self, identifier, version, continuation=None):
        if not CHANNEL_ID.fullmatch(identifier):
            raise ValueError("Invalid public YouTube channel ID.")
        if continuation:
            value = self.http.post_json(
                "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false",
                {
                    "context": {
                        "client": {
                            "clientName": "WEB",
                            "clientVersion": version,
                            "hl": "en",
                        }
                    },
                    "continuation": continuation,
                },
            )
        else:
            body = self.http.get(
                f"https://www.youtube.com/channel/{identifier}/videos?hl=en"
            )
            value = embedded_json(body, r"(?:var\s+)?ytInitialData\s*=\s*")
        return youtube_page(value, identifier)


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    text: list = field(default_factory=list)

    def nodes(self):
        yield self
        for child in self.children:
            yield from child.nodes()

    def content(self):
        return "".join(self.text) + "".join(child.content() for child in self.children)


class Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag not in {"script", "style"}:
            self.stack[-1].text.append(data)


def bilibili_cards(body, creator_id=None):
    parser = Cards()
    parser.feed(body)
    items, seen, valid_cards = [], set(), 0
    for card in parser.root.nodes():
        if "bili-video-card" not in card.attrs.get("class", "").split():
            continue
        nodes = list(card.nodes())
        hrefs = [n.attrs.get("href", "") for n in nodes if n.tag == "a"]
        link = next(
            (
                urlsplit("https:" + h if h.startswith("//") else h)
                for h in hrefs
                if re.match(
                    r"(?:https:)?//www\.bilibili\.com/(?:zh-Hans/)?video/BV1", h
                )
            ),
            None,
        )
        if not link or link.hostname != "www.bilibili.com":
            continue
        identifier = link.path.strip("/").split("/")[-1]
        if not BVID.fullmatch(identifier) or identifier in seen:
            continue
        title_node = next(
            (
                n
                for n in nodes
                if "bili-video-card__info--tit" in n.attrs.get("class", "").split()
            ),
            None,
        )
        if title_node is None:
            continue
        valid_cards += 1
        title = title_node.attrs.get("title") or title_node.content().strip()
        owner = next(
            (
                re.search(r"space\.bilibili\.com/(?:zh-Hans/)?(\d+)(?:[/?#]|$)", h)
                for h in hrefs
                if "space.bilibili.com" in h
            ),
            None,
        )
        owner_id = owner.group(1) if owner else None
        if creator_id and owner_id != str(creator_id):
            continue

        def label(class_name):
            return next(
                (
                    n.content().strip().lstrip("· ")
                    for n in nodes
                    if class_name in n.attrs.get("class", "").split()
                ),
                "",
            )

        seen.add(identifier)
        items.append(
            {
                "platform": "bilibili",
                "video_id": identifier,
                "canonical_id": f"bilibili:{identifier}:p1",
                "url": f"https://www.bilibili.com/video/{identifier}",
                "title": html.unescape(title),
                "creator_id": owner_id,
                "creator_name": label("bili-video-card__info--author"),
                "duration_seconds": duration_seconds(
                    label("bili-video-card__stats__duration")
                ),
                "published_label": label("bili-video-card__info--date"),
                "part_index": 1,
                "access_evidence": "public_listing",
                "duration_scope": "listing_archive_or_default_part_unknown",
            }
        )
    if not items and not valid_cards:
        state = pinia_state(body)
        if state is not None:
            response = state.get("searchResponse", {}).get("searchAllResponse")
            if not isinstance(response, dict) or not isinstance(
                response.get("result"), list
            ):
                raise AccessPaused(
                    "Bilibili public state did not contain a recognized search-results response."
                )
            for node in walk(response):
                identifier = node.get("bvid", "")
                owner_id = str(node.get("mid", ""))
                if (
                    not BVID.fullmatch(identifier)
                    or identifier in seen
                    or not node.get("title")
                    or not owner_id.isdigit()
                ):
                    continue
                if creator_id and owner_id != str(creator_id):
                    continue
                seen.add(identifier)
                duration = node.get("duration")
                title = html.unescape(re.sub(r"<[^>]+>", "", node["title"]))
                items.append(
                    {
                        "platform": "bilibili",
                        "video_id": identifier,
                        "canonical_id": f"bilibili:{identifier}:p1",
                        "url": f"https://www.bilibili.com/video/{identifier}",
                        "title": title,
                        "creator_id": owner_id,
                        "creator_name": node.get("author", ""),
                        "description": node.get("description", ""),
                        "duration_seconds": duration
                        if type(duration) is int and duration > 0
                        else duration_seconds(duration),
                        "published_at": node.get("pubdate"),
                        "published_label": str(node.get("pubdate", "")),
                        "part_index": 1,
                        "access_evidence": "public_listing",
                        "duration_scope": "listing_archive_or_default_part_unknown",
                    }
                )
        elif not any(s in body for s in ("没有找到", "暂无数据")):
            raise AccessPaused(
                "Bilibili did not expose recognizable public video cards."
            )
    return items


class BilibiliDiscovery:
    def __init__(self, http):
        self.http = http

    def home(self):
        return bilibili_cards(self.http.get("https://www.bilibili.com/zh-Hans")), None

    def search(self, query, page=1, order="totalrank"):
        url = "https://search.bilibili.com/zh-Hans/all?" + urlencode(
            {"keyword": query, "page": page, "order": order}
        )
        return bilibili_cards(self.http.get(url)), page + 1

    def creator(self, identifier, name, page=1):
        if not re.fullmatch(r"[1-9]\d{0,18}", str(identifier)) or not name:
            raise ValueError(
                "Creator-filtered Bilibili search requires an observed public creator ID and name."
            )
        items, next_page = self.search(name, page, "click")
        return [i for i in items if i["creator_id"] == str(identifier)], next_page
