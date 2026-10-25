import gzip
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepvids.catalogs import SOURCES, fetch_catalog, get_catalog, import_catalog, list_catalogs, sample_catalog
from deepvids.reports import release_gate
from deepvids.storage import Store


def bili_id(number):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    value = ""
    while number:
        number, digit = divmod(number, len(alphabet))
        value = alphabet[digit] + value
    return "BV1" + value.rjust(9, "1")


def chinaopen_rows(count):
    return "".join(f"{bili_id(i)}\thttps://www.bilibili.com/video/{bili_id(i)}\n" for i in range(count))


def youtube_rows(count):
    return "".join(json.dumps({"display_id": f"{i:010d}A", "title": "测试 title", "duration": 5500 + i,
                                "view_count": i, "description": "Fixture"}, ensure_ascii=False) + "\n" for i in range(count))


class Reply(io.BytesIO):
    def __init__(self, body, url, status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}

    def geturl(self):
        return self.url


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "state")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def file(self, name, contents):
        target = self.root / name
        if name.endswith(".gz"):
            target.write_bytes(gzip.compress(contents.encode("utf-8")))
        else:
            target.write_text(contents, encoding="utf-8")
        return target

    def test_deduplicates_before_uniform_draw_and_preserves_raw_duration(self):
        data = youtube_rows(120)
        path = self.file("youtube.gz", data + data.splitlines()[0] + "\ninvalid\n")
        catalog = import_catalog(self.store, "youniverse", path)
        self.assertEqual(catalog["item_count"], 120)
        self.assertEqual(catalog["manifest"]["duplicate_rows"], 1)
        self.assertEqual(catalog["manifest"]["invalid_rows"], 1)
        first = sample_catalog(self.store, catalog["id"], 100, 73)
        second = sample_catalog(self.store, catalog["id"], 100, 73)
        self.assertEqual(first["items"], second["items"])
        self.assertEqual(len({item["video_id"] for item in first["items"]}), 100)
        self.assertTrue(all(item["duration"] > 5400 for item in first["items"]))
        self.assertTrue(Path(first["artifact_path"]).is_file())
        self.assertEqual(first["scope"], "local_file")
        self.assertFalse(release_gate(self.store)["phase_one_complete"])

    def test_draw_can_reach_every_catalog_entry(self):
        catalog = import_catalog(self.store, "chinaopen", self.file("bili.txt", chinaopen_rows(120)))
        draw = sample_catalog(self.store, catalog["id"], 120, 1)
        self.assertEqual({item["video_id"] for item in draw["items"]}, {bili_id(i) for i in range(120)})
        self.assertTrue(all(item["part_index"] == 1 for item in draw["items"]))
        for size in (0, 121, True):
            with self.assertRaises(ValueError):
                sample_catalog(self.store, catalog["id"], size)

    def test_rejects_mismatched_bili_link_and_keeps_explicit_parts(self):
        name = bili_id(0)
        data = f"{name}\thttps://www.bilibili.com/video/{name}?p=2\n"
        data += f"{name}\thttps://www.bilibili.com/video/{bili_id(1)}\n"
        catalog = import_catalog(self.store, "chinaopen", self.file("bili.txt", data))
        self.assertEqual(catalog["item_count"], 1)
        self.assertEqual(catalog["manifest"]["invalid_rows"], 1)
        self.assertEqual(sample_catalog(self.store, catalog["id"], 1)["items"][0]["part_index"], 2)

    def test_prefix_limit_is_always_labeled_a_preview(self):
        catalog = import_catalog(self.store, "youniverse", self.file("youtube.gz", youtube_rows(10)), row_limit=3)
        self.assertEqual(catalog["scope"], "prefix_preview")
        self.assertEqual(catalog["item_count"], 3)
        self.assertFalse(catalog["manifest"]["reached_eof"])

    def test_truncated_full_import_rolls_back_instead_of_publishing(self):
        path = self.file("broken.gz", youtube_rows(120))
        path.write_bytes(path.read_bytes()[:-8])
        with self.assertRaises(EOFError):
            import_catalog(self.store, "youniverse", path)
        catalog = list_catalogs(self.store)[0]
        self.assertEqual(catalog["status"], "failed")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            sample_catalog(self.store, catalog["id"], 1)

    def test_complete_source_requires_identity_evidence(self):
        with self.assertRaises(ValueError):
            import_catalog(self.store, "youniverse", self.file("youtube.gz", youtube_rows(10)), "source_snapshot")
        with self.assertRaises(ValueError):
            import_catalog(self.store, "chinaopen", self.file("bili.txt", chinaopen_rows(10)), "source_snapshot")

    def test_rejected_historical_sources_cannot_complete_phase_one(self):
        for name, body in (("chinaopen", chinaopen_rows(120).encode()), ("youniverse", gzip.compress(youtube_rows(120).encode()))):
            with patch("urllib.request.OpenerDirector.open", return_value=Reply(body, SOURCES[name].url)):
                catalog = fetch_catalog(self.store, name, preview_rows=100)
            sample_catalog(self.store, catalog["id"], 100, 1)
        gate = release_gate(self.store)
        self.assertFalse(gate["phase_one_complete"])
        self.assertEqual(gate["current_phase"], 1)
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["phases"][1]["status"], "not_started")
        for platform in ("youtube", "bilibili"):
            self.assertFalse(gate["platforms"][platform]["candidate_discovery_ready"])
            self.assertEqual(len(gate["platforms"][platform]["ineligible_catalogs"]), 1)
        with patch.dict(SOURCES, {name: replace(source, discovery_eligible=True) for name, source in SOURCES.items()}):
            self.assertFalse(release_gate(self.store)["phase_one_complete"])

    def test_download_cap_is_enforced_when_server_ignores_range(self):
        body = gzip.compress(youtube_rows(200).encode()) + b"x" * 10000
        with patch("urllib.request.OpenerDirector.open", return_value=Reply(body, SOURCES["youniverse"].url)):
            catalog = fetch_catalog(self.store, "youniverse", max_bytes=1024, preview_rows=100)
        self.assertEqual(catalog["manifest"]["receipt"]["downloaded_bytes"], 1024)
        self.assertFalse(catalog["manifest"]["receipt"]["resource_complete"])
        self.assertEqual(catalog["scope"], "prefix_preview")

    def test_nonzero_range_cannot_be_imported_as_the_file_prefix(self):
        body = gzip.compress(youtube_rows(100).encode())
        response = Reply(body, SOURCES["youniverse"].url, 206, {"Content-Range": "bytes 10-999/10000"})
        with patch("urllib.request.OpenerDirector.open", return_value=response):
            with self.assertRaises(ValueError):
                fetch_catalog(self.store, "youniverse")
        self.assertEqual(list_catalogs(self.store), [])

    def test_migration_preserves_previous_experiment_state(self):
        run_id = self.store.create("youtube", {"kind": "probe"}, {"pending": "fixture"})
        self.store.db.execute("PRAGMA user_version=1")
        self.store.db.commit()
        self.store.close()
        self.store = Store(self.root / "state")
        self.assertEqual(self.store.get(run_id)["state"], {"pending": "fixture"})
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
