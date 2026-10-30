from collections import Counter
from contextlib import closing
import tempfile
import unittest

from broader_vids.exploration.domains import (
    nodes,
    put_node,
    seed_interests,
    select_existing,
)
from broader_vids.policy import Policy
from broader_vids.preferences import get_profile, set_profile
from broader_vids.recommendations.diversity import query_keys, same_topic, signature
from broader_vids.recommendations.ranking import rank_slate, screen
from broader_vids.storage import Store
from test_recommendations import candidate, quality


def video(index, query, primary=None, *, title=None):
    item = candidate(index)
    item.update(
        creator_id=str(index),
        title=title or f"Video {index}",
        origins=[{"query": query, "domains": ["mathematics"]}] if query else [],
        engagement_score={"score": 100 - index},
    )
    observation = quality(str(index))
    observation["topics"] = {
        "specific": [primary] if primary else [],
        "broad": ["mathematics"],
        "confidence": "high",
    }
    return item, observation


def rank(records, version=4):
    return rank_slate(
        [item for item, _ in records],
        {item["canonical_id"]: observation for item, observation in records},
        {"interests": ["mathematics"]},
        {key: float(key == "engagement") for key in Policy().weights},
        policy_version=version,
    )


class TopicDiversityTests(unittest.TestCase):
    def test_inspection_covers_available_queries_before_repeating_them(self):
        items = [candidate(index + 1) for index in range(98)]
        for index, item in enumerate(items):
            item["origins"] = [
                {"query": f"Search topic {index // 7}", "domains": ["mathematics"]}
            ]
        selected, _ = screen(
            items, [], {}, {"interests": ["mathematics"]}, policy_version=4
        )
        counts = Counter(next(iter(query_keys(item))) for item in selected)
        self.assertEqual(len(selected), 35)
        self.assertEqual(len(counts), 14)
        self.assertEqual(set(counts.values()), {2, 3})
        old, _ = screen(items, [], {}, {"interests": ["mathematics"]}, policy_version=3)
        self.assertLess(len({next(iter(query_keys(item))) for item in old}), 14)

    def test_repeated_queries_do_not_displace_distinct_assessed_topics(self):
        records = [
            video(i, "mapo tofu recipe", f"Recipe variation {i}") for i in range(1, 11)
        ]
        records += [
            video(i, f"Other query {i}", f"Other topic {i}") for i in range(11, 20)
        ]
        result = rank(records)
        self.assertEqual(len(result), 10)
        self.assertEqual(
            sum("query:mapo tofu recipe" in query_keys(item) for item in result), 1
        )
        self.assertFalse(any(item["selection_diversity"]["relaxed"] for item in result))
        old = rank(records, version=3)
        self.assertTrue(
            all("query:mapo tofu recipe" in query_keys(item) for item in old)
        )
        self.assertNotIn("selection_diversity", old[0])

    def test_primary_topic_variants_match_across_queries_without_capping_broad_domains(
        self,
    ):
        records = [
            video(1, "first recipe", "Mapo tofu"),
            video(2, "vegan food", "Vegan mapo tofu"),
            video(3, "family cooking", "Cantonese-style mapo tofu"),
            video(4, "graphs", "Dijkstra’s algorithm"),
            video(5, "paths", "Dijkstra algorithm"),
        ]
        records += [
            video(i, f"Query {i}", f"Distinct subject {i}") for i in range(6, 14)
        ]
        result = rank(records)
        keys = {item["canonical_id"] for item in result}
        self.assertEqual(
            len(keys & {records[i][0]["canonical_id"] for i in range(3)}), 1
        )
        self.assertEqual(len(keys & {records[i][0]["canonical_id"] for i in (3, 4)}), 1)
        self.assertEqual(len(result), 10)
        self.assertTrue(all(item["related"] for item in result))

    def test_school_courses_do_not_dominate_a_varied_slate(self):
        records = [
            video(1, "optics", "Double-slit interference", title="高考双缝干涉"),
            video(2, "biology", "Plant auxin", title="高中生物选修"),
            video(3, "mechanics", "Angular momentum", title="High-school physics"),
        ]
        records += [video(i, f"Other {i}", f"Other subject {i}") for i in range(4, 13)]
        result = rank(records)
        self.assertEqual(len(result), 10)
        self.assertEqual(
            sum(
                item["canonical_id"]
                in {records[i][0]["canonical_id"] for i in range(3)}
                for item in result
            ),
            1,
        )

    def test_sparse_and_unknown_topics_fill_safely_without_inventing_evidence(self):
        repeated = [
            video(i, "only available query", "Same topic") for i in range(1, 13)
        ]
        result = rank(repeated)
        self.assertEqual(len(result), 10)
        self.assertTrue(
            all(item["selection_diversity"]["relaxed"] for item in result[1:])
        )
        unknown = [video(i, None) for i in range(1, 13)]
        self.assertFalse(
            any(item["selection_diversity"]["relaxed"] for item in rank(unknown))
        )
        repeated[-1][1]["quality"] = 59
        self.assertNotIn(
            repeated[-1][0]["canonical_id"],
            {item["canonical_id"] for item in rank(repeated[-3:])},
        )
        a, b = (
            signature(*video(20, None, "Algebra")),
            signature(*video(21, None, "Algebraic geometry")),
        )
        self.assertFalse(same_topic(a, b))
        shared = video(22, "first")[0]
        shared["origins"].append({"query": "second"})
        self.assertEqual(len(query_keys(shared)), 2)
        self.assertEqual(query_keys({"origins": [{"query": "!!!"}]}), frozenset())

    def test_removed_interest_branches_leave_automatic_selection_and_can_return(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics", "music"])
            seed_interests(store)
            current = nodes(store)
            music = next(n for n in current.values() if n["label"] == "music")
            child = {
                **music,
                "id": "music-child",
                "label": "Jazz",
                "origin": "exploration",
                "parents": [music["id"]],
                "ancestors": [music["id"]],
                "depth": 2,
            }
            with store.db:
                put_node(store, child)
            set_profile(store, ["mathematics"])
            profile = get_profile(store)
            chosen = select_existing(store, profile, 1)
            self.assertEqual([n["label"] for n in chosen], ["mathematics"])
            self.assertIn(music["id"], nodes(store))
            self.assertIn(child["id"], nodes(store))
            set_profile(store, ["mathematics", "music"])
            chosen = select_existing(store, get_profile(store), 1)
            self.assertTrue(any(n["label"] in {"music", "Jazz"} for n in chosen))
