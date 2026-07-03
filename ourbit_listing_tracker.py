#!/usr/bin/env python3
"""Track new Ourbit listing announcements and notify Telegram."""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SECTION_ID = "15425930840735"
DEFAULT_INCLUDE_REGEX = r"\b(will\s+list|listed|new\s+listing)\b"
DEFAULT_EXCLUDE_REGEX = r"\b(delist|delisted|delisting)\b"


@dataclass
class Config:
    base_url: str
    section_id: str
    locale: str
    page_size: int
    max_pages: int
    poll_interval_seconds: int
    request_timeout_seconds: int
    state_db_path: Path
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_message_thread_id: str
    telegram_disable_web_page_preview: bool
    telegram_disable_notification: bool
    telegram_send_delay_seconds: float
    title_include_regex: Optional[str]
    title_exclude_regex: Optional[str]

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=get_env("OURBIT_BASE_URL", "https://www.ourbit.com").rstrip("/"),
            section_id=get_env("OURBIT_SECTION_ID", DEFAULT_SECTION_ID),
            locale=get_env("OURBIT_LOCALE", "en-US"),
            page_size=get_int_env("OURBIT_PAGE_SIZE", 30),
            max_pages=get_int_env("OURBIT_MAX_PAGES", 2),
            poll_interval_seconds=get_int_env("POLL_INTERVAL_SECONDS", 60),
            request_timeout_seconds=get_int_env("REQUEST_TIMEOUT_SECONDS", 20),
            state_db_path=state_db_path_from_env(
                get_env("STATE_DB_PATH", "state/ourbit_listing_tracker.sqlite3")
            ),
            telegram_bot_token=get_env("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=get_env("TELEGRAM_CHAT_ID", ""),
            telegram_message_thread_id=get_env("TELEGRAM_MESSAGE_THREAD_ID", ""),
            telegram_disable_web_page_preview=get_bool_env(
                "TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", False
            ),
            telegram_disable_notification=get_bool_env(
                "TELEGRAM_DISABLE_NOTIFICATION", False
            ),
            telegram_send_delay_seconds=get_float_env(
                "TELEGRAM_SEND_DELAY_SECONDS", 0.5
            ),
            title_include_regex=get_optional_env(
                "OURBIT_TITLE_INCLUDE_REGEX", DEFAULT_INCLUDE_REGEX
            ),
            title_exclude_regex=get_optional_env(
                "OURBIT_TITLE_EXCLUDE_REGEX", DEFAULT_EXCLUDE_REGEX
            ),
        )


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    created_at: str
    section_id: str
    url: str


@dataclass
class CheckResult:
    fetched: int = 0
    seeded: int = 0
    new_seen: int = 0
    sent: int = 0
    would_send: int = 0
    skipped: int = 0


def get_env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None else value.strip()


def get_optional_env(name: str, default: str) -> Optional[str]:
    value = get_env(name, default)
    return value or None


def get_int_env(name: str, default: int) -> int:
    value = get_env(name, str(default))
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if parsed <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return parsed


def get_float_env(name: str, default: float) -> float:
    value = get_env(name, str(default))
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {value!r}")
    if parsed < 0:
        raise ValueError(f"{name} must be zero or positive, got {value!r}")
    return parsed


def get_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def state_db_path_from_env(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            logging.warning("Ignoring malformed .env line %s", line_number)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def open_json(url: str, timeout_seconds: int) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "OurbitListingTracker/1.0"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error fetching {url}: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {body[:300]}") from exc

    if payload.get("code") != 0:
        raise RuntimeError(f"API error from {url}: {payload!r}")
    return payload


def article_url(base_url: str, locale: str, article_id: int) -> str:
    if locale == "en-US":
        return f"{base_url}/support/articles/{article_id}"
    return f"{base_url}/{locale}/support/articles/{article_id}"


def fetch_articles(config: Config) -> List[Article]:
    articles: List[Article] = []
    seen_ids = set()
    for page in range(1, config.max_pages + 1):
        query = urllib.parse.urlencode({"page": page, "perPage": config.page_size})
        url = (
            f"{config.base_url}/help/announce/api/{config.locale}"
            f"/section/{config.section_id}/articles?{query}"
        )
        payload = open_json(url, config.request_timeout_seconds)
        results = payload.get("data", {}).get("results", [])
        if not isinstance(results, list):
            raise RuntimeError(f"Unexpected articles payload: {payload!r}")
        if not results:
            break
        for item in results:
            try:
                article_id = int(item["id"])
                title = str(item["title"]).strip()
                created_at = str(item["createdAt"]).strip()
                section_id = str(item.get("sectionId") or config.section_id)
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Unexpected article item: {item!r}") from exc
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)
            articles.append(
                Article(
                    id=article_id,
                    title=title,
                    created_at=created_at,
                    section_id=section_id,
                    url=article_url(config.base_url, config.locale, article_id),
                )
            )
    return articles


def connect_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_articles (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            sent_at TEXT,
            status TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def load_seen_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(row[0]) for row in conn.execute("SELECT id FROM seen_articles")}


