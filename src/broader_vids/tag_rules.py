import unicodedata


BUILTIN_TAGS = {"bilibili": ("视频翻译",)}


def normalize_tag(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def tag_rules(profile, platform):
    profile = profile or {}
    disabled = {
        normalize_tag(tag)
        for tag in profile.get("disabled_builtin_tags", {}).get(platform, [])
    }
    rules = {
        normalize_tag(tag): {"tag": tag, "source": "manual", "editable": True}
        for tag in profile.get("excluded_tags", {}).get(platform, [])
    }
    for tag in BUILTIN_TAGS.get(platform, ()):
        if normalize_tag(tag) not in disabled:
            rules[normalize_tag(tag)] = {
                "tag": tag,
                "source": "builtin",
                "editable": True,
            }
    return list(rules.values())
