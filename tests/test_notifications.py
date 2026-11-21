from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
import json
import sys
import unittest
from unittest.mock import patch

from broader_vids import notifications
from broader_vids.processes import run_bounded
from broader_vids.storage import Store


YOUTUBE = [
    {
        "canonical_id": "youtube:dQw4w9WgXcQ",
        "title": "First",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    },
    {
        "canonical_id": "youtube:jNQXAC9IVRw",
        "title": "Second",
        "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    },
]
BILIBILI = [
    {
        "canonical_id": "bilibili:BV17x411w7KC:p2",
        "title": "第二集",
        "url": "https://www.bilibili.com/video/BV17x411w7KC?p=2",
    }
]


class NotificationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.store = self.enterContext(closing(Store(directory)))
        self.enterContext(patch.object(notifications, "_send", notifications._worker))
        self.send = self.enterContext(
            patch.object(notifications, "_post", return_value="sent")
        )

    def run_record(
        self,
        selected=None,
        *,
        platform="youtube",
        status="completed",
        day=19,
        phase="published",
    ):
        if selected is None:
            selected = YOUTUBE
        identifier = self.store.create(
            platform,
            {"kind": "recommendation"},
            {
                "phase": phase,
                "published_at": f"2026-11-{day:02d}T12:00:00+00:00",
                "selected": selected,
            },
        )
        return self.store.get(identifier) | {"status": status}

    def enqueue(self, **values):
        run = self.run_record(**values)
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            notifications.enqueue(self.store, run)
        return run["id"]

    def delivery(self):
        return notifications.settings(self.store)["deliveries"][0]

    def test_opt_in_does_not_backfill_or_send_empty_and_preview_slates(self):
        old = self.enqueue()
        self.assertEqual(
            notifications.settings(self.store),
            {"enabled": False, "topic": "", "deliveries": []},
        )
        notifications.configure(self.store, True, "https://ntfy.sh/profile_A-19")
        empty = self.enqueue(selected=[])
        preview = self.enqueue(phase="assess")
        for identifier in (old, empty, preview):
            notifications.deliver(self.store, identifier, retry=True)
        self.assertEqual(notifications.settings(self.store)["deliveries"], [])
        self.send.assert_not_called()
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 12)

    def test_manual_send_reuses_published_parts_and_preserves_automatic_receipt(self):
        notifications.configure(self.store, True, "fixture-topic")
        items = [BILIBILI[0] | {"title": "火山" * 1300}] * 3
        run = self.run_record(items, platform="bilibili", status="partial")
        self.store.save(run)
        with self.store.db:
            notifications.enqueue(self.store, run)
        notifications.deliver(self.store, run["id"])
        automatic = [call.args for call in self.send.call_args_list]
        receipt = self.delivery()
        self.assertEqual(len(automatic), 3)

        def worker(payload, timeout):
            self.assertGreater(timeout, 0)
            return notifications._manual_worker(*payload[1:])

        self.send.reset_mock()
        with (
            patch.object(notifications, "_send_in_worker", side_effect=worker),
            patch.object(
                self.store, "get", side_effect=AssertionError("Loaded full round")
            ),
        ):
            self.assertEqual(
                notifications.send_current(self.store, "bilibili", run["id"]),
                {"outcome": "sent", "accepted_parts": 3, "total_parts": 3},
            )
            self.assertEqual(
                [call.args for call in self.send.call_args_list], automatic
            )
            self.send.reset_mock()
            self.send.side_effect = ["sent", "uncertain"]
            self.assertEqual(
                notifications.send_current(self.store, "bilibili", run["id"]),
                {"outcome": "uncertain", "accepted_parts": 1, "total_parts": 3},
            )
            self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.delivery(), receipt)

    def test_manual_send_requires_current_published_list_and_free_lock(self):
        stale = self.run_record()
        preview = self.run_record(phase="assess")
        notifications.configure(self.store, True, "fixture-topic")
        with (
            notifications._delivery_lock(self.store, "youtube"),
            notifications._delivery_lock(self.store, "test"),
            patch.object(notifications, "_send_in_worker") as worker,
        ):
            self.assertEqual(
                notifications.send_current(self.store, "youtube", preview["id"]),
                {"outcome": "busy"},
            )
            self.assertEqual(
                notifications.send_test(self.store, topic_matches=lambda _: True),
                {"outcome": "busy"},
            )
            worker.assert_not_called()
        self.assertEqual(
            notifications.send_current(self.store, "youtube", stale["id"]),
            {"outcome": "stale"},
        )
        self.assertEqual(
            notifications.send_current(self.store, "youtube", preview["id"]),
            {"outcome": "unavailable"},
        )
        self.assertEqual(
            notifications._manual_worker(
                str(self.store.root), "old-topic", "title", "body"
            ),
            "canceled",
        )
        notifications.configure(self.store, False, None)
        self.assertEqual(
            notifications.send_test(self.store, topic_matches=lambda _: True),
            {"outcome": "inactive"},
        )
        self.send.assert_not_called()

    def test_configuration_is_profile_local_and_accepts_only_public_topic(self):
        for value in (
            "",
            "http://ntfy.sh/topic",
            "https://example.org/topic",
            "https://NTFY.SH/topic",
            "https://ntfy.sh:443/topic",
            "https://ntfy.sh./topic",
            "https://ntfy.sh/topic/",
            "https://ntfy.sh/topic?query",
            "https://ntfy.sh/topic#fragment",
            "https://ntfy.sh/%74opic",
            "https://me:secret@ntfy.sh/topic",
            "https://ntfy.sh@elsewhere/topic",
            "two/topics",
            "topic\n",
            "x" * 65,
            "账号",
            "v1",
            False,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                notifications.configure(self.store, True, value)

        self.assertEqual(
            notifications.configure(self.store, True, "https://ntfy.sh/profile_A-19")[
                "topic"
            ],
            "profile_A-19",
        )
        self.assertEqual(
            notifications.configure(self.store, False, None)["topic"], "profile_A-19"
        )
        self.assertEqual(
            notifications.configure(self.store, True, None)["topic"], "profile_A-19"
        )
        self.assertEqual(notifications.configure(self.store, False, "")["topic"], "")
        with TemporaryDirectory() as directory, closing(Store(directory)) as other:
            self.assertFalse(notifications.settings(other)["enabled"])
        self.send.assert_not_called()

    def test_enqueue_is_transactional_and_deduplicated(self):
        notifications.configure(self.store, True, "my-secret-topic")
        run = self.run_record()
        self.store.db.execute("BEGIN IMMEDIATE")
        notifications.enqueue(self.store, run)
        self.assertTrue(self.store.db.in_transaction)
        self.store.db.rollback()
        notifications.deliver(self.store, run["id"])
        self.send.assert_not_called()

        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            notifications.enqueue(self.store, run)
            notifications.enqueue(self.store, run)
        notifications.deliver(self.store, run["id"])
        notifications.deliver(self.store, run["id"], retry=True)
        self.send.assert_called_once()
        self.assertEqual(
            self.send.call_args.args,
            (
                "my-secret-topic",
                "Broader Vids - YouTube - 2026-11-19 - Full",
                "1. First\n"
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n\n"
                "2. Second\n"
                "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            ),
        )
        self.assertEqual(self.delivery()["status"], "sent")
        self.assertFalse(self.delivery()["can_retry"])

    def test_utf8_split_preserves_rank_entire_canonical_links_and_partial_label(self):
        notifications.configure(self.store, True, "my-topic")
        first = BILIBILI[0]
        second = {
            "canonical_id": "bilibili:BV17x411w7KC:p1",
            "title": "very long " + "🙂" * 1500,
            "url": "https://www.bilibili.com/video/BV17x411w7KC?from=tracking",
        }
        identifier = self.enqueue(
            platform="bilibili", selected=[first, second], status="partial"
        )
        notifications.deliver(self.store, identifier)
        messages = [call.args[1:] for call in self.send.call_args_list]
        self.assertEqual(len(messages), 2)
        self.assertEqual(
            messages[0],
            (
                "Broader Vids - Bilibili - 2026-11-19 - Partial (1/2)",
                "1. 第二集\nhttps://www.bilibili.com/video/BV17x411w7KC?p=2",
            ),
        )
        self.assertEqual(
            messages[1][0], "Broader Vids - Bilibili - 2026-11-19 - Partial (2/2)"
        )
        self.assertTrue(messages[1][1].startswith("2. very long 🙂"))
        self.assertTrue(
            messages[1][1].endswith(
                "…\nhttps://www.bilibili.com/video/BV17x411w7KC?p=1"
            )
        )
        self.assertLessEqual(len(messages[1][1].encode("utf-8")), 4096)
        self.assertGreater(len(messages[1][1].encode("utf-8")), 4092)

    def test_exact_boundary_includes_inter_entry_separator(self):
        notifications.configure(self.store, True, "my-topic")
        first, second = YOUTUBE
        size = len(f"1. \n{first['url']}\n\n2. {second['title']}\n{second['url']}")
        first = dict(first, title="a" * (4096 - size))
        exact = self.enqueue(selected=[first, second])
        notifications.deliver(self.store, exact)
        self.send.assert_called_once()
        self.assertEqual(len(self.send.call_args.args[2].encode("utf-8")), 4096)

        self.send.reset_mock()
        too_long = self.enqueue(
            selected=[dict(first, title=first["title"] + "a"), second]
        )
        notifications.deliver(self.store, too_long)
        self.assertEqual(self.send.call_count, 2)

    def test_bad_selected_identity_records_unsendable_delivery(self):
        notifications.configure(self.store, True, "my-topic")
        identifier = self.enqueue(
            selected=[dict(YOUTUBE[0], url="https://example.org/private")]
        )
        notifications.deliver(self.store, identifier, retry=True)
        self.send.assert_not_called()
        self.assertEqual(self.delivery()["status"], "failed")
        self.assertFalse(self.delivery()["can_retry"])

    def test_uncertain_part_requires_manual_retry_without_replaying_confirmed_prefix(
        self,
    ):
        notifications.configure(self.store, True, "my-topic")
        identifier = self.enqueue(
            selected=[dict(i, title="字" * 1600) for i in YOUTUBE]
        )
        self.send.side_effect = ["sent", "uncertain", "sent"]
        notifications.deliver(self.store, identifier)
        self.assertEqual(self.delivery()["sent_parts"], 1)
        self.assertEqual(self.delivery()["status"], "uncertain")
        self.assertTrue(self.delivery()["can_retry"])
        with closing(Store(self.store.root)) as reopened:
            notifications.deliver(reopened, identifier)
            self.assertEqual(self.send.call_count, 2)
            notifications.deliver(reopened, identifier, retry=True)
        self.assertEqual(self.send.call_count, 3)
        self.assertEqual(
            self.send.call_args_list[1].args, self.send.call_args_list[2].args
        )
        self.assertEqual(self.delivery()["status"], "sent")

    def test_delivery_window_leaves_remaining_parts_pending(self):
        notifications.configure(self.store, True, "my-topic")
        identifier = self.enqueue(
            selected=[dict(item, title="字" * 1600) for item in YOUTUBE]
        )
        current_time = 0

        def send(*args, **kwargs):
            nonlocal current_time
            current_time += notifications._DELIVERY_WINDOW
            return "sent"

        self.send.side_effect = send
        with patch.object(
            notifications.time, "monotonic", side_effect=lambda: current_time
        ):
            notifications.deliver(self.store, identifier)
            self.send.assert_called_once()
            self.assertEqual(self.delivery()["status"], "pending")
            self.assertTrue(self.delivery()["can_retry"])
            notifications.deliver(self.store, identifier, retry=True)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.delivery()["status"], "sent")

    def test_interrupted_claim_is_uncertain_after_lock_release(self):
        notifications.configure(self.store, True, "my-topic")
        identifier = self.enqueue()
        with notifications._delivery_lock(self.store, "youtube") as acquired:
            self.assertTrue(acquired)
            self.assertIsNotNone(notifications._claim(self.store, identifier, None))
            self.assertEqual(self.delivery()["status"], "sending")
            notifications.deliver(self.store, identifier, retry=True)
            self.send.assert_not_called()
        self.assertEqual(self.delivery()["status"], "uncertain")
        notifications.deliver(self.store, identifier)
        self.send.assert_not_called()
        notifications.deliver(self.store, identifier, retry=True)
        self.send.assert_called_once()

    def test_concurrent_retry_and_topic_change_leave_claimed_part_as_only_send(self):
        notifications.configure(self.store, True, "old-topic")
        identifier = self.enqueue(
            selected=[dict(i, title="字" * 1600) for i in YOUTUBE]
        )
        entered = Event()
        release = Event()

        def pause_send(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("The test did not release the sender.")
            return "sent"

        def deliver_with_new_connection():
            with closing(Store(self.store.root)) as worker:
                notifications.deliver(worker, identifier)

        self.send.side_effect = pause_send
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(deliver_with_new_connection)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.delivery()["status"], "sending")
                notifications.deliver(self.store, identifier, retry=True)
                self.assertEqual(self.send.call_count, 1)
                notifications.configure(self.store, True, "new-topic")
                self.assertEqual(self.delivery()["status"], "canceled")
            finally:
                release.set()
            future.result(timeout=5)

        self.send.side_effect = None
        notifications.deliver(self.store, identifier, retry=True)
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[0], "old-topic")
        current = self.enqueue(day=20)
        notifications.deliver(self.store, current)
        self.assertEqual(self.send.call_args.args[0], "new-topic")
        self.assertEqual(self.delivery()["run_id"], current)
        self.assertEqual(len(notifications.settings(self.store)["deliveries"]), 2)
        self.assertEqual(
            len(list((Path(self.store.root) / ".dashboard" / "ntfy").glob("*.lock"))), 1
        )

    def test_dashboard_can_retry_recent_failures_and_older_payloads_expire(self):
        notifications.configure(self.store, True, "my-topic")
        expired = self.enqueue(day=18)
        failed = self.enqueue(day=19)
        self.send.return_value = "failed"
        notifications.deliver(self.store, expired)
        notifications.deliver(self.store, failed)
        other_platform = self.enqueue(selected=BILIBILI, platform="bilibili")
        self.enqueue(day=20)
        self.enqueue(day=21)

        deliveries = notifications.settings(self.store)["deliveries"]
        self.assertEqual(len(deliveries), 4)
        self.assertNotIn(expired, [item["run_id"] for item in deliveries])
        self.assertTrue(
            next(item for item in deliveries if item["run_id"] == failed)["can_retry"]
        )
        self.assertIn(other_platform, [item["run_id"] for item in deliveries])

        self.send.reset_mock()
        self.send.return_value = "sent"
        notifications.deliver(self.store, expired, retry=True)
        self.send.assert_not_called()
        notifications.deliver(self.store, failed, retry=True)
        self.send.assert_called_once()

    def test_publication_wrapper_leaves_delivery_pending_if_lock_is_unavailable(self):
        notifications.configure(self.store, True, "my-topic")
        identifier = self.enqueue()
        with patch.object(
            notifications, "_delivery_lock", side_effect=OSError("locked")
        ):
            notifications.deliver_after_publication(self.store, identifier)
        self.send.assert_not_called()
        self.assertEqual(self.delivery()["status"], "pending")
        self.assertTrue(self.delivery()["can_retry"])


