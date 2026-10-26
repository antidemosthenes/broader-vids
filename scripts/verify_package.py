import io
import json
from pathlib import Path
import re
import sys
import tarfile
from urllib.parse import unquote
import zipfile


def main(archive_path):
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    expected_python = manifest["version"].replace("-alpha.", "a")
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        private = {
            ".localdata",
            ".cache",
            ".venv",
            ".git",
            "node_modules",
            "PHASE3_HANDOVER.md",
            ".env",
        }
        assert not any(private.intersection(Path(name).parts) for name in names), (
            "Private file in package"
        )
        wheel_names = [name for name in names if name.endswith(".whl")]
        assert (
            len(wheel_names) == 1 and f"deepvids-{expected_python}-" in wheel_names[0]
        )
        for name in names:
            item = archive.getmember(name)
            if item.isfile() and not name.endswith(".whl"):
                source = root / Path(name).relative_to("package")
                assert source.resolve().is_relative_to(root), name
                assert (
                    source.is_file()
                    and archive.extractfile(item).read() == source.read_bytes()
                ), name
        with zipfile.ZipFile(
            io.BytesIO(archive.extractfile(wheel_names[0]).read())
        ) as wheel:
            packaged = set(wheel.namelist())
            assert not any(
                "__pycache__" in name or name.endswith(".pyc") for name in packaged
            )
            for source in (root / "src/deepvids").rglob("*"):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                name = source.relative_to(root / "src").as_posix()
                assert name in packaged and wheel.read(name) == source.read_bytes(), (
                    name
                )
            for retired in (
                "catalogs",
                "sampling",
                "experiments",
                "diagnostics",
                "audio",
                "audio_worker",
            ):
                assert f"deepvids/{retired}.py" not in packaged
        documents = [
            root / "README.md",
            root / "skills/deepvids/SKILL.md",
            *(root / "docs").rglob("*.md"),
        ]
        for source in documents:
            for link in re.findall(
                r"\]\(([^)]+)\)", source.read_text(encoding="utf-8")
            ):
                if ":" in link or link.startswith("#"):
                    continue
                target = (source.parent / unquote(link.split("#")[0])).resolve()
                assert target.is_file(), f"Broken link in {source.name}: {link}"
                if target.is_relative_to(root):
                    assert "package/" + target.relative_to(root).as_posix() in names, (
                        f"Unshipped linked file: {link}"
                    )
        for name in ("plugin.json", ".claude-plugin/marketplace.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            version = (
                data["plugins"][0]["version"] if "plugins" in data else data["version"]
            )
            assert version == manifest["version"]
    print(
        f"Verified {Path(archive_path).name}: current source, assets, documentation links, versions and private-state exclusions."
    )


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("Usage: uv run scripts/verify_package.py [ARCHIVE.tgz]")
    root = Path(__file__).resolve().parent.parent
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    main(sys.argv[1] if len(sys.argv) == 2 else root / f"deepvids-{version}.tgz")
