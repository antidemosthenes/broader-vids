from urllib.parse import parse_qs, urlsplit


def caption_tracks(info, platform):
    preferred = ("zh", "en") if platform == "bilibili" else ("en", "zh")
    candidates = []
    for source, languages in (
        ("author", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ):
        for language, tracks in languages.items():
            normalized = language.removeprefix("ai-")
            preference = next(
                (
                    i
                    for i, prefix in enumerate(preferred)
                    if normalized.startswith(prefix)
                ),
                len(preferred),
            )
            if platform == "bilibili" and preference == len(preferred):
                continue
            for extension in ("json3", "json", "vtt", "srt"):
                track = next(
                    (
                        t
                        for t in tracks
                        if t.get("ext") == extension
                        and (t.get("url") or isinstance(t.get("data"), str))
                    ),
                    None,
                )
                if track is None:
                    continue
                translated = bool(
                    parse_qs(urlsplit(track.get("url", "")).query).get("tlang")
                )
                if platform == "youtube" and translated:
                    continue
                rank = (
                    source != "author",
                    preference,
                    not normalized.endswith("-orig"),
                )
                candidates.append((rank, source, language, track))
                break
    selected, seen = [], set()
    for _, source, language, track in sorted(candidates, key=lambda t: t[0]):
        key = (track.get("url"), track.get("ext"), track.get("data"))
        if key not in seen:
            selected.append((source, language, track))
            seen.add(key)
    return selected