class TransportTests(unittest.TestCase):
    def test_worker_rechecks_database_after_connect_and_before_post(self):
        with (
            TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
            closing(Store(directory)) as settings_store,
            patch.object(notifications.http.client, "HTTPSConnection") as factory,
        ):
            notifications.configure(store, True, "old-topic")
            identifier = store.create("youtube", {}, {})
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                notifications.enqueue(
                    store,
                    {
                        "id": identifier,
                        "platform": "youtube",
                        "status": "completed",
                        "state": {
                            "phase": "published",
                            "published_at": "2026-11-19T12:00:00+00:00",
                            "selected": YOUTUBE,
                        },
                    },
                )
            notifications._claim(store, identifier, None)

            self.assertEqual(
                notifications._worker(directory, identifier, 1, 2), "canceled"
            )
            factory.assert_not_called()

            connection = factory.return_value
            connection.connect.side_effect = lambda: notifications.configure(
                settings_store, True, "new-topic"
            )
            self.assertEqual(
                notifications._worker(directory, identifier, 1, 1), "canceled"
            )
            connection.connect.assert_called_once()
            connection.request.assert_not_called()

            self.assertEqual(
                notifications._send(
                    root=directory,
                    run_id=identifier,
                    part=1,
                    attempt=1,
                ),
                "canceled",
            )

    def test_worker_deadline_terminates_blocked_transport_with_private_input(self):
        def stalled_worker(command, *, timeout, input):
            self.assertEqual(
                command, [sys.executable, "-m", "broader_vids.notifications"]
            )
            self.assertEqual(
                json.loads(input), ["profile-directory", "run-identifier", 2, 3]
            )
            return run_bounded(
                [sys.executable, "-c", "from time import sleep; sleep(30)"],
                timeout=timeout,
                input=input,
            )

        started = monotonic()
        with (
            patch.object(notifications, "_TIMEOUT", 0.25),
            patch.object(notifications, "run_bounded", side_effect=stalled_worker),
        ):
            self.assertEqual(
                notifications._send(
                    root="profile-directory",
                    run_id="run-identifier",
                    part=2,
                    attempt=3,
                ),
                "uncertain",
            )
        self.assertLess(monotonic() - started, 5)

    def test_direct_https_ignores_redirect_and_sends_only_expected_topic_and_body(self):
        with patch.object(notifications.http.client, "HTTPSConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value.__enter__.return_value.status = 307
            outcome = notifications._post(
                "profile-topic", "My title", "2. 中文", is_current=lambda: True
            )
        self.assertEqual(outcome, "failed")
        factory.assert_called_once_with("ntfy.sh", timeout=5)
        connection.request.assert_called_once_with(
            "POST",
            "/profile-topic",
            "2. 中文".encode("utf-8"),
            {
                "Content-Type": "text/plain; charset=utf-8",
                "X-Title": "My title",
                "Connection": "close",
            },
        )

    def test_canceled_claim_is_rechecked_after_connect_before_post(self):
        invalidated = Event()
        with patch.object(notifications.http.client, "HTTPSConnection") as factory:
            connection = factory.return_value
            connection.connect.side_effect = invalidated.set
            outcome = notifications._post(
                "old-topic",
                "Title",
                "Body",
                is_current=lambda: not invalidated.is_set(),
            )
        self.assertEqual(outcome, "canceled")
        connection.request.assert_not_called()

    def test_timeout_after_dispatch_is_uncertain_and_connections_are_closed(self):
        with patch.object(notifications.http.client, "HTTPSConnection") as factory:
            connection = factory.return_value
            connection.getresponse.side_effect = TimeoutError()
            self.assertEqual(
                notifications._post(
                    "my-topic", "Title", "Body", is_current=lambda: True
                ),
                "uncertain",
            )
            connection.close.assert_called_once()
            connection.connect.side_effect = TimeoutError()
            self.assertEqual(
                notifications._post(
                    "my-topic", "Title", "Body", is_current=lambda: True
                ),
                "failed",
            )
            connection.request.assert_called_once()
