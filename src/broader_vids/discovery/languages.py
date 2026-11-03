import threading
import unicodedata


class QueryLanguageError(ValueError):
    pass


_converter = None
_converter_lock = threading.Lock()


def simplified(value):
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                from opencc import OpenCC

                converter = OpenCC("t2s")
                converter.convert("繁體中文")
                _converter = converter
    return _converter.convert(value)


def search_query(platform, value):
    if platform not in {"youtube", "bilibili"}:
        raise ValueError("Use a supported search platform.")
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise QueryLanguageError("Search keywords must contain 1–200 characters.")
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    letters = [unicodedata.name(char, "") for char in value if char.isalpha()]
    latin = any("LATIN" in name for name in letters)
    han = any("CJK" in name or "IDEOGRAPH" in name for name in letters)
    if platform == "youtube":
        if not latin or any(
            "LATIN" not in name and "GREEK" not in name for name in letters
        ):
            raise QueryLanguageError(
                "YouTube search keywords must use English. Translate the keyword, not the interest or topic hierarchy."
            )
    elif not han or any(
        not any(script in name for script in ("LATIN", "GREEK", "CJK", "IDEOGRAPH"))
        for name in letters
    ):
        raise QueryLanguageError(
            "Bilibili search keywords must use Simplified Chinese. Latin technical names can remain inside a Chinese query."
        )
    if platform == "bilibili":
        value = simplified(value)
    if not value or len(value) > 200:
        raise QueryLanguageError(
            "Normalized search keywords must contain 1–200 characters."
        )
    return value
