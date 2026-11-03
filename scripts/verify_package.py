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
    from broader_vids.learning.presets import starter_preferences

    starter_preferences()
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        private = {
            ".localdata",
            ".cache",
            ".venv",
            ".git",
            "node_modules",
            "PHASE3_HANDOVER.md",
            "PROJECT_PLAN.md",
            "PLAN.md",
            "PHASES.md",
            "AUDIT.md",
            "my-plan.md",
            ".test-tmp",
            "research",
            "validation",
            ".env",
        }
        assert not any(private.intersection(Path(name).parts) for name in names), (
            "Private file in package"
        )
        for required in ("bin/broader-vids.cjs", "bin/plugin-install.cjs"):
            assert f"package/{required}" in names, (
                f"Missing plugin installer: {required}"
            )
        for group in ("core", "evidence", "asr"):
            required = f"package/python/{group}-requirements.txt"
            assert required in names, (
                f"Missing runtime dependency file: {required}. "
                "Include it in the source checkout and npm package."
            )
        wheel_names = [name for name in names if name.endswith(".whl")]
        assert (
            len(wheel_names) == 1
            and f"broader_vids-{expected_python}-" in wheel_names[0]
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
            for source in (root / "src/broader_vids").rglob("*"):
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
                "readiness",
                "learning/migration",
                "recommendations/legacy",
            ):
                assert f"broader_vids/{retired}.py" not in packaged
        documents = [
            root / "README.md",
            *(root / "skills").rglob("*.md"),
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
        plugin = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        assert plugin["version"] == manifest["version"]
        onboarding = plugin["extensions"]["com.openai"]["onboardingSkill"]
        assert "package/" + onboarding.removeprefix("./") in names
        catalog = json.loads(
            (root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        entry = next(
            item for item in catalog["plugins"] if item["name"] == plugin["name"]
        )
        assert catalog["name"] == "broader-vids"
        assert entry["source"] == {
            "source": "npm",
            "package": "broader-vids",
            "version": manifest["version"],
            "registry": "https://registry.npmjs.org",
        }
        assert entry["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_USE",
        }
        assert not any(".claude-plugin/" in name for name in names)
    print(
        f"Verified {Path(archive_path).name}: current source, assets, documentation links, versions and private-state exclusions."
    )


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("Usage: uv run scripts/verify_package.py [ARCHIVE.tgz]")
    root = Path(__file__).resolve().parent.parent
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    main(sys.argv[1] if len(sys.argv) == 2 else root / f"broader-vids-{version}.tgz")
