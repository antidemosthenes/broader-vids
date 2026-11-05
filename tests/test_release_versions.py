import json
import os
import shlex
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

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
        ".claude-plugin/plugin.json": json.dumps(
            {"name": "broader-vids", "version": version}, indent=2
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
        self.assertEqual(
            json.loads(
                (self.root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
            )["version"],
            VERSION,
        )
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

        original = json.loads((self.root / "package.json").read_text(encoding="utf-8"))[
            "version"
        ]
        major, minor, patch = map(int, original.split("."))
        target_version = f"{major}.{minor}.{patch + 1}"
        result = versioning.set_version(self.root, target_version)

        self.assertEqual(result["version"], target_version)
        self.assertEqual(result["files"], list(versioning._FILES))
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertIn(f"python/broader_vids-{target_version}-*.whl", package["files"])
        self.assertNotIn(f"python/broader_vids-{original}-*.whl", package["files"])

    def test_workflow_commits_every_versioned_manifest(self) -> None:
        source = Path(__file__).parents[1]
        make_repo(self.root, "0.1.5")
        script = self.root / "scripts/versioning.py"
        script.parent.mkdir()
        shutil.copyfile(source / "scripts/versioning.py", script)
        environment = {
            **os.environ,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.safecrlf",
            "GIT_CONFIG_VALUE_1": "false",
        }

        def run(*command):
            return subprocess.run(
                command,
                cwd=self.root,
                env=environment,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=15,
            )

        def commit(message):
            run(
                "git",
                "-c",
                "user.name=Release fixture",
                "-c",
                "user.email=release@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                f"core.hooksPath={self.root / 'empty-hooks'}",
                "commit",
                "-qm",
                message,
            )

        run("git", "init", "-q")
        run("git", "add", ".")
        commit("Initial fixture")
        versioning.set_version(self.root, "0.1.6")
        workflow = yaml.safe_load(
            (source / ".github/workflows/release.yml").read_text(encoding="utf-8")
        )
        step = next(
            item
            for item in workflow["jobs"]["prepare"]["steps"]
            if item.get("name") == "Build a versioned copy of the tested source"
        )
        stage = next(
            line.strip()
            for line in step["run"].splitlines()
            if line.strip().startswith("git add ")
            or "from scripts.versioning import _FILES" in line
        )
        arguments = shlex.split(stage)
        if arguments[0] == "uv":
            arguments = [sys.executable, *arguments[4:]]
        run(*arguments)
        run("git", "diff", "--exit-code")
        staged = set(run("git", "diff", "--cached", "--name-only").stdout.splitlines())
        self.assertEqual(staged, set(versioning._FILES))
        commit("Release fixture")
        for name in versioning._FILES:
            self.assertEqual(
                run("git", "show", f"HEAD:{name}").stdout,
                (self.root / name).read_text(encoding="utf-8"),
            )
        self.assertEqual(
            json.loads(run("git", "show", "HEAD:.claude-plugin/plugin.json").stdout)[
                "version"
            ],
            "0.1.6",
        )

    def test_npm_excludes_claude_version_recovery_files(self) -> None:
        source = Path(__file__).parents[1]
        metadata = json.loads((source / "package.json").read_text(encoding="utf-8"))
        files = [
            name for name in metadata["files"] if name.startswith(".claude-plugin")
        ]
        (self.root / "package.json").write_text(
            json.dumps(
                {
                    "name": "broader-vids-release-fixture",
                    "version": "0.0.0",
                    "files": files,
                }
            ),
            encoding="utf-8",
        )
        directory = self.root / ".claude-plugin"
        directory.mkdir()
        for name in (
            "plugin.json",
            "marketplace.json",
            ".plugin.json.fixture.bak",
            ".plugin.json.fixture.tmp",
        ):
            (directory / name).write_text("{}", encoding="utf-8")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        self.assertIsNotNone(npm, "Release validation requires npm.")
        result = subprocess.run(
            [
                npm,
                "pack",
                "--dry-run",
                "--ignore-scripts",
                "--json",
                "--offline",
                "--cache",
                str(self.root / "cache"),
            ],
            cwd=self.root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
        )
        packed = {item["path"] for item in json.loads(result.stdout)[0]["files"]}
        self.assertEqual(
            packed,
            {
                "package.json",
                ".claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
            },
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
