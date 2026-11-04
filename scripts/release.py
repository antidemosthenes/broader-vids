import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile

from build_plugin import build
from verify_package import main as verify_package


ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "https://registry.npmjs.org"
REPOSITORY = "https://github.com/antidemosthenes/broader-vids.git"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(tag=None):
    package = read_json(ROOT / "package.json")
    version = package["version"]
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("A public release requires a stable X.Y.Z version.")
    if tag is not None and tag != f"v{version}":
        raise ValueError("The release tag must match the package version exactly.")
    python = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    npm_lock = read_json(ROOT / "package-lock.json")
    claude_plugin = read_json(ROOT / ".claude-plugin/plugin.json")
    if claude_plugin["name"] != "broader-vids":
        raise ValueError("The Claude plugin manifest must use the broader-vids name.")
    versions = [
        read_json(ROOT / "plugin.json")["version"],
        claude_plugin["version"],
        python["project"]["version"],
        npm_lock["version"],
        npm_lock["packages"][""]["version"],
        next(p["version"] for p in lock["package"] if p["name"] == "broader-vids"),
    ]
    source = (ROOT / "src/broader_vids/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    if not match or match[1] != version or any(v != version for v in versions):
        raise ValueError(
            "Node, Python, Codex, Claude, and lock-file versions must agree."
        )
    if package.get("repository", {}).get("url") != REPOSITORY:
        raise ValueError("The release repository must match the npm trusted publisher.")
    if package.get("publishConfig") != {
        "access": "public",
        "registry": REGISTRY,
        "tag": "latest",
    }:
        raise ValueError(
            "The release must target the public npm registry and latest tag."
        )
    catalog = read_json(ROOT / ".agents/plugins/marketplace.json")
    expected = {
        "source": "npm",
        "package": "broader-vids",
        "version": "latest",
        "registry": REGISTRY,
    }
    if catalog["name"] != "broader-vids" or catalog["plugins"][0]["source"] != expected:
        raise ValueError("The public catalog must track the latest public npm release.")
    claude_catalog = read_json(ROOT / ".claude-plugin/marketplace.json")
    if (
        claude_catalog["name"] != "broader-vids"
        or claude_catalog["plugins"][0]["name"] != "broader-vids"
        or claude_catalog["plugins"][0]["source"] != expected
    ):
        raise ValueError("The Claude catalog must track the latest public npm release.")
    return {
        "package": "broader-vids",
        "version": version,
        "tag": f"v{version}",
        "registry": REGISTRY,
    }


def filenames(version):
    return [
        f"broader-vids-{version}.tgz",
        f"broader-vids-{version}.zip",
        f"broader_vids-{version}-py3-none-any.whl",
    ]


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "name": path.name,
        "sha256": digest.hexdigest(),
        "bytes": path.stat().st_size,
    }


def write_output(path, *, source=None, text=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".tmp", delete=False
        ) as target:
            temporary = Path(target.name)
            if source is not None:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, target)
            else:
                target.write(text.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare():
    metadata = check()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise ValueError("Install npm before preparing a release.")
    destination = ROOT / "dist"
    names = filenames(metadata["version"])
    outputs = [
        destination,
        ROOT / "python",
        ROOT / names[0],
        ROOT / "python" / names[2],
    ]
    outputs.extend(
        destination / name for name in [*names, "release.json", "SHA256SUMS"]
    )
    if any(path.resolve() != path for path in outputs):
        raise ValueError("Release outputs must not contain symbolic links.")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", "python"], cwd=ROOT, check=True
    )
    subprocess.run([npm, "pack", "--ignore-scripts"], cwd=ROOT, check=True)
    build(ROOT)
    write_output(destination / names[0], source=ROOT / names[0])
    write_output(destination / names[2], source=ROOT / "python" / names[2])
    manifest = {
        "schema": 1,
        **metadata,
        "files": [fingerprint(destination / name) for name in names],
    }
    write_output(
        destination / "release.json", text=json.dumps(manifest, indent=2) + "\n"
    )
    write_output(
        destination / "SHA256SUMS",
        text="".join(
            f"{item['sha256']}  {item['name']}\n" for item in manifest["files"]
        ),
    )
    return verify(destination)


