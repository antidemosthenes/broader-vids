from collections import Counter
import hashlib
import json
import tempfile
import unittest

from broader_vids.discovery.service import (
    create_discovery,
    discovery_checks,
    discovery_report,
    execute_discovery,
    make_shortlist,
    merge_page,
    write_discovery_report,
)
from broader_vids.discovery.sources import (
    bilibili_cards,
    duration_seconds,
    youtube_page,
)
from broader_vids.http import AccessPaused, BudgetExhausted
from broader_vids.discovery.state import pinia_state
from broader_vids.storage import Store


def bvid(number):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    result = ""
    while number:
        number, digit = divmod(number, len(alphabet))
        result = alphabet[digit] + result
    return "BV1" + result.rjust(9, "1")


def video(number, creator=None):
    return {
        "videoRenderer": {
            "videoId": f"{number:010d}A",
            "title": {"simpleText": f"Topic explanation {number}"},
            "lengthText": {"simpleText": "15:00"},
            "publishedTimeText": {"simpleText": "5 years ago"},
            "ownerText": {
                "runs": [
                    {
                        "text": f"Creator{creator or number % 40 + 1}",
                        "navigationEndpoint": {
                            "browseEndpoint": {
                                "browseId": f"UC{creator or number % 40 + 1:022d}"
                            }
                        },
                    }
                ]
            },
        }
    }


def card(number, creator=None, duration="15:00"):
    owner = creator or number % 40 + 1
    return f'<div class="bili-video-card"><a href="https://www.bilibili.com/video/{bvid(number)}"></a><h3 class="bili-video-card__info--tit" title="Topic explanation {number}"></h3><span class="bili-video-card__stats__duration">{duration}</span><a href="//space.bilibili.com/{owner}" class="bili-video-card__info--owner"><span class="bili-video-card__info--author">Creator{owner}</span><span class="bili-video-card__info--date">2020-01-01</span></a></div>'


