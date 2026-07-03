# Ourbit New Listing Tracker

Tracks Ourbit's `Listing Information` announcements and sends a Telegram message when a new listing announcement appears.

The inspected Ourbit page is a Next.js support page. The visible category URL:

```text
https://www.ourbit.com/support/categories/15425930840735
```

loads article rows from this JSON endpoint:

```text
https://www.ourbit.com/help/announce/api/en-US/section/15425930840735/articles?page=1&perPage=30
```

Article details come from:

```text
https://www.ourbit.com/help/announce/api/en-US/article/{article_id}
```

## Behavior

- First run seeds the current fetched announcements as already seen, so old listings are not sent to Telegram.
- Later checks send unseen announcements oldest-first.
- Defaults only send listing-style titles and skip delisting titles.
- Seen IDs are stored in SQLite at `state/ourbit_listing_tracker.sqlite3`.
- Credentials live in `.env`, which is ignored by git.

## Notification Sample

Telegram messages look like this:

```text
New Ourbit listing announcement

Ourbit Will List DYDX (DYDX) in the Innovation Zone
Published: 2026-07-03 08:40:23 UTC

Trading pairs: DYDX/USDT
Deposit: 08:50, July 3 2026 (UTC)
Withdrawal: 08:50, July 4 2026 (UTC)
Trading for DYDX/USDT: 08:50, July 3 2026 (UTC)

https://www.ourbit.com/support/articles/17827791513176
```

In Telegram, the title is bold because the bot sends HTML-formatted messages.

## Quick Start

```bash
git clone https://github.com/cobecheng/Ourbit-new-listing-tracker.git
cd Ourbit-new-listing-tracker
cp .env.example .env
```

Fill `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then run:

```bash
python3 ourbit_listing_tracker.py --bootstrap
python3 ourbit_listing_tracker.py
```

## Setup

```bash
python3 --version  # Python 3.9+ recommended
cp .env.example .env
```

Edit `.env`:

```text
TELEGRAM_BOT_TOKEN=123456:abc...
TELEGRAM_CHAT_ID=-1001234567890
```

For Telegram, create a bot with `@BotFather`, add the bot to your group/channel, and make it an admin if it is posting to a channel. Public channels can use `@channelusername`; private groups/channels usually need the numeric `-100...` chat ID.

## Test Locally

Preview without sending or marking announcements as seen:

```bash
python3 ourbit_listing_tracker.py --once --dry-run --send-existing
```

Seed current announcements as seen:

```bash
python3 ourbit_listing_tracker.py --bootstrap
```

Run one real check:

```bash
python3 ourbit_listing_tracker.py --once
```

Run continuously:

```bash
python3 ourbit_listing_tracker.py
```

## Run 24/7 On macOS

From the project directory:

```bash
python3 ourbit_listing_tracker.py --bootstrap
chmod +x scripts/install_launchd.sh
./scripts/install_launchd.sh
```

Check status:

```bash
launchctl print gui/$(id -u)/com.cobecheng.ourbit-listing-tracker
```

Watch logs:

```bash
tail -f logs/ourbit-listing-tracker.out.log
tail -f logs/ourbit-listing-tracker.err.log
```

Stop and unload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.cobecheng.ourbit-listing-tracker.plist
```

## Configuration

Most settings are in `.env.example`.

Useful knobs:

```text
POLL_INTERVAL_SECONDS=60
OURBIT_MAX_PAGES=2
OURBIT_INCLUDE_DETAILS=true
OURBIT_TITLE_INCLUDE_REGEX=\b(will\s+list|listed|new\s+listing)\b
OURBIT_TITLE_EXCLUDE_REGEX=\b(delist|delisted|delisting)\b
```

Set `OURBIT_TITLE_INCLUDE_REGEX=` and `OURBIT_TITLE_EXCLUDE_REGEX=` if you want every announcement in the category, including delists.

## Development Checks

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile ourbit_listing_tracker.py tests/test_tracker.py
```
