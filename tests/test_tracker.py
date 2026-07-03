import io
import tempfile
import unittest
from pathlib import Path

import ourbit_listing_tracker as tracker


def make_config(db_path: Path) -> tracker.Config:
    return tracker.Config(
        base_url="https://www.ourbit.com",
        section_id="15425930840735",
        locale="en-US",
        page_size=30,
        max_pages=2,
        poll_interval_seconds=60,
        request_timeout_seconds=20,
        state_db_path=db_path,
        telegram_bot_token="token",
        telegram_chat_id="-100123",
        telegram_message_thread_id="",
        telegram_disable_web_page_preview=False,
        telegram_disable_notification=False,
        telegram_send_delay_seconds=0,
        title_include_regex=tracker.DEFAULT_INCLUDE_REGEX,
        title_exclude_regex=tracker.DEFAULT_EXCLUDE_REGEX,
    )


def article(article_id: int, title: str, created_at: str) -> tracker.Article:
    return tracker.Article(
        id=article_id,
        title=title,
        created_at=created_at,
        section_id="15425930840735",
        url=f"https://www.ourbit.com/support/articles/{article_id}",
    )


class TrackerTests(unittest.TestCase):
    def test_filter_includes_listings_and_excludes_delists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp) / "state.sqlite3")
            self.assertTrue(
                tracker.article_matches_filters(
                    config,
                    article(1, "Ourbit Will List DYDX (DYDX) in the Innovation Zone", "2026-07-03T08:40:23Z"),
                )
            )
            self.assertFalse(
                tracker.article_matches_filters(
                    config,
                    article(2, "Ourbit Will Delist 8 Spot Trading Pairs (July 1)", "2026-06-29T10:21:38Z"),
                )
            )

    def test_first_run_seeds_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp) / "state.sqlite3")
            sends = []

            def fetcher(_config):
                return [
                    article(2, "Ourbit Will List Token B (BBB) in the Innovation Zone", "2026-07-02T00:00:00Z"),
                    article(1, "Ourbit Will List Token A (AAA) in the Innovation Zone", "2026-07-01T00:00:00Z"),
                ]

            result = tracker.run_check(
                config,
                article_fetcher=fetcher,
                telegram_sender=lambda _config, message: sends.append(message),
            )

            self.assertEqual(result.seeded, 2)
            self.assertEqual(result.sent, 0)
            self.assertEqual(sends, [])

    def test_subsequent_run_sends_new_listings_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp) / "state.sqlite3")
            sends = []

            tracker.run_check(
                config,
                bootstrap=True,
                article_fetcher=lambda _config: [
                    article(1, "Ourbit Will List Token A (AAA) in the Innovation Zone", "2026-07-01T00:00:00Z")
                ],
                telegram_sender=lambda _config, message: sends.append(message),
            )

            def fetcher(_config):
                return [
                    article(3, "Ourbit Will List Token C (CCC) in the Innovation Zone", "2026-07-03T00:00:00Z"),
                    article(2, "Ourbit Will List Token B (BBB) in the Innovation Zone", "2026-07-02T00:00:00Z"),
                    article(1, "Ourbit Will List Token A (AAA) in the Innovation Zone", "2026-07-01T00:00:00Z"),
                    article(4, "Ourbit Will Delist Old Token (OLD)", "2026-07-04T00:00:00Z"),
                ]

            result = tracker.run_check(
                config,
                article_fetcher=fetcher,
                telegram_sender=lambda _config, message: sends.append(message),
            )

            self.assertEqual(result.new_seen, 3)
            self.assertEqual(result.sent, 2)
            self.assertEqual(result.skipped, 1)
            self.assertIn("Token B", sends[0])
            self.assertIn("Token C", sends[1])
            self.assertNotIn("Trading pairs", sends[0])
            self.assertNotIn("Published:", sends[0])
            self.assertRegex(
                sends[0],
                r"^<b>Ourbit Will List Token B \(BBB\) in the Innovation Zone</b>\n\nhttps://www\.ourbit\.com/support/articles/2$",
            )

    def test_dry_run_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            config = make_config(db_path)
            out = io.StringIO()

            result = tracker.run_check(
                config,
                dry_run=True,
                send_existing=True,
                article_fetcher=lambda _config: [
                    article(1, "Ourbit Will List Token A (AAA) in the Innovation Zone", "2026-07-01T00:00:00Z")
                ],
                output=out,
            )

            self.assertEqual(result.would_send, 1)
            conn = tracker.connect_state(db_path)
            try:
                self.assertEqual(tracker.load_seen_ids(conn), set())
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