class FixtureHttp:
    fail_once = False

    def __init__(self, store, run, **kwargs):
        self.store, self.run = store, run
        self.last_receipt = None

    def elapsed(self):
        return self.store.request_count(self.run["id"])

    def check(self):
        if (
            self.store.request_count(self.run["id"])
            >= self.run["settings"]["max_requests"]
        ):
            raise BudgetExhausted("Fixture budget exhausted.")

    def record(self, url, value):
        self.check()
        request_id = self.store.begin_request(self.run["id"], url)
        if type(self).fail_once and self.run["state"]["index"] == 2:
            type(self).fail_once = False
            self.store.end_request(request_id, "unresolved", "Fixture interruption")
            raise AccessPaused("Fixture interruption")
        self.last_receipt = {
            "request_id": request_id,
            "url": url,
            "sha256": hashlib.sha256(str(value).encode()).hexdigest(),
            "bytes": len(str(value)),
            "transport": "anonymous_http",
        }
        self.store.end_request(request_id, "ok", json.dumps(self.last_receipt))
        return value

    def get(self, url):
        if "youtube.com/channel/" in url:
            creator = int(url.split("/channel/UC")[1].split("/")[0])
            value = {
                "contents": [
                    video(10000 + creator * 20 + i, creator) for i in range(12)
                ]
            }
            return self.record(url, "ytInitialData = " + json.dumps(value) + ";")
        if "youtube.com/?" in url:
            return self.record(url, '"INNERTUBE_CLIENT_VERSION":"fixture-version"')
        if "search.bilibili.com" in url:
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(url).query)["keyword"][0]
            if query.startswith("Creator"):
                creator = int(query[7:])
                return self.record(
                    url,
                    "".join(card(20000 + creator * 20 + i, creator) for i in range(10))
                    + card(99999, 999),
                )
            offset = self.run["state"]["index"] * 20 + 100
            return self.record(url, "".join(card(offset + i) for i in range(16)))
        return self.record(url, "".join(card(i + 1) for i in range(10)))

    def post_json(self, url, payload):
        offset = self.run["state"]["index"] * 20 + 100
        return self.record(url, {"contents": [video(offset + i) for i in range(16)]})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_both_platforms_complete_only_with_live_source_receipts(self):
        for platform in ("youtube", "bilibili"):
            identifier = create_discovery(
                self.store,
                platform,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = execute_discovery(self.store, identifier, FixtureHttp)
            self.assertEqual(run["status"], "completed")
            report = discovery_report(self.store, identifier)
            self.assertEqual(len(report["shortlist"]), 50)
            self.assertGreaterEqual(report["candidate_count"], 100)
            counts = Counter(item["creator_id"] for item in report["shortlist"])
            self.assertLessEqual(max(counts.values()), 3)
            target = write_discovery_report(self.store, identifier)
            self.assertTrue(target.is_file())
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8"))["run_id"], identifier
            )
        for platform in ("youtube", "bilibili"):
            for _ in range(2):
                execute_discovery(
                    self.store,
                    create_discovery(
                        self.store,
                        platform,
                        assessment_model="fixture-model",
                        reasoning_effort="low",
                    ),
                    FixtureHttp,
                )
        self.assertGreater(
            self.store.db.execute("SELECT COUNT(*) FROM discovery_registry").fetchone()[
                0
            ],
            100,
        )

    def test_forged_receipts_cannot_pass_discovery(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = execute_discovery(self.store, identifier, FixtureHttp)
        for item in run["state"]["candidates"].values():
            for origin in item["origins"]:
                origin["receipt"]["sha256"] = "unverified"
        self.assertFalse(
            discovery_checks(self.store, run)["at_least_100_observed_candidates"]
        )

    def test_interrupt_resume_preserves_query_and_avoids_duplicates(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        FixtureHttp.fail_once = True
        partial = execute_discovery(self.store, identifier, FixtureHttp)
        self.assertEqual(partial["status"], "paused")
        self.assertEqual(partial["state"]["index"], 2)
        completed = execute_discovery(self.store, identifier, FixtureHttp)
        self.assertEqual(completed["status"], "completed")
        baseline_id = create_discovery(
            self.store,
            "youtube",
            round_number=0,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        baseline = execute_discovery(self.store, baseline_id, FixtureHttp)
        self.assertEqual(
            set(completed["state"]["candidates"]), set(baseline["state"]["candidates"])
        )
        self.assertEqual(
            completed["state"]["shortlist"], baseline["state"]["shortlist"]
        )
        self.assertEqual(
            self.store.request_count(identifier),
            self.store.request_count(baseline_id) + 1,
        )

    def test_exhausted_budget_does_not_reset_on_continue(self):
        identifier = create_discovery(
            self.store,
            "bilibili",
            max_requests=2,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        self.assertEqual(
            execute_discovery(self.store, identifier, FixtureHttp)["status"],
            "budget_exhausted",
        )
        self.assertEqual(
            execute_discovery(self.store, identifier, FixtureHttp)["status"],
            "budget_exhausted",
        )
        self.assertEqual(self.store.request_count(identifier), 2)

    def test_niche_result_is_not_buried_by_broad_rank_or_duplicates(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        broad, _ = youtube_page({"contents": [video(i) for i in range(120)]})
        merge_page(run, broad, "search", {"family_id": "broad", "intent": "deep"}, {})
        narrow, _ = youtube_page({"contents": [video(9999)]})
        merge_page(
            run, narrow, "search", {"family_id": "niche", "intent": "intersection"}, {}
        )
        for _ in range(5):
            merge_page(
                run, broad, "search", {"family_id": "broad", "intent": "deep"}, {}
            )
        shortlist = make_shortlist(run)
        self.assertIn("youtube:0000009999A", [s["canonical_id"] for s in shortlist])
        self.assertEqual(len(run["state"]["candidates"]), 41)

    def test_known_long_youtube_is_excluded_but_bilibili_parts_remain_unknown(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        raw = video(1)
        raw["videoRenderer"]["lengthText"]["simpleText"] = "1:30:01"
        items, _ = youtube_page({"contents": [raw]})
        merge_page(run, items, "search", {}, {})
        self.assertEqual(make_shortlist(run), [])
        identifier = create_discovery(
            self.store,
            "bilibili",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        merge_page(run, bilibili_cards(card(1, duration="2:00:00")), "home", {}, {})
        self.assertEqual(len(make_shortlist(run)), 1)

    def test_new_youtube_lockup_and_continuation(self):
        value = {
            "contents": [
                {
                    "lockupViewModel": {
                        "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                        "contentId": "0000000001A",
                        "metadata": {
                            "lockupMetadataViewModel": {
                                "title": {"content": "Niche explanation"}
                            }
                        },
                        "contentImage": {
                            "thumbnailBadgeViewModel": {"text": "1:30:00"}
                        },
                    }
                },
                {
                    "continuationEndpoint": {
                        "continuationCommand": {"token": "page-two"}
                    }
                },
            ]
        }
        items, continuation = youtube_page(value, "UC0000000000000000000001")
        self.assertEqual(items[0]["duration_seconds"], 5400)
        self.assertEqual(continuation, "page-two")
        self.assertIsNone(duration_seconds("LIVE"))
        self.assertIsNone(duration_seconds("1:75"))

    def test_public_serialized_data_parses_aliases_without_executing_javascript(self):
        script = (
            '<script>window.__pinia=(function(a,b){return {searchResponse:{searchAllResponse:{result:[{bvid:a,mid:3,author:"Creator3",title:"A &amp; B",duration:"2:07",pubdate:1}]}},optional:b}}("'
            + bvid(1)
            + '",void 0))</script>'
        )
        self.assertEqual(pinia_state(script)["optional"], None)
        self.assertEqual(bilibili_cards(script, "3")[0]["duration_seconds"], 127)
        for malicious in (
            script.replace("void 0", 'alert("injected")'),
            script.replace("void 0", "a"),
            script.replace("return {", "return fetch("),
        ):
            with self.assertRaises(AccessPaused):
                pinia_state(malicious)


if __name__ == "__main__":
    unittest.main()
