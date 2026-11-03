import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from release import ROOT, REGISTRY, check, filenames, verify as verify_artifacts

REPO = "antidemosthenes/broader-vids"
API = "https://api.github.com/repos/" + REPO


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version
    ):
        raise ValueError("A release requires a stable X.Y.Z version.")
    return tuple(map(int, version.split(".")))


def request_json(url, *, data=None, token=None, missing=False, timeout=15):
    headers = {"User-Agent": "Broader-Vids-release", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    raw = None if data is None else json.dumps(data).encode()
    if raw is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=raw, headers=headers), timeout=timeout
        ) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Release metadata exceeds its size limit.")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if missing and code == 404:
            return None
        raise ValueError(f"Release service returned HTTP {code}.") from error


def registry(version, *, timeout=15):
    return request_json(
        f"{REGISTRY}/broader-vids/{version}?release_check={time.time_ns()}",
        missing=True,
        timeout=timeout,
    )


def git_value(*arguments):
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *arguments], text=True, stderr=subprocess.DEVNULL
    ).strip()


def source_commit(commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError(
            "The source commit must contain forty lowercase hexadecimal characters."
        )
    package = json.loads(git_value("show", commit + ":package.json"))
    marker = package.get("broaderVidsRelease", {})
    source = marker.get("sourceCommit") if isinstance(marker, dict) else None
    if (
        isinstance(source, str)
        and re.fullmatch(r"[a-f0-9]{40}", source)
        and source != commit
    ):
        version_tuple(package.get("version"))
        try:
            tagged = git_value(
                "rev-parse",
                "--verify",
                "refs/tags/v" + package["version"] + "^{commit}",
            )
            parent = git_value("rev-parse", commit + "^1")
        except subprocess.CalledProcessError:
            tagged, parent = None, None
        if tagged == commit and parent == source:
            return {"source_sha": source, "recovery": True}
    return {"source_sha": commit, "recovery": False}


def readiness(source_sha, main_sha):
    current = source_commit(main_sha)
    current_source = current["source_sha"]
    if main_sha != source_sha and not (
        current["recovery"] and current_source == source_sha
    ):
        return {
            "eligible": False,
            "reason": "A newer main update supersedes this release.",
        }
    return {"eligible": True}


def choose_version(declared, latest, source_sha):
    base = version_tuple(declared)
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError(
            "The source commit must contain forty lowercase hexadecimal characters."
        )
    if latest is None:
        raise ValueError(
            "Publish the first stable npm version before enabling automatic releases."
        )
    public = version_tuple(latest.get("version"))
    if latest.get("broaderVidsRelease", {}).get("sourceCommit") == source_sha:
        if public < base:
            raise ValueError(
                "Published source metadata conflicts with the declared version."
            )
        return {"version": latest["version"], "reused": True}
    target = max(base, (public[0], public[1], public[2] + 1))
    return {"version": ".".join(map(str, target)), "reused": False}


def plan(source_sha):
    metadata = check()
    result = choose_version(metadata["version"], registry("latest"), source_sha)
    return {**result, "tag": "v" + result["version"], "source_sha": source_sha}


def integrity(archive):
    digest = hashlib.sha512()
    with Path(archive).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha512-" + base64.b64encode(digest.digest()).decode()


def publication(version, archive):
    version_tuple(version)
    current = registry(version)
    if current is not None:
        if current.get("dist", {}).get("integrity") != integrity(archive):
            raise ValueError(
                "The immutable published version differs from the verified archive."
            )
        return {"action": "reuse", "version": version}
    latest = registry("latest")
    if latest is not None and version_tuple(latest.get("version")) >= version_tuple(
        version
    ):
        raise ValueError(
            "A newer public version already exists. Refuse to move latest backwards."
        )
    return {"action": "publish", "version": version}


def verify_published(version, archive):
    version_tuple(version)
    expected = integrity(archive)
    deadline = time.monotonic() + 60
    for attempt in range(20):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        current = registry(version, timeout=min(15, remaining))
        if current is not None:
            if current.get("dist", {}).get("integrity") != expected:
                raise ValueError(
                    "The registry version differs from the verified archive."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            latest = registry("latest", timeout=min(15, remaining))
            if latest is not None and version_tuple(
                latest.get("version")
            ) >= version_tuple(version):
                return {"verified": True, "version": version, "integrity": expected}
        remaining = deadline - time.monotonic()
        if remaining <= 0 or attempt == 19:
            break
        time.sleep(min(2, remaining))
    raise ValueError(
        "The public registry did not expose the verified release within its verification allowance."
    )


def github_release(version, directory):
    version_tuple(version)
    verify_artifacts(directory)
    token = os.environ.get("GH_TOKEN")
    source_sha = os.environ.get("RELEASE_SHA", "")
    if not token or not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError(
            "GitHub release publication needs a job token and the verified release commit."
        )
    tag = "v" + version
    commit = request_json(API + "/commits/" + tag, token=token)
    if commit.get("sha") != source_sha:
        raise ValueError(
            "The GitHub tag no longer identifies the verified release commit."
        )
    release = request_json(API + "/releases/tags/" + tag, token=token, missing=True)
    if release is None:
        latest = registry("latest")
        release = request_json(
            API + "/releases",
            token=token,
            data={
                "tag_name": tag,
                "target_commitish": source_sha,
                "name": "Broader Vids " + version,
                "generate_release_notes": True,
                "draft": False,
                "prerelease": False,
                "make_latest": "true"
                if latest and latest.get("version") == version
                else "false",
            },
        )
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    upload_url = release["upload_url"].split("{", 1)[0]
    if (
        urllib.parse.urlsplit(upload_url).scheme != "https"
        or urllib.parse.urlsplit(upload_url).netloc != "uploads.github.com"
    ):
        raise ValueError("Unexpected GitHub asset upload address.")
    for name in [*filenames(version), "release.json", "SHA256SUMS"]:
        path = Path(directory) / name
        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError(
                "A release asset exceeds the permitted size or uses a symbolic link."
            )
        content = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if name in assets:
            asset = assets[name]
            if asset.get("digest") != digest or asset.get("size") != len(content):
                raise ValueError(
                    "An existing GitHub release asset differs from the verified file: "
                    + name
                )
            continue
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/octet-stream",
            "User-Agent": "Broader-Vids-release",
        }
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    upload_url + "?" + urllib.parse.urlencode({"name": name}),
                    data=content,
                    headers=headers,
                ),
                timeout=30,
            ) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("GitHub upload metadata exceeds its size limit.")
                uploaded = json.loads(raw)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise ValueError(f"GitHub asset upload returned HTTP {code}.") from error
        if uploaded.get("size") != len(content) or uploaded.get("digest") != digest:
            raise ValueError(
                "GitHub did not confirm the uploaded asset checksum: " + name
            )
    return {"verified": True, "url": release["html_url"], "version": version}


def main():
    parser = argparse.ArgumentParser(
        description="Plan and verify automatic public releases."
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    selected = commands.add_parser("source")
    selected.add_argument("--commit", required=True)
    selected = commands.add_parser("readiness")
    selected.add_argument("--source-sha", required=True)
    selected.add_argument("--main-sha", required=True)
    selected = commands.add_parser("plan")
    selected.add_argument("--source-sha", required=True)
    for operation in ("publication", "verify-published"):
        selected = commands.add_parser(operation)
        selected.add_argument("--version", required=True)
        selected.add_argument("--archive", type=Path, required=True)
    selected = commands.add_parser("github-release")
    selected.add_argument("--version", required=True)
    selected.add_argument("--artifacts-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.operation == "source":
        result = source_commit(args.commit)
    elif args.operation == "readiness":
        result = readiness(args.source_sha, args.main_sha)
    elif args.operation == "plan":
        result = plan(args.source_sha)
    elif args.operation == "github-release":
        result = github_release(args.version, args.artifacts_dir)
    else:
        operation = publication if args.operation == "publication" else verify_published
        result = operation(args.version, args.archive)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
