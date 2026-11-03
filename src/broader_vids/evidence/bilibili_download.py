from urllib.parse import urlsplit

from yt_dlp.extractor.bilibili import BiliBiliIE


class ReliableBilibiliIE(BiliBiliIE):
    @classmethod
    def ie_key(cls):
        return BiliBiliIE.ie_key()

    def extract_formats(self, play_info):
        formats = super().extract_formats(play_info)
        dash = play_info.get("dash") or {}
        streams = [*(dash.get("video") or []), *(dash.get("audio") or [])]
        by_url = {
            s.get("baseUrl") or s.get("base_url") or s.get("url"): s for s in streams
        }
        for entry in formats:
            native = by_url.get(entry.get("url"), {})
            backups = native.get("backupUrl") or native.get("backup_url") or []
            urls = list(dict.fromkeys([entry["url"], *backups]))
            urls = [
                url
                for url in urls
                if urlsplit(url).scheme == "https"
                and (
                    (urlsplit(url).hostname or "").endswith(".bilivideo.com")
                    or (urlsplit(url).hostname or "").endswith(".akamaized.net")
                )
            ]
            if urls:
                entry["url"] = urls[0]
                entry["backup_urls"] = urls[1:]
        return formats
