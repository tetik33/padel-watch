# Padel Watch

A free system that watches padel websites, Meetup and WhatsApp groups and sends a Telegram message when
something useful shows up. Everything is controlled with the Telegram bot's buttons.

## What is checked

| What | How often | Program |
|---|---|---|
| Sun Set events (sunsetpadel.at/events): new events, free spots | every 5 min | `sunset_events.py` |
| Padel-Austria tournaments (Wien, Starter, open) | hourly + button | `tournaments.py` |
| Free 2-hour courts at Padeldome and other clubs | button + urgent search | `free_courts.py` |
| Meetup padel events (group calendar + e-mails) | every 5 min, in Google | `backup/meetup_google_script.gs` |
| WhatsApp "players wanted" posts | live, on the Wispbyte server | `backup/whatsapp_server/` |

## The three pinned messages in Telegram

- **status** – every watcher with "updated … ago" and what it found; urgent search at the end.
- **renewals** – what must be renewed when (GitHub token, Wispbyte login, WhatsApp on the spare phone),
  with links. Press ✅ after renewing; reminders come at 09:00 and disappear when you press ✅.
- **problems** – everything that is broken right now, in plain words with what to do. A new problem also
  sends one notification (held from 23:00 to 07:00) that disappears when the problem is solved.

## Files

| File / folder | What it is |
|---|---|
| `sunset_events.py`, `tournaments.py`, `free_courts.py` | the three website checkers |
| `telegram_bot.py` | the Telegram bot: buttons, pinned messages, urgent search, mute, WhatsApp summary |
| `shared.py` | helpers used by all programs (settings, saving, Telegram, plain-English errors) |
| `config.toml` | **all settings** – edit it here on GitHub (pencil icon) |
| `state/` | the bot's memory, written automatically – never edit or delete |
| `tests/` | safety checks, run after every upload ("Tests" in the Actions tab) |
| `backup/` | copies of code that runs elsewhere (Google script, Wispbyte server) – GitHub does not use it |
| `.github/workflows/` | the automations: `sunset_events.yml`, `tournaments.yml`, `free_courts.yml`, `telegram_bot.yml`, `tests.yml` |

## Secrets (Settings → Secrets and variables → Actions)

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `WISPBYTE_URL`, `WISPBYTE_KEY`, `MEETUP_EVENTS_URL`.

**This repository is public.** Never write a token, key, chat id, server address or invite link into a
file here. They belong in the secrets above, in the Google script's Script properties, or on the server.

## Starting the automations (cron-job.org)

| Job | Workflow file | Schedule |
|---|---|---|
| Telegram bot | `telegram_bot.yml` | every minute |
| Sun Set events | `sunset_events.yml` | every 5 minutes |

URL: `https://api.github.com/repos/<user>/padel-watch/actions/workflows/<workflow file>/dispatches`,
method POST, body `{"ref":"main"}`, header `Authorization: Bearer <GitHub token>`.
Tournaments run hourly by themselves; court checks are started by the bot.

## WhatsApp server (Wispbyte) – files on the server only

| File | What it is |
|---|---|
| `index.js`, `core.js`, `package.json` | the program (copies in `backup/whatsapp_server/`) |
| `secrets.json` (or host settings) | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `API_KEY` (= GitHub secret `WISPBYTE_KEY`), `PORT` |
| `group_links.json` | `{"<group id>@g.us": "https://chat.whatsapp.com/…", "<group name>": "…"}` |
| `settings.json` (optional) | `quiet_from`/`quiet_to` (Vienna hours, posts collected into one morning message), `weekdays`, `include_words`, `exclude_words`, `dedupe_similarity` |
| `auth_info/`, `backup/` | the WhatsApp login and its daily copies – keep private |

If WhatsApp logs the bridge out: delete `auth_info`, restart, scan the QR code with the spare phone.
Log in to Wispbyte at least every 14 days, or the free server stops (the renewals message reminds you).

## Meetup (Google Apps Script)

Runs in Google, not here. Setup steps are at the top of `backup/meetup_google_script.gs`.
Add your Meetup groups to `GROUPS` in the script (the part after `meetup.com/`).
Run `sendAllPadelEventsNow` in the script to get all upcoming padel events at once.