def mark_article(conn: sqlite3.Connection, article: Article, status: str) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_articles
            (id, title, url, created_at, first_seen_at, sent_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article.id,
            article.title,
            article.url,
            article.created_at,
            now,
            now if status == "sent" else None,
            status,
        ),
    )
    if status == "sent":
        conn.execute(
            "UPDATE seen_articles SET sent_at = ?, status = ? WHERE id = ?",
            (now, status, article.id),
        )
    elif status != "pending":
        conn.execute(
            "UPDATE seen_articles SET status = ? WHERE id = ? AND sent_at IS NULL",
            (status, article.id),
        )
    conn.commit()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_created_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def article_matches_filters(config: Config, article: Article) -> bool:
    title = article.title
    if config.title_exclude_regex and re.search(
        config.title_exclude_regex, title, re.IGNORECASE
    ):
        return False
    if config.title_include_regex and not re.search(
        config.title_include_regex, title, re.IGNORECASE
    ):
        return False
    return True


def build_message(article: Article) -> str:
    return f"<b>{html.escape(article.title)}</b>\n\n{html.escape(article.url)}"


def send_telegram_message(config: Config, text: str) -> None:
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": str(
            config.telegram_disable_web_page_preview
        ).lower(),
        "disable_notification": str(config.telegram_disable_notification).lower(),
    }
    if config.telegram_message_thread_id:
        payload["message_thread_id"] = config.telegram_message_thread_id

    data = urllib.parse.urlencode(payload).encode("utf-8")
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "OurbitListingTracker/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram network error: {exc}") from exc

    payload = json.loads(body)
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload!r}")


ArticleFetcher = Callable[[Config], List[Article]]
TelegramSender = Callable[[Config, str], None]


def run_check(
    config: Config,
    dry_run: bool = False,
    bootstrap: bool = False,
    send_existing: bool = False,
    article_fetcher: ArticleFetcher = fetch_articles,
    telegram_sender: TelegramSender = send_telegram_message,
    output: Optional[object] = None,
) -> CheckResult:
    output = output or sys.stdout
    articles = article_fetcher(config)
    result = CheckResult(fetched=len(articles))
    conn = connect_state(config.state_db_path)
    try:
        seen_ids = load_seen_ids(conn)
        if bootstrap or (not seen_ids and not send_existing):
            result.seeded = len(articles)
            if not dry_run:
                for article in articles:
                    mark_article(conn, article, "initial_seed" if not bootstrap else "bootstrap")
            return result

        unseen = [article for article in articles if article.id not in seen_ids]
        result.new_seen = len(unseen)
        for article in sorted(unseen, key=lambda item: (parse_created_at(item.created_at), item.id)):
            if not article_matches_filters(config, article):
                result.skipped += 1
                if not dry_run:
                    mark_article(conn, article, "skipped_filter")
                continue

            message = build_message(article)

            if dry_run:
                print("\n--- Telegram message preview ---", file=output)
                print(message, file=output)
                result.would_send += 1
                continue

            telegram_sender(config, message)
            mark_article(conn, article, "sent")
            result.sent += 1
            if config.telegram_send_delay_seconds:
                time.sleep(config.telegram_send_delay_seconds)
        return result
    finally:
        conn.close()


def ensure_telegram_config(config: Config) -> None:
    missing = []
    if not config.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not config.telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise SystemExit(
            "Missing Telegram config: "
            + ", ".join(missing)
            + ". Add them to .env or run with --dry-run/--bootstrap."
        )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and preview messages without sending Telegram or writing state.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Mark currently fetched announcements as seen and exit.",
    )
    parser.add_argument(
        "--send-existing",
        action="store_true",
        help="If state is empty, send fetched matching announcements instead of seeding.",
    )
    parser.add_argument(
        "--env-file",
        default=str(PROJECT_DIR / ".env"),
        help="Path to .env file. Defaults to project .env.",
    )
    parser.add_argument("--state-db", help="Override STATE_DB_PATH.")
    parser.add_argument("--log-level", default=get_env("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_env_file(Path(args.env_file).expanduser())
    config = Config.from_env()
    if args.state_db:
        config = dataclasses.replace(config, state_db_path=Path(args.state_db).expanduser())

    if not args.dry_run and not args.bootstrap:
        ensure_telegram_config(config)

    if args.once or args.bootstrap or args.dry_run:
        result = run_check(
            config,
            dry_run=args.dry_run,
            bootstrap=args.bootstrap,
            send_existing=args.send_existing,
        )
        logging.info("Check result: %s", result)
        return 0

    stop_requested = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        logging.info("Received signal %s; stopping after current sleep/check.", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    logging.info(
        "Starting Ourbit tracker: section=%s locale=%s interval=%ss state=%s",
        config.section_id,
        config.locale,
        config.poll_interval_seconds,
        config.state_db_path,
    )
    while not stop_requested:
        try:
            result = run_check(config, send_existing=args.send_existing)
            logging.info("Check result: %s", result)
        except Exception:
            logging.exception("Tracker check failed")

        for _ in range(config.poll_interval_seconds):
            if stop_requested:
                break
            time.sleep(1)
    logging.info("Stopped Ourbit tracker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
