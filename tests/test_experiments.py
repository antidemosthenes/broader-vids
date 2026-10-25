from pathlib import Path
import tempfile
import sqlite3
import unittest

from deepvids.experiments import create_experiment, create_search_control, execute
from deepvids.http import AccessPaused
from deepvids.models import Archive, Outcome, Part, Resolution, SearchPage
from deepvids.reports import make_report, release_gate, write_report
from deepvids.storage import Store


class FakeHttp:
    def __init__(self, store, run):
        self.previous = run["elapsed"]

    def elapsed(self):
        return self.previous + 0.001


class HealthyBili:
    def __init__(self, http):
        pass

    def resolve(self, aid):
        count = 1 + aid % 3
        return Resolution(Outcome.VALID, Archive("bilibili", str(aid), "Fixture",
                          tuple(Part(f"bilibili:{aid}:{n}", str(n), 120 + n) for n in range(count))))


class FlakyBili(HealthyBili):
    def resolve(self, aid):
        if aid not in (1, 2, 3, 4):
            raise AccessPaused("Fixture access failure")
        return super().resolve(aid)


class HealthyYouTube:
    searches = 0

    def __init__(self, http):
        pass

    def bootstrap(self):
        return "fixture-client"

    def search(self, prefix, client_version):
        type(self).searches += 1
        return SearchPage([prefix + "-aaaaA", prefix + "-aaaaA", "bad"], True)

    def resolve(self, video_id):
        return Resolution(Outcome.VALID, Archive("youtube", video_id, "Fixture", (Part(video_id, "Fixture", 5500),)))


class FlakyYouTube(HealthyYouTube):
    def resolve(self, video_id):
        raise AccessPaused("Fixture metadata failure")


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def bili_run(self):
        return create_experiment(self.store, "bilibili", seed=73, steps=32,
                                 seed_aids=[1, 2, 3, 4], seed_provenance="Synthetic test population")

    def test_pending_proposal_resume_matches_uninterrupted_chain(self):
        interrupted = self.bili_run()
        paused = execute(self.store, interrupted, FakeHttp, bili_factory=FlakyBili)
        self.assertEqual(paused["status"], "paused")
        pending = [c["pending"] for c in paused["state"]["chains"] if c["pending"]]
        self.assertEqual(len(pending), 1)
        completed = execute(self.store, interrupted, FakeHttp, bili_factory=HealthyBili)
        self.assertEqual(completed["status"], "completed")
        baseline = self.bili_run()
        execute(self.store, baseline, FakeHttp, bili_factory=HealthyBili)
        payloads = lambda run: [e["payload"] for e in self.store.events(run) if e["kind"] == "sample"]
        self.assertEqual(payloads(interrupted), payloads(baseline))
        self.assertEqual(len(payloads(interrupted)), 128)
        self.assertTrue(any(not e["moved"] for e in payloads(interrupted)))
        before = len(self.store.events(interrupted))
        execute(self.store, interrupted, FakeHttp, bili_factory=HealthyBili)
        self.assertEqual(len(self.store.events(interrupted)), before)

    def test_youtube_dedup_and_saved_metadata_phase(self):
        run_id = create_experiment(self.store, "youtube", queries=3, seed=4)
        paused = execute(self.store, run_id, FakeHttp, youtube_factory=FlakyYouTube)
        self.assertEqual(paused["state"]["query_index"], 3)
        self.assertEqual(len(paused["state"]["candidates"]), 3)
        self.assertEqual(paused["state"]["metadata_index"], 0)
        HealthyYouTube.searches = 0
        run = execute(self.store, run_id, FakeHttp, youtube_factory=HealthyYouTube)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(HealthyYouTube.searches, 0)
        report = make_report(self.store, run_id)
        self.assertEqual(report["unique_parts"], 3)
        self.assertEqual(report["over_90_minutes"], 3)
        self.assertEqual(report["duration_eligible_unique_parts"], 0)
        self.assertEqual(report["truncated_prefix_pages"], 3)
        path = write_report(self.store, run_id)
        self.assertTrue(path.is_file())
        self.assertTrue((path.parent / "trace.jsonl").is_file())

    def test_gate_does_not_confuse_algorithm_success_with_coverage(self):
        run_id = self.bili_run()
        execute(self.store, run_id, FakeHttp, bili_factory=HealthyBili)
        self.assertFalse(release_gate(self.store)["ready"])
        report = make_report(self.store, run_id)
        self.assertFalse(report["coverage_validated"])
        self.assertFalse(report["content_access_validated"])

    def test_metadata_cursor_and_sample_commit_together(self):
        run_id = create_experiment(self.store, "youtube", queries=1)
        self.store.db.execute("""CREATE TRIGGER fail_sample BEFORE INSERT ON events
            WHEN NEW.kind='sample' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            execute(self.store, run_id, FakeHttp, youtube_factory=HealthyYouTube)
        self.assertEqual(self.store.get(run_id)["state"]["metadata_index"], 0)
        self.assertFalse(any(e["kind"] == "resolution" for e in self.store.events(run_id)))
        self.store.db.execute("DROP TRIGGER fail_sample")
        execute(self.store, run_id, FakeHttp, youtube_factory=HealthyYouTube)
        self.assertEqual(make_report(self.store, run_id)["unique_parts"], 1)

    def test_search_control_does_not_count_as_a_sampler_trial(self):
        run_id = create_search_control(self.store, "AbcDE-aaaaA")
        result = execute(self.store, run_id, FakeHttp, youtube_factory=HealthyYouTube)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(release_gate(self.store)["platforms"]["youtube"]["experiment_count"], 0)
        self.assertEqual(len(make_report(self.store, run_id)["control_results"]), 1)

    def test_run_lease_is_exclusive(self):
        run_id = self.bili_run()
        with self.store.lease(run_id):
            with self.assertRaises(ValueError):
                with self.store.lease(run_id):
                    self.fail("Second lease should fail")
        with self.store.lease(run_id):
            self.assertTrue(True)

    def test_run_id_cannot_escape_the_state_directory(self):
        with self.assertRaises(ValueError):
            with self.store.lease("../outside"):
                self.fail("Invalid run ID must not acquire a lease")

    def test_invalid_budget_and_seed_configuration(self):
        for budget in (0, 1001):
            with self.assertRaises(ValueError):
                create_experiment(self.store, "youtube", max_requests=budget)
        with self.assertRaises(ValueError):
            create_experiment(self.store, "youtube", max_seconds=float("nan"))
        with self.assertRaises(ValueError):
            create_experiment(self.store, "bilibili", seed_aids=[1, 1, 2, 3], seed_provenance="Test")


if __name__ == "__main__":
    unittest.main()

