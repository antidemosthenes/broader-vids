from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path


_STABLE_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
_FILES = (
    "package.json",
    "package-lock.json",
    "plugin.json",
    "pyproject.toml",
    "uv.lock",
    "src/broader_vids/__init__.py",
)


def _one(pattern: str, text: str, label: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"expected one {label}")
    return matches[0]


def _json(path: Path, text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"malformed JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON metadata must be an object: {path.name}")
    return value


def _version(value: object, label: str) -> str:
    if not isinstance(value, str) or not _STABLE_VERSION.fullmatch(value):
        raise ValueError(f"invalid stable version in {label}")
    return value


def set_version(root: str | Path, version: str) -> dict[str, object]:
    version = _version(version, "requested version")
    root = Path(root).resolve()
    paths = {name: root / name for name in _FILES}
    originals: dict[str, bytes] = {}
    texts: dict[str, str] = {}
    for name, path in paths.items():
        info = path.lstat()
        if (
            path.resolve() != path
            or stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
        ):
            raise ValueError(f"release output is not a regular file: {name}")
        originals[name] = path.read_bytes()
        try:
            texts[name] = originals[name].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"release output is not UTF-8: {name}") from exc

    package = _json(paths["package.json"], texts["package.json"])
    package_lock = _json(paths["package-lock.json"], texts["package-lock.json"])
    plugin = _json(paths["plugin.json"], texts["plugin.json"])
    if package.get("name") != "broader-vids" or plugin.get("name") != "broader-vids":
        raise ValueError("unexpected package name")
    lock_packages = package_lock.get("packages")
    if not isinstance(lock_packages, dict) or not isinstance(
        lock_packages.get(""), dict
    ):
        raise ValueError("package-lock.json is missing packages['']")
    if (
        package_lock.get("name") != "broader-vids"
        or lock_packages[""].get("name") != "broader-vids"
    ):
        raise ValueError("unexpected package-lock name")

    pyproject_text = texts["pyproject.toml"]
    uv_text = texts["uv.lock"]
    try:
        pyproject = tomllib.loads(pyproject_text)
        uv_lock = tomllib.loads(uv_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("malformed TOML release metadata") from exc
    project = pyproject.get("project")
    uv_entries = uv_lock.get("package")
    if not isinstance(project, dict) or not isinstance(uv_entries, list):
        raise ValueError("TOML metadata is missing release package data")
    uv_packages = [
        entry
        for entry in uv_entries
        if isinstance(entry, dict) and entry.get("name") == "broader-vids"
    ]
    if len(uv_packages) != 1 or uv_packages[0].get("source") != {"editable": "."}:
        raise ValueError("uv.lock must contain one editable broader-vids package")
    init_text = texts["src/broader_vids/__init__.py"]
    init_match = _one(
        r'^__version__\s*=\s*(["\'])([^"\']+)\1\s*$', init_text, "__version__"
    )

    old_versions = [
        _version(package.get("version"), "package.json"),
        _version(package_lock.get("version"), "package-lock.json"),
        _version(lock_packages[""].get("version"), "package-lock.json packages['']"),
        _version(plugin.get("version"), "plugin.json"),
        _version(project.get("version"), "pyproject.toml"),
        _version(uv_packages[0].get("version"), "uv.lock"),
        _version(init_match.group(2), "__init__.py"),
    ]
    if len(set(old_versions)) != 1:
        raise ValueError("release version fields do not agree")
    old = old_versions[0]
    if tuple(map(int, version.split("."))) < tuple(map(int, old.split("."))):
        raise ValueError("release version cannot be a downgrade")

    files = package.get("files")
    wheel = f"python/broader_vids-{old}-*.whl"
    if (
        not isinstance(files, list)
        or files.count(wheel) != 1
        or sum(
            isinstance(entry, str)
            and re.fullmatch(r"python/broader_vids-.+-\*\.whl", entry) is not None
            for entry in files
        )
        != 1
    ):
        raise ValueError("package.json must contain the exact wheel whitelist entry")
    if old == version:
        return {"version": version, "files": []}

    edits = dict(texts)
    package["version"] = version
    package["files"] = [
        f"python/broader_vids-{version}-*.whl" if entry == wheel else entry
        for entry in files
    ]
    package_lock["version"] = version
    lock_packages[""]["version"] = version
    plugin["version"] = version
    edits["package.json"] = json.dumps(package, indent=2, ensure_ascii=False) + "\n"
    edits["package-lock.json"] = (
        json.dumps(package_lock, indent=2, ensure_ascii=False) + "\n"
    )
    edits["plugin.json"] = json.dumps(plugin, indent=2, ensure_ascii=False) + "\n"

    project_match = _one(
        r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", pyproject_text, "project section"
    )
    section = project_match.group(1)
    version_match = _one(
        r'(?m)^(version\s*=\s*)(["\'])([^"\']+)(\2\s*(?:#.*)?)$',
        section,
        "project version",
    )
    project_text = (
        section[: version_match.start(3)] + version + section[version_match.end(3) :]
    )
    edits["pyproject.toml"] = (
        pyproject_text[: project_match.start(1)]
        + project_text
        + pyproject_text[project_match.end(1) :]
    )

    uv_match = _one(
        r'(?ms)^\[\[package\]\]\s*\n(?:(?!^\[\[package\]\]).)*?^name\s*=\s*["\']broader-vids["\']\s*\n(?:(?!^\[\[package\]\]).)*?^version\s*=\s*["\']([^"\']+)["\']',
        uv_text,
        "uv package version",
    )
    edits["uv.lock"] = (
        uv_text[: uv_match.start(1)] + version + uv_text[uv_match.end(1) :]
    )
    edits["src/broader_vids/__init__.py"] = (
        init_text[: init_match.start(2)] + version + init_text[init_match.end(2) :]
    )
    changed = [name for name in _FILES if edits[name] != texts[name]]

    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    recovery = set()
    try:
        for name in changed:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{paths[name].name}.", suffix=".tmp", dir=paths[name].parent
            )
            staged[name] = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(edits[name].encode("utf-8"))
            mode = stat.S_IMODE(paths[name].lstat().st_mode)
            os.chmod(staged[name], mode)
            fd, backup_name = tempfile.mkstemp(
                prefix=f".{paths[name].name}.", suffix=".bak", dir=paths[name].parent
            )
            backups[name] = Path(backup_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(originals[name])
            os.chmod(backups[name], mode)
        replaced: list[str] = []
        try:
            for name in changed:
                os.replace(staged[name], paths[name])
                replaced.append(name)
        except OSError as error:
            for name in reversed(replaced):
                try:
                    os.replace(backups[name], paths[name])
                except OSError:
                    recovery.add(backups[name])
            if recovery:
                saved = ", ".join(str(path) for path in sorted(recovery))
                raise OSError(
                    "Version rollback is incomplete. Recovery copies: " + saved
                ) from error
            raise
    finally:
        for path in (*staged.values(), *backups.values()):
            if path in recovery:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return {"version": version, "files": changed}
