import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from .providers import YOUTUBE_ID
from .discovery_sources import BVID


class EvidencePending(Exception):
    pass


def public_error(error):
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    message = re.sub(r"https?://\S+", "<url>", message)
    message = re.sub(r"(?i)(SESSDATA|bili_jct|DedeUserID|access_token|refresh_token|cookie)\s*[=:]\s*[^\s,;]+", r"\1=<redacted>", message)
    return message[:400]


def identify(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Evidence requires an ordinary HTTPS video URL.")
    query = parse_qs(parsed.query)
    if parsed.hostname == "www.youtube.com" and parsed.path == "/watch":
        identifier = query.get("v", [""])[0]
        if not YOUTUBE_ID.fullmatch(identifier):
            raise ValueError("Invalid YouTube video ID.")
        return {"platform": "youtube", "video_id": identifier, "part_index": 1, "canonical_id": f"youtube:{identifier}",
                "url": f"https://www.youtube.com/watch?v={identifier}"}
    if parsed.hostname == "www.bilibili.com":
        identifier = parsed.path.strip("/").removeprefix("zh-Hans/").removeprefix("video/")
        try:
            part = int(query.get("p", ["1"])[0])
        except ValueError:
            raise ValueError("Bilibili part index must be a positive integer.") from None
        if not BVID.fullmatch(identifier) or not 1 <= part <= 10000:
            raise ValueError("Invalid Bilibili video or part index.")
        return {"platform": "bilibili", "video_id": identifier, "part_index": part,
                "canonical_id": f"bilibili:{identifier}:p{part}", "url": f"https://www.bilibili.com/video/{identifier}?p={part}"}
    raise ValueError("Use a canonical YouTube watch URL or Bilibili video URL.")


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def ffmpeg_executable():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise EvidencePending("FFmpeg is unavailable; install the evidence extra with UV.") from None


def probe_media(path, timeout=30):
    process = subprocess.run([ffmpeg_executable(), "-hide_banner", "-nostdin", "-i", str(path),
                              "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
                             capture_output=True, timeout=timeout)
    output = process.stderr.decode("utf-8", errors="replace")
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    dimensions = re.search(r"Video:[^\n]*?\b(\d{2,5})x(\d{2,5})\b", output)
    if process.returncode or not duration or not dimensions:
        raise EvidencePending("FFmpeg could not verify a complete playable local video stream.")
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    if not 0 < seconds <= 5400:
        raise EvidencePending("The playable video/part is empty or exceeds the 90-minute exclusion.")
    return {"duration_seconds": seconds, "width": int(dimensions[1]), "height": int(dimensions[2]),
            "audio_present": bool(re.search(r"Stream[^\n]*Audio:", output))}


def extract_frame(media, timestamp, target, timeout=30, long_edge=768):
    process = subprocess.run([ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                              "-ss", str(timestamp), "-i", str(media), "-frames:v", "1",
                              "-vf", f"scale={long_edge}:{long_edge}:force_original_aspect_ratio=decrease", str(target)],
                             capture_output=True, timeout=timeout)
    if process.returncode or not Path(target).is_file():
        raise EvidencePending("FFmpeg could not extract the requested frame.")


def image_fingerprint(path):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise EvidencePending("Frame processing requires Pillow from the evidence extra.") from None
    with Image.open(path) as image:
        pixels = list(ImageOps.grayscale(image).resize((16, 16)).get_flattened_data())
        mean = sum(pixels) / len(pixels)
        return sum((value >= mean) << index for index, value in enumerate(pixels))


def parse_captions(body, format_name, duration):
    cues = []
    if format_name in {"json3", "json"}:
        value = json.loads(body)
        if "events" in value:
            for event in value["events"]:
                if event.get("segs"):
                    start = event.get("tStartMs", 0) / 1000
                    cues.append((start, start + event.get("dDurationMs", 0) / 1000,
                                 "".join(seg.get("utf8", "") for seg in event["segs"])))
        elif "body" in value:
            cues = [(c.get("from"), c.get("to"), c.get("content")) for c in value["body"]]
        else:
            raise ValueError("Unsupported caption JSON schema.")
    elif format_name in {"vtt", "srt"}:
        timestamp = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
        def seconds(match):
            return int(match[0] or 0) * 3600 + int(match[1]) * 60 + int(match[2]) + int(match[3]) / 1000
        for block in re.split(r"\r?\n\s*\r?\n", body):
            match = re.search(timestamp + r"\s*-->\s*" + timestamp + r"[^\n]*\n([\s\S]+)", block)
            if match:
                cues.append((seconds(match.groups()[:4]), seconds(match.groups()[4:8]), match[9]))
    else:
        raise ValueError("Supported caption formats are JSON3, Bilibili JSON, VTT and SRT.")
    result, previous = [], None
    for start, end, text in cues:
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (start, end)):
            continue
        if not 0 <= start < min(end, duration) or end > duration + 5 or not isinstance(text, str):
            continue
        text = html.unescape(re.sub(r"<[^>]*>", "", text)).strip()
        if not text or (previous and text == previous["text"] and start <= previous["end"]):
            continue
        record = {"id": f"C{len(result) + 1:05d}", "start": round(start, 3), "end": round(min(end, duration), 3), "text": text}
        result.append(record)
        previous = record
    return sorted(result, key=lambda cue: cue["start"])


def compact_captions(cues, max_characters=12000):
    if sum(len(c["text"]) for c in cues) <= max_characters:
        return list(cues)
    selected = {}
    allocation = max_characters // 5
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        index = round(fraction * (len(cues) - 1))
        indices = range(index, len(cues)) if fraction < 1 else range(len(cues) - 1, -1, -1)
        used = 0
        for offset in indices:
            cue = cues[offset]
            if used + len(cue["text"]) > allocation:
                break
            selected[cue["id"]] = cue
            used += len(cue["text"])
    return sorted(selected.values(), key=lambda cue: cue["start"])


class QuietLog:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


class VideoFetcher:
    def __init__(self, byte_limit, deadline, cookie_jar=None):
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            raise EvidencePending("Install the optional evidence dependencies with UV.") from None
        self.byte_limit, self.deadline = byte_limit, deadline
        self.downloaded_bytes = 0
        self._finished = {}
        self.options = {"quiet": True, "logger": QuietLog(), "no_warnings": True, "noplaylist": True,
                        "writesubtitles": True, "subtitleslangs": ["zh.*", "ai-zh.*", "en.*", "ai-en.*"],
                        "socket_timeout": 15, "retries": 0, "fragment_retries": 0, "extractor_retries": 0,
                        "js_runtimes": {"node": {}}, "cachedir": False, "max_filesize": byte_limit,
                        "format": "worst[height>=240][height<=480][vcodec!=none][acodec!=none]/worstvideo[height>=240][height<=480]+worstaudio/worst",
                        "progress_hooks": [self._progress], "ffmpeg_location": ffmpeg_executable()}
        self.ydl = YoutubeDL(self.options)
        if cookie_jar is not None:
            for cookie in cookie_jar:
                self.ydl.cookiejar.set_cookie(cookie)

    def _progress(self, progress):
        filename = progress.get("filename", "current")
        self._finished[filename] = progress.get("downloaded_bytes", 0)
        self.downloaded_bytes = sum(self._finished.values())
        if self.downloaded_bytes > self.byte_limit or time.monotonic() > self.deadline:
            raise EvidencePending("The evidence download byte/time budget was exhausted.")

    def metadata(self, identity):
        try:
            info = self.ydl.extract_info(identity["url"], download=False)
        except Exception as error:
            raise EvidencePending(f"{identity['platform']} content access failed ({type(error).__name__}): {public_error(error)}") from None
        if info.get("_type") in {"playlist", "multi_video"}:
            entries = list(info.get("entries") or [])
            if len(entries) != 1:
                raise EvidencePending("The extractor returned multiple playable units; explicit part resolution is required.")
            info = entries[0]
        duration = info.get("duration")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 5400:
            raise EvidencePending("The resolved playable duration is unknown or exceeds 90 minutes.")
        if info.get("is_live") or info.get("is_preview"):
            raise EvidencePending("Live streams and restricted previews are not full-video evidence.")
        identifier = str(info.get("id", ""))
        if identity["platform"] == "youtube" and identifier != identity["video_id"]:
            raise EvidencePending("The extractor returned a different YouTube video ID.")
        if identity["platform"] == "bilibili" and identity["part_index"] > 1:
            returned_part = parse_qs(urlsplit(info.get("webpage_url", "")).query).get("p", [str(info.get("playlist_index", ""))])[0]
            if returned_part != str(identity["part_index"]):
                raise EvidencePending("The requested Bilibili part was not independently identified by the extractor.")
        return info

    def captions(self, info, directory, platform):
        groups = (("author", info.get("subtitles") or {}), ("automatic", info.get("automatic_captions") or {}))
        preferred = ("zh", "en") if platform == "bilibili" else ("en", "zh")
        for source, languages in groups:
            for prefix in preferred:
                for language, tracks in languages.items():
                    if not language.removeprefix("ai-").startswith(prefix):
                        continue
                    track = next((t for extension in ("json3", "json", "vtt", "srt") for t in tracks if t.get("ext") == extension and (t.get("url") or isinstance(t.get("data"), str))), None)
                    if track is None:
                        continue
                    try:
                        inline = isinstance(track.get("data"), str)
                        if inline:
                            raw = track["data"].encode("utf-8")
                        else:
                            with self.ydl.urlopen(track["url"]) as response:
                                raw = response.read(2 * 1024 * 1024 + 1)
                        if len(raw) > 2 * 1024 * 1024:
                            raise EvidencePending("Caption response exceeded 2 MiB.")
                        cues = parse_captions(raw.decode("utf-8-sig"), track["ext"], info["duration"])
                    except Exception as error:
                        raise EvidencePending(f"Caption access/format failed ({type(error).__name__}); no transcript was invented.") from None
                    if cues:
                        target = Path(directory) / "captions.json"
                        target.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
                        return {"source": "automatic" if language.startswith("ai-") else source,
                                "language": language, "format": track["ext"], "path": str(target),
                                "sha256": file_hash(target), "cue_count": len(cues), "download_bytes": 0 if inline else len(raw),
                                "materialized_bytes": len(raw), "delivery": "extractor_inline" if inline else "caption_url"}
        return None

    def download(self, info, directory):
        output = Path(directory) / "media.%(ext)s"
        self.ydl.params["outtmpl"]["default"] = str(output)
        self.ydl.params["writesubtitles"] = False
        info.pop("requested_subtitles", None)
        self.ydl.params.update({"format": "worst[height>=240][height<=480][vcodec!=none][acodec!=none]/worstvideo[height>=240][height<=480]+worstaudio/worst",
                                "merge_output_format": "mp4", "overwrites": False})
        try:
            self.ydl.process_ie_result(info, download=True)
        except Exception as error:
            raise EvidencePending(f"Media download stopped ({type(error).__name__}): {public_error(error)}") from None
        candidates = [p for p in Path(directory).glob("media.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".flv"}]
        if len(candidates) != 1 or candidates[0].stat().st_size > self.byte_limit:
            raise EvidencePending("The downloader did not produce one video within the byte limit.")
        return candidates[0]
