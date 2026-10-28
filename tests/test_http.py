import json
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from broader_vids.http import AccessPaused, BudgetExhausted, NoRedirect, PublicHttp
from broader_vids.parsing import embedded_json
from broader_vids.storage import Store


class HttpTests(unittest.TestCase):
    def test_throttling_is_unresolved_and_consumes_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                identifier = store.create(
                    "bilibili",
                    {"kind": "discovery", "max_seconds": 60, "max_requests": 1},
                    {},
                )
                http = PublicHttp(store, store.get(identifier))
                failure = urllib.error.HTTPError(
                    "https://api.bilibili.com/", 429, "fixture", {}, None
                )
                with patch("urllib.request.OpenerDirector.open", side_effect=failure):
                    with self.assertRaises(AccessPaused):
                        http.get(
                            "https://api.bilibili.com/x/web-interface/view?bvid=BV1111111111"
                        )
                self.assertEqual(store.request_count(identifier), 1)
                self.assertEqual(store.requests(identifier)[0]["outcome"], "unresolved")
                with self.assertRaises(BudgetExhausted):
                    http.get(
                        "https://api.bilibili.com/x/web-interface/view?bvid=BV1111111111"
                    )
            finally:
                store.close()

    def test_redirects_pause_before_following(self):
        with self.assertRaises(AccessPaused):
            NoRedirect().redirect_request(
                None, None, 302, "redirect", {}, "https://example.com/"
            )

    def test_embedded_json_preserves_escaped_braces_and_rejects_missing_data(self):
        value = {"title": 'A {title} "example"'}
        body = "var ytInitialData = " + json.dumps(value) + "; other();"
        self.assertEqual(embedded_json(body, r"ytInitialData\s*=\s*"), value)
        with self.assertRaises(AccessPaused):
            embedded_json("unrecognized page", r"ytInitialData\s*=\s*")