def verify(directory):
    metadata = check()
    directory = Path(directory).resolve()
    manifest = read_json(directory / "release.json")
    if (
        set(manifest) != {"schema", "package", "version", "tag", "registry", "files"}
        or manifest.get("schema") != 1
    ):
        raise ValueError("Unsupported release manifest.")
    if any(manifest.get(key) != value for key, value in metadata.items()):
        raise ValueError("Release metadata does not match the checked source.")
    expected = filenames(metadata["version"])
    if not isinstance(manifest["files"], list) or len(manifest["files"]) != len(
        expected
    ):
        raise ValueError(
            "The release must contain exactly one npm archive, ZIP, and wheel."
        )
    for name, record in zip(expected, manifest["files"]):
        path = directory / name
        if path.is_symlink() or not path.is_file() or record != fingerprint(path):
            raise ValueError(f"Release artifact is missing or changed: {name}")
    sums = "".join(f"{item['sha256']}  {item['name']}\n" for item in manifest["files"])
    if (directory / "SHA256SUMS").read_text(encoding="utf-8") != sums:
        raise ValueError("The checksum list does not match the release manifest.")
    verify_package(directory / expected[0])
    with (
        tarfile.open(directory / expected[0]) as npm,
        zipfile.ZipFile(directory / expected[1]) as plugin,
    ):
        members = [m for m in npm.getmembers() if m.isfile()]
        zipped = plugin.namelist()
        names = [m.name.removeprefix("package/") for m in members]
        if (
            len(set(names)) != len(names)
            or len(set(zipped)) != len(zipped)
            or set(zipped) != set(names)
        ):
            raise ValueError(
                "The npm archive and plugin ZIP must contain the same files."
            )
        for member, name in zip(members, names):
            raw = npm.extractfile(member).read()
            if plugin.read(name) != raw:
                raise ValueError(f"The plugin ZIP differs from the npm archive: {name}")
            if name.endswith(".whl") and raw != (directory / expected[2]).read_bytes():
                raise ValueError("The release wheel differs from the packaged engine.")
    return {"verified": True, **metadata, "artifacts_directory": str(directory)}


def available(tag=None):
    metadata = check(tag)
    url = f"{REGISTRY}/broader-vids/{metadata['version']}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Broader-Vids-release"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20):
            pass
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code == 404:
            return {"available": True, **metadata}
        raise ValueError(
            f"The npm registry returned HTTP {code}; publication is blocked."
        ) from error
    raise ValueError(
        "This npm version already exists. Choose a new version; published versions are immutable."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Prepare and verify reviewed Broader Vids release artifacts."
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("prepare")
    for operation in ("check", "available"):
        command = commands.add_parser(operation)
        command.add_argument("--tag")
    command = commands.add_parser("set-version")
    command.add_argument("--version", required=True)
    command.add_argument("--source-sha")
    command = commands.add_parser("verify")
    command.add_argument("--artifacts-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.operation == "set-version":
        from versioning import set_version

        if (
            args.source_sha is not None
            and re.fullmatch(r"[a-f0-9]{40}", args.source_sha) is None
        ):
            raise ValueError(
                "The source commit must contain forty lowercase hexadecimal characters."
            )
        result = set_version(ROOT, args.version)
        if args.source_sha:
            package = read_json(ROOT / "package.json")
            package["broaderVidsRelease"] = {"sourceCommit": args.source_sha}
            write_output(
                ROOT / "package.json", text=json.dumps(package, indent=2) + "\n"
            )
    elif args.operation == "prepare":
        result = prepare()
    elif args.operation == "verify":
        result = verify(args.artifacts_dir)
    else:
        result = check(args.tag) if args.operation == "check" else available(args.tag)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
