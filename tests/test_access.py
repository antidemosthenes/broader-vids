import json
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from deepvids.experiments import create_probe
from deepvids.http import AccessPaused, BudgetExhausted, NoRedirect, PublicHttp
from deepvids.models import Outcome
from deepvids.providers import Bilibili, YouTube, embedded_json
from deepvids.storage import Store


class AccessTests(unittest.TestCase):
    def test_throttling_is_unresolved_and_consumes_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                run_id = create_probe(store, "bilibili", 1)
                http = PublicHttp(store, store.get(run_id))
                failure = urllib.error.HTTPError("https://api.bilibili.com/", 429, "fixture", {}, None)
                with patch("urllib.request.OpenerDirector.open", side_effect=failure):
                    with self.assertRaises(AccessPaused):
                        http.get("https://api.bilibili.com/x/web-interface/view?aid=1")
                self.assertEqual(store.request_count(run_id), 1)
                self.assertEqual(store.requests(run_id)[0]["outcome"], "unresolved")
                with self.assertRaises(BudgetExhausted):
                    http.get("https://api.bilibili.com/x/web-interface/view?aid=1")
            finally:
                store.close()

    def test_redirects_pause_before_following(self):
        with self.assertRaises(AccessPaused):
            NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com/")

    def test_bili_unknown_and_missing_responses_are_distinct(self):
        self.assertEqual(Bilibili.parse_archive(1, {"code": -404}).outcome, Outcome.UNAVAILABLE)
        for code in (-412, -403, -400, 123, None):
            self.assertEqual(Bilibili.parse_archive(1, {"code": code}).outcome, Outcome.UNRESOLVED)
        self.assertEqual(Bilibili.parse_archive(1, {"code": 0, "data": {"aid": 2, "pages": []}}).outcome, Outcome.UNRESOLVED)

    def test_multipart_metadata_keeps_long_parts_in_raw_population(self):
        response = {"code": 0, "data": {"aid": 1, "title": "Fixture", "pages": [
            {"cid": 10, "part": "Part 1", "duration": 5400},
            {"cid": 20, "part": "Part 2", "duration": 5401}]}}
        resolution = Bilibili.parse_archive(1, response)
        self.assertEqual(resolution.outcome, Outcome.VALID)
        self.assertEqual(len(resolution.archive.parts), 2)
        self.assertEqual(resolution.archive.parts[1].key, "bilibili:1:20")
        self.assertEqual(resolution.archive.access_evidence, "metadata_only")

    def test_youtube_player_status_and_escaped_braces(self):
        video_id = "AbcDE-aaaaA"
        value = {"playabilityStatus": {"status": "OK"}, "videoDetails": {
            "videoId": video_id, "title": 'A {title} "example"', "lengthSeconds": "5400"}}
        body = "var ytInitialPlayerResponse = " + json.dumps(value) + "; other();"
        parsed = embedded_json(body, r"ytInitialPlayerResponse\s*=\s*")
        self.assertEqual(YouTube.parse_player(video_id, parsed).archive.parts[0].duration_seconds, 5400)
        self.assertEqual(YouTube.parse_player(video_id, {"playabilityStatus": {"status": "ERROR"}}).outcome, Outcome.UNRESOLVED)
        self.assertEqual(YouTube.parse_player(video_id, {"playabilityStatus": {"status": "LOGIN_REQUIRED"}}).outcome, Outcome.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()

