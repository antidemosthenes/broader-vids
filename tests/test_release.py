import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_release_tag_and_versions_must_agree(self):
        version = release.check()["version"]
        self.assertEqual(release.check(f"v{version}")["version"], version)
        for tag in ("v99.0.0", "main", "v0.1.0; echo invalid"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check(tag)

    def test_changed_artifact_is_rejected_before_package_inspection(self):
        metadata = release.check()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for name in release.filenames(metadata["version"]):
                path = root / name
                path.write_bytes(b"original")
                records.append(release.fingerprint(path))
            (root / "release.json").write_text(
                json.dumps({"schema": 1, **metadata, "files": records}),
                encoding="utf-8",
            )
            (root / records[0]["name"]).write_bytes(b"changed")
            with patch.object(release, "verify_package") as inspect:
                with self.assertRaisesRegex(ValueError, "missing or changed"):
                    release.verify(root)
                inspect.assert_not_called()
