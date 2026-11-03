import json
import io
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from broader_vids.http import AccessPaused, BudgetExhausted, NoRedirect, PublicHttp
from broader_vids.parsing import embedded_json, walk
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
                body = io.BytesIO(b"rate limited")
                failure = urllib.error.HTTPError(
                    "https://api.bilibili.com/", 429, "fixture", {}, body
                )
                with patch("urllib.request.OpenerDirector.open", side_effect=failure):
                    with self.assertRaises(AccessPaused):
                        http.get(
                            "https://api.bilibili.com/x/web-interface/view?bvid=BV1111111111"
                        )
                self.assertTrue(body.closed)
                self.assertEqual(store.request_count(identifier), 1)
                self.assertEqual(store.requests(identifier)[0]["outcome"], "unresolved")
                with self.assertRaises(BudgetExhausted):
                    http.get(
                        "https://api.bilibili.com/x/web-interface/view?bvid=BV1111111111"
                    )
            finally:
                store.close()

    def test_redirects_pause_before_following(self):
        body = io.BytesIO(b"redirect")
        with self.assertRaises(AccessPaused):
            NoRedirect().redirect_request(
                None, body, 302, "redirect", {}, "https://example.com/"
            )
        self.assertTrue(body.closed)
        with self.assertRaises(AccessPaused):
            PublicHttp.decode_json('{"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}")

    def test_embedded_json_preserves_escaped_braces_and_rejects_missing_data(self):
        value = {"title": 'A {title} "example"'}
        body = "var ytInitialData = " + json.dumps(value) + "; other();"
        self.assertEqual(embedded_json(body, r"ytInitialData\s*=\s*"), value)
        with self.assertRaises(AccessPaused):
            embedded_json("unrecognized page", r"ytInitialData\s*=\s*")

    def test_deeply_nested_embedded_metadata_fails_cleanly(self):
        with self.assertRaises(AccessPaused):
            embedded_json(
                'var data = {"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}",
                r"data\s*=\s*",
            )

    def test_metadata_walk_preserves_preorder_without_recursion(self):
        leaf = {"leaf": True}
        nested = leaf
        for _ in range(1200):
            nested = [nested]
        root = {"deep": nested, "last": {"last": True}}
        self.assertEqual(list(walk(root)), [root, leaf, root["last"]])
