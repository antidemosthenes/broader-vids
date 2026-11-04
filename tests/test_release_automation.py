import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
from string import Template
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_ci


class ReleaseAutomationTests(unittest.TestCase):
    def test_patch_allocation_and_source_retries(self):
        source = "a" * 40
        latest = {"version": "0.1.9"}
        self.assertEqual(
            release_ci.choose_version("0.1.0", latest, source),
            {"version": "0.1.10", "reused": False},
        )
        self.assertEqual(
            release_ci.choose_version("0.2.0", latest, source)["version"], "0.2.0"
        )
        latest["broaderVidsRelease"] = {"sourceCommit": source}
        self.assertTrue(release_ci.choose_version("0.1.0", latest, source)["reused"])
        for declared, public, sha in (
            ("0.2.0", latest, source),
            ("0.1.0", None, source),
            ("0.1.0", {"version": "0.0.0-stage"}, source),
            ("0.1.0", latest, "bad"),
        ):
            with (
                self.subTest(declared=declared, public=public),
                self.assertRaises(ValueError),
            ):
                release_ci.choose_version(declared, public, sha)

    def test_tagged_unpublished_versions_are_reserved_and_same_source_retries_reuse_them(
        self,
    ):
        source = "a" * 40
        for original, public, tags, expected in (
            ("b" * 40, {"version": "0.1.0"}, "v0.1.0\nv0.1.1", "0.1.2"),
            (source, {"version": "0.1.0"}, "v0.1.0\nv0.1.1", "0.1.1"),
            ("b" * 40, {"version": "0.1.0"}, "v0.1.1\nv0.1.5\nv1.0.0-beta.1", "0.1.6"),
            (
                "b" * 40,
                {"version": "0.1.1", "broaderVidsRelease": {"sourceCommit": source}},
                "v0.1.1",
                None,
            ),
        ):
            with (
                self.subTest(original=original, public=public, tags=tags),
                patch.object(release_ci, "check", return_value={"version": "0.1.1"}),
                patch.object(release_ci, "registry", return_value=public),
                patch.object(release_ci, "git_value", side_effect=[tags, "c" * 40]),
                patch.object(
                    release_ci,
                    "source_commit",
                    return_value={"source_sha": original, "recovery": True},
                ),
            ):
                if expected is None:
                    with self.assertRaisesRegex(ValueError, "immutable release tag"):
                        release_ci.plan(source)
                else:
                    result = release_ci.plan(source)
                    self.assertEqual(result["version"], expected)
                    self.assertEqual(result["reused"], original == source)

    def test_publication_jobs_survive_skipped_ancestor_but_require_success_and_no_cancellation(
        self,
    ):
        workflow = yaml.safe_load(
            (release_ci.ROOT / ".github/workflows/release.yml").read_text()
        )
        for name in ("publish", "github-release"):
            condition = workflow["jobs"][name]["if"]
            self.assertIn("!cancelled()", condition)
            for prepared, published, ready, cancelled, expected in (
                ("success", "success", "true", False, True),
                ("failure", "success", "true", False, False),
                ("skipped", "success", "true", False, False),
                ("success", "success", "false", False, False),
                ("success", "success", "true", True, False),
                ("success", "failure", "true", False, name == "publish"),
                ("success", "skipped", "true", False, name == "publish"),
            ):
                with self.subTest(
                    name=name,
                    prepared=prepared,
                    published=published,
                    ready=ready,
                    cancelled=cancelled,
                ):
                    expression = condition.replace("!cancelled()", repr(not cancelled))
                    expression = expression.replace(
                        "needs.prepare.result", repr(prepared)
                    )
                    expression = expression.replace(
                        "needs.publish.result", repr(published)
                    )
                    expression = expression.replace(
                        "needs.prepare.outputs.ready", repr(ready)
                    )
                    self.assertEqual(
                        eval(
                            expression.replace("&&", " and "), {"__builtins__": {}}, {}
                        ),
                        expected,
                    )

    def test_workflow_publishes_a_local_tarball_in_an_offline_npm_dry_run(self):
        workflow = yaml.safe_load(
            (release_ci.ROOT / ".github/workflows/release.yml").read_text()
        )
        script = next(
            step["run"]
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Verify and publish the exact archive"
        )
        assignment = next(
            line.strip()
            for line in script.splitlines()
            if line.strip().startswith("archive=")
        )
        archive = Template(shlex.split(assignment)[0].partition("=")[2]).substitute(
            RELEASE_VERSION="0.0.0"
        )
        command = next(
            line.strip()
            for line in script.splitlines()
            if line.strip().startswith("npm publish ")
        )
        arguments = shlex.split(Template(command).substitute(archive=archive))
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        self.assertIsNotNone(npm, "Release validation requires npm.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / archive
            target.parent.mkdir()
            package = json.dumps(
                {
                    "name": "broader-vids-release-fixture",
                    "version": "0.0.0",
                    "license": "MIT",
                }
            ).encode()
            with tarfile.open(target, "w:gz") as packed:
                member = tarfile.TarInfo("package/package.json")
                member.size = len(package)
                packed.addfile(member, io.BytesIO(package))
            user_config, global_config = root / "user.npmrc", root / "global.npmrc"
            user_config.touch()
            global_config.touch()
            result = subprocess.run(
                [
                    npm,
                    *arguments[1:],
                    "--dry-run",
                    "--json",
                    "--registry=http://127.0.0.1:1",
                ],
                cwd=root,
                env={
                    **os.environ,
                    "NPM_CONFIG_USERCONFIG": str(user_config),
                    "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                    "NPM_CONFIG_CACHE": str(root / "cache"),
                    "NPM_CONFIG_OFFLINE": "true",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "protocol.allow",
                    "GIT_CONFIG_VALUE_0": "never",
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            published = report.get("broader-vids-release-fixture", report)
            self.assertEqual(published["name"], "broader-vids-release-fixture")
            self.assertEqual(published["version"], "0.0.0")

    def test_published_versions_are_reused_only_when_archive_matches_and_never_downgrade_latest(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.tgz"
            archive.write_bytes(b"verified")
            existing = {"dist": {"integrity": release_ci.integrity(archive)}}
            with patch.object(release_ci, "registry", return_value=existing):
                self.assertEqual(
                    release_ci.publication("0.1.1", archive)["action"], "reuse"
                )
                archive.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "immutable"):
                    release_ci.publication("0.1.1", archive)
            for latest, succeeds in (
                ({"version": "0.1.0"}, True),
                ({"version": "0.2.0"}, False),
            ):
                with patch.object(release_ci, "registry", side_effect=[None, latest]):
                    if succeeds:
                        self.assertEqual(
                            release_ci.publication("0.1.1", archive)["action"],
                            "publish",
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, "backwards"):
                            release_ci.publication("0.1.1", archive)

    def test_registry_propagation_is_bounded_and_wrong_integrity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.tgz"
            archive.write_bytes(b"verified")
            existing = {"dist": {"integrity": release_ci.integrity(archive)}}
            clock = SimpleNamespace(value=0.0)

            def sleep(seconds):
                clock.value += seconds

            def processing(version, **kwargs):
                if clock.value < 120:
                    return None
                if version == "latest":
                    return {"version": "0.1.0" if clock.value < 140 else "0.1.1"}
                return existing

            with (
                patch.object(
                    release_ci,
                    "time",
                    SimpleNamespace(monotonic=lambda: clock.value, sleep=sleep),
                ),
                patch.object(release_ci.sys, "stderr", io.StringIO()),
            ):
                with patch.object(release_ci, "registry", side_effect=processing):
                    self.assertTrue(
                        release_ci.verify_published("0.1.1", archive)["verified"]
                    )
                    self.assertGreaterEqual(clock.value, 140)
                    self.assertLess(clock.value, 300)
                clock.value = 0
                with patch.object(release_ci, "registry", return_value=None):
                    with self.assertRaisesRegex(ValueError, "five minutes"):
                        release_ci.verify_published("0.1.1", archive)
                    self.assertEqual(clock.value, 300)
                with patch.object(
                    release_ci,
                    "registry",
                    return_value={"dist": {"integrity": "wrong"}},
                ):
                    with self.assertRaisesRegex(ValueError, "differs"):
                        release_ci.verify_published("0.1.1", archive)

    def test_github_assets_upload_once_and_reject_existing_differences(self):
        with tempfile.TemporaryDirectory() as directory:
            names = [*release_ci.filenames("0.1.1"), "release.json", "SHA256SUMS"]
            for name in names:
                (Path(directory) / name).write_bytes(name.encode())
            source = "a" * 40
            release = {
                "assets": [],
                "upload_url": "https://uploads.github.com/repos/antidemosthenes/broader-vids/releases/1/assets{?name}",
                "html_url": "https://github.com/antidemosthenes/broader-vids/releases/tag/v0.1.1",
            }
            assets = []

            def upload(request, **kwargs):
                name = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(request.full_url).query
                )["name"][0]
                asset = {
                    "name": name,
                    "size": len(request.data),
                    "digest": "sha256:" + hashlib.sha256(request.data).hexdigest(),
                }
                assets.append(asset)
                return io.BytesIO(json.dumps(asset).encode())

            with (
                patch.dict(
                    os.environ, {"GH_TOKEN": "fixture-token", "RELEASE_SHA": source}
                ),
                patch.object(release_ci, "verify_artifacts"),
                patch.object(release_ci, "registry", return_value={"version": "0.1.1"}),
            ):
                with (
                    patch.object(
                        release_ci,
                        "request_json",
                        side_effect=[{"sha": source}, None, release],
                    ),
                    patch("urllib.request.urlopen", side_effect=upload),
                ):
                    self.assertTrue(
                        release_ci.github_release("0.1.1", directory)["verified"]
                    )
                self.assertEqual(len(assets), 5)
                release["assets"] = assets
                with (
                    patch.object(
                        release_ci,
                        "request_json",
                        side_effect=[{"sha": source}, release],
                    ),
                    patch(
                        "urllib.request.urlopen",
                        side_effect=AssertionError("Unexpected repeated upload"),
                    ),
                ):
                    self.assertTrue(
                        release_ci.github_release("0.1.1", directory)["verified"]
                    )
                assets[0]["digest"] = "wrong"
                with patch.object(
                    release_ci, "request_json", side_effect=[{"sha": source}, release]
                ):
                    with self.assertRaisesRegex(ValueError, "existing GitHub"):
                        release_ci.github_release("0.1.1", directory)
                with patch.object(
                    release_ci, "request_json", return_value={"sha": "b" * 40}
                ):
                    with self.assertRaisesRegex(ValueError, "tag"):
                        release_ci.github_release("0.1.1", directory)

    def test_workflow_releases_only_successful_push_checks_on_main(self):
        workflow = yaml.safe_load(
            (release_ci.ROOT / ".github/workflows/release.yml").read_text()
        )
        trigger = workflow[True]["workflow_run"]
        self.assertEqual(trigger["branches"], ["main"])
        self.assertEqual(trigger["workflows"], ["checks"])
        self.assertEqual(trigger["types"], ["completed"])
        condition = workflow["jobs"]["resolve"]["if"]
        for guard in (
            "github.event.workflow_run.conclusion == 'success'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_branch == 'main'",
            "github.event.workflow_run.head_repository.full_name == github.repository",
            "github.ref == 'refs/heads/main'",
        ):
            self.assertIn(guard, condition)

    def test_version_commits_resolve_to_their_source_and_do_not_release_newer_main(
        self,
    ):
        source, commit, newer = "a" * 40, "b" * 40, "c" * 40
        package = {"version": "0.1.1", "broaderVidsRelease": {"sourceCommit": source}}
        with patch.object(
            release_ci, "git_value", side_effect=[json.dumps(package), commit, source]
        ):
            self.assertEqual(
                release_ci.source_commit(commit),
                {"source_sha": source, "recovery": True},
            )
        with patch.object(
            release_ci, "git_value", side_effect=[json.dumps(package), commit, newer]
        ):
            self.assertEqual(
                release_ci.source_commit(newer),
                {"source_sha": newer, "recovery": False},
            )
        with (
            patch.object(
                release_ci,
                "source_commit",
                return_value={"source_sha": source, "recovery": True},
            ),
        ):
            self.assertTrue(release_ci.readiness(source, commit)["eligible"])
        with (
            patch.object(
                release_ci,
                "source_commit",
                return_value={"source_sha": newer, "recovery": False},
            ),
        ):
            self.assertFalse(release_ci.readiness(source, newer)["eligible"])
