import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import versioning


VERSION = "1.2.3"


def make_repo(root: Path, version: str = "0.1.0") -> dict[str, str]:
    files = {
        "package.json": json.dumps(
            {
                "name": "broader-vids",
                "version": version,
                "files": [f"python/broader_vids-{version}-*.whl", "README.md"],
                "extra": {"kept": True},
            },
            indent=2,
        )
        + "\n",
        "package-lock.json": json.dumps(
            {
                "name": "broader-vids",
                "version": version,
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "broader-vids", "version": version},
                    "node_modules/other": {"version": "8.7.6"},
                },
            },
            indent=2,
        )
        + "\n",
        "plugin.json": json.dumps(
            {"name": "broader-vids", "version": version, "custom": [1, 2]}, indent=2
        )
        + "\n",
        "pyproject.toml": f'[project]\nname = "broader-vids"\nversion = "{version}"\ndescription = "kept"\n\n[tool.other]\nversion = "7.8.9"\n',
        "uv.lock": f'version = 1\n\n[[package]]\nname = "broader-vids"\nversion = "{version}"\nsource = {{ editable = "." }}\n\n[[package]]\nname = "some-dependency"\nversion = "9.8.7"\n',
        "src/broader_vids/__init__.py": f'__version__ = "{version}"\nVALUE = "preserved"\n',
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return files


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_set_version_updates_all_version_fields_and_preserves_unrelated_content(
        self,
    ) -> None:
        before = make_repo(self.root)

        result = versioning.set_version(self.root, VERSION)

        self.assertEqual(result, {"version": VERSION, "files": list(versioning._FILES)})
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], VERSION)
        self.assertIn(f"python/broader_vids-{VERSION}-*.whl", package["files"])
        self.assertEqual(package["extra"], {"kept": True})
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], lock["packages"][""]["version"])
        self.assertEqual(lock["version"], VERSION)
        self.assertEqual(lock["packages"]["node_modules/other"]["version"], "8.7.6")
        self.assertIn(
            'version = "7.8.9"',
            (self.root / "pyproject.toml").read_text(encoding="utf-8"),
        )
        self.assertIn(
            'version = "9.8.7"', (self.root / "uv.lock").read_text(encoding="utf-8")
        )
        self.assertIn(
            'VALUE = "preserved"',
            (self.root / "src/broader_vids/__init__.py").read_text(encoding="utf-8"),
        )
        self.assertTrue(
            all(
                (self.root / name).read_text(encoding="utf-8") != before[name]
                for name in versioning._FILES
            )
        )

    def test_actual_release_manifests_update_in_a_temporary_copy(self) -> None:
        source = Path(__file__).parents[1]
        for name in versioning._FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)

        result = versioning.set_version(self.root, "0.1.1")

        self.assertEqual(result["version"], "0.1.1")
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertIn("python/broader_vids-0.1.1-*.whl", package["files"])
        self.assertNotIn("python/broader_vids-0.1.0-*.whl", package["files"])

    def test_invalid_requested_version_is_non_destructive(self) -> None:
        original = make_repo(self.root)
        for requested in ("1.2", "v1.2.3", "1.02.3", "1.2.3-alpha.1"):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                versioning.set_version(self.root, requested)
            self.assertEqual(
                {
                    name: (self.root / name).read_text(encoding="utf-8")
                    for name in original
                },
                original,
            )

    def test_mismatched_existing_versions_are_non_destructive(self) -> None:
        original = make_repo(self.root)
        plugin_path = self.root / "plugin.json"
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        plugin["version"] = "0.2.0"
        plugin_path.write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")
        original["plugin.json"] = plugin_path.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "do not agree"):
            versioning.set_version(self.root, VERSION)

        self.assertEqual(
            {name: (self.root / name).read_text(encoding="utf-8") for name in original},
            original,
        )

    def test_write_failure_rolls_back_already_replaced_files(self) -> None:
        original = make_repo(self.root)
        replace = versioning.os.replace
        calls = 0

        def fail_second_replace(source: str | Path, destination: str | Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected replace failure")
            replace(source, destination)

        with patch.object(versioning.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "injected"):
                versioning.set_version(self.root, VERSION)

        self.assertEqual(
            {name: (self.root / name).read_text(encoding="utf-8") for name in original},
            original,
        )

    def test_failed_rollback_preserves_the_original_recovery_copy(self):
        original = make_repo(self.root)
        replace = versioning.os.replace
        calls = 0

        def fail_write_and_restore(source, destination):
            nonlocal calls
            calls += 1
            if calls in (2, 3):
                raise OSError("fixture persistent write failure")
            replace(source, destination)

        with patch.object(versioning.os, "replace", side_effect=fail_write_and_restore):
            with self.assertRaisesRegex(OSError, "Recovery copies"):
                versioning.set_version(self.root, VERSION)
        backups = list(self.root.glob(".package.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            backups[0].read_text(encoding="utf-8"), original["package.json"]
        )
