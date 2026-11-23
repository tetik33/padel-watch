"""Telegram remote control for the Padeldome court check."""
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import shared as W

# WhatsApp bridge (Wispbyte server): address and key come from GitHub secrets, never from this public file.
WISPBYTE_URL = os.environ.get("WISPBYTE_URL", "").strip().rstrip("/")
WISPBYTE_KEY = os.environ.get("WISPBYTE_KEY", "").strip()

WORKFLOW = "free_courts.yml"
EVENTS_WORKFLOW = "tournaments.yml"
FLAG = "state/urgent_settings.txt"
URGENT_STATE = "state/urgent_reported.txt"
LISTEN_SECONDS = int(os.environ.get("LISTEN_SECONDS", "50"))
MAX_AGE_SEC = 30 * 60
URGENT_EVERY_SEC = W.get("urgent", "every_sec", 270)
URGENT_BACKOFF_SEC = W.get("urgent", "backoff_sec", 900)
URGENT_MAX_HOURS = W.get("urgent", "max_hours", 48)
PAUSE_HOURS = W.get("urgent", "pause_hours", 2)
EVENTS_FILE = "state/sunset_events.txt"
STATUS_FILE = "state/status_message_id.txt"

def _date(text, default):
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return default

TOKEN_EXPIRES = _date(W.get("renewals", "github_token_expires", W.get("bot", "token_expires", "2026-12-12")), dt.date(2026, 12, 12))
REMIND_DAYS = tuple(n for n in W.get("bot", "remind_days", [14, 7, 3, 1, 0]) if isinstance(n, int))
REMINDERS_FILE = "state/reminders.txt"
STATUS_EVERY_MIN = W.get("bot", "status_every_min", 5)

WATCHERS = [
    ("📅 Sun Set events", "sunset_events.yml", 45),
    ("🎾 Court checks", "free_courts.yml", None),
    ("🏆 Padel-Austria tournaments", "tournaments.yml", 180),
]
CADENCE = {
    "sunset_events.yml": "every 5 min", "free_courts.yml": "on request + urgent", "tournaments.yml": "hourly", "urgent": "checked every 5 min",
    "meetup": "every 5 min",
}
CADENCE.update({k: v for k, v in W.get("bot", "cadence", {}).items() if k in CADENCE and isinstance(v, str)})
HEALTH_KEY = {"sunset_events.yml": "sunset", "free_courts.yml": "courts", "tournaments.yml": "events"}
MUTE_KEY = {"sunset_events.yml": "sunset", "tournaments.yml": "events"}
MUTE_LABELS = {"sunset": "📅 Sun Set events", "events": "🏆 Tournaments", "meetup": "🤝 Meetup events"}
MUTE_HOURS = [h for h in W.get("bot", "mute_hours", [2, 8, 24, 72, 168]) if isinstance(h, int) and 0 < h <= 24 * 60] or [2, 8, 24]
QUIET_FROM, QUIET_TO = W.get("urgent", "quiet_from", 23), W.get("urgent", "quiet_to", 7)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
MEETUP_URL = os.environ.get("MEETUP_EVENTS_URL", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
REF = os.environ.get("GITHUB_REF_NAME", "main") or "main"

COMMANDS = {
    "check all options": "check",
    "urgent padel": "urgent",
    "urgent evenings only": "start_evenings",
    "urgent main 4 only": "start_main",
    "extend urgent +48h": "extend",
    "pause alerts 2h": "pause",
    "resume alerts": "pause",
    "all locations": "start_all",
    "sun set events": "events",
    "meetup events": "meetup",
    "meetup": "meetup",
    "mute": "mute",
    "mute alerts": "mute",
    "unmute": "mute",
    "padel-austria tournaments": "padel_events",
    "padel austria events": "padel_events",
    "stop urgent check": "stop",
    "whatsapp summary": "whatsapp_summary",
    "pause": "pause",
    "extend": "extend",
    "evenings": "start_evenings",
    "evening": "start_evenings",
    "main 4": "start_main",
    "main": "start_main",
    "sun set": "events",
    "tournaments": "padel_events",
    "help": "menu",
    "status": "status",
    "check": "check",
    "urgent": "urgent",
    "padeldome check": "check",
    "padeldome urgent": "urgent",
    "stop": "stop",
    "/check": "check",
    "/urgent": "urgent",
    "/stop": "stop",
    "/start": "menu",
    "/menu": "menu",
    "menu": "menu",
}

def keyboard():
    flag = read_flag()
    if not flag:
        rows = [["🎾 Check all options", "🏆 Padel-Austria tournaments"],
                ["🚨 Urgent padel", "📅 Sun Set events"],
                ["🤝 Meetup events", "📱 WhatsApp Summary"],
                ["🔕 Mute"]]
    else:
        rows = [["🎾 Check all options", "🏆 Padel-Austria tournaments"],
                ["📅 Sun Set events", "🛑 Stop urgent check"],
                ["⏱ Extend urgent +48h", "🔕 Mute"],
                ["🤝 Meetup events", "📱 WhatsApp Summary"]]
    return json.dumps({
        "keyboard": [[{"text": t} for t in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    })

WIZ_KINDS = ("d", "t", "p", "n", "f", "o", "g", "b", "x", "m", "u", "r")
WIZ = []
WIZ_EARLIEST = [7, 9, 12, 16, 17, 18]
WIZ_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WIZ_MAX_DAYS = W.get("courts", "days_ahead", 15)

def wiz_pack(st):
    return f"{st['days']}.{st['mask']}.{st['main']}.{'-' if st['frm'] is None else st['frm']}"

def wiz_unpack(text):
    try:
        days, mask, main, frm = text.split(".")
        st = {"days": int(days), "mask": mask, "main": int(main),
              "frm": None if frm == "-" else int(frm), "step": 2}
        if not (1 <= st["days"] <= WIZ_MAX_DAYS and len(mask) == 7 and set(mask) <= {"0", "1"}
                and st["main"] in (0, 1) and (st["frm"] is None or 0 <= st["frm"] <= 23)):
            return None
        return st
    except (ValueError, AttributeError):
        return None

def wiz_btn(text, data):
    return {"text": text, "callback_data": data}

def wiz_render(st):
    pk = wiz_pack(st) if st else ""
    if st is None:
        text = ("<b><u>🚨 URGENT SEARCH · step 1 of 3</u></b>\n\n<b>How many days ahead?</b>\n"
                "Today counts as day 1. The club books up to 14 days ahead, so 15 is the maximum.")
        nums = [wiz_btn(str(n), f"d:{n}") for n in range(1, WIZ_MAX_DAYS + 1)]
        rows = [[wiz_btn("⚡ Evenings Mon-Thu · next 10 days · 18-20", "g:10.1111000.0.-:18:20")],
                [wiz_btn("⚡ As before: 3 days · any day · any time", "g:3.1111111.0.-:7:21")]]
        rows += [nums[i:i + 5] for i in range(0, len(nums), 5)]
        rows.append([wiz_btn("❌ Cancel", "x")])
        return text, json.dumps({"inline_keyboard": rows})
    summary = f"📅 <b>{st['days']} days</b>"
    if st["step"] == 2:
        text = (f"<b><u>🚨 URGENT SEARCH · step 2 of 3</u></b>\n{summary}\n\n"
                "<b>Which weekdays?</b> Tap single days and then Next, or tap a ready-made set to go on right away.")
        day_btns = [wiz_btn(("✅ " if c == "1" else "⬜ ") + WIZ_DAY_NAMES[i], f"t:{pk}:{i}")
                    for i, c in enumerate(st["mask"])]
        rows = [day_btns[:4], day_btns[4:],
                [wiz_btn("Mon-Thu", f"p:{pk}:1111000"), wiz_btn("All days", f"p:{pk}:1111111"),
                 wiz_btn("Sat-Sun", f"p:{pk}:0000011")],
                [wiz_btn("➡️ Next", f"n:{pk}")],
                [wiz_btn("❌ Cancel", "x")]]
        return text, json.dumps({"inline_keyboard": rows})
    where = "main 4 locations only" if st["main"] else "all locations"
    summary += f" · 🗓 <b>{wd_text(st['mask'])}</b> · 📍 {where}"
    if st["frm"] is None:
        text = (f"<b><u>🚨 URGENT SEARCH · step 3 of 3</u></b>\n{summary}\n\n"
                "<b>Earliest start of the game?</b> (a game lasts 2 hours)\n"
                "Or tap a shortcut to start the search right away.")
        rows = [[wiz_btn("⚡ 18-20 evening", f"g:{pk}:18:20"), wiz_btn("⚡ 17-21", f"g:{pk}:17:21"),
                 wiz_btn("⚡ Any time", f"g:{pk}:7:21")],
                [wiz_btn(f"{h:02d}:00", f"f:{pk}:{h}") for h in WIZ_EARLIEST],
                [wiz_btn(("🏠 Main 4 only: ON" if st["main"] else "🏠 Main 4 only: OFF"), f"o:{pk}")],
                [wiz_btn("⬅️ Back", f"b:{pk}"), wiz_btn("❌ Cancel", "x")]]
        return text, json.dumps({"inline_keyboard": rows})
    text = (f"<b><u>🚨 URGENT SEARCH · step 3 of 3</u></b>\n{summary}\n"
            f"⏰ earliest start <b>{st['frm']:02d}:00</b>\n\n<b>Latest start?</b> The search starts right after your tap.")
    hours = list(range(st["frm"], 22)) or [21]
    btns = [wiz_btn(f"{h:02d}:{'30' if h >= 21 else '00'}", f"g:{pk}:{st['frm']}:{h}") for h in hours]
    rows = [btns[i:i + 5] for i in range(0, len(btns), 5)]
    rows.append([wiz_btn("⬅️ Earlier start", f"n:{pk}"), wiz_btn("❌ Cancel", "x")])
    return text, json.dumps({"inline_keyboard": rows})

def wiz_send_first():
    text, markup = wiz_render(None)
    try:
        tg("sendMessage", chat_id=CHAT, text=text, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        print(f"WARNING: could not send the urgent menu ({type(exc).__name__}).")

def wiz_edit(mid, text, markup=None):
    try:
        tg("editMessageText", chat_id=CHAT, message_id=mid, text=text, parse_mode="HTML",
           reply_markup=markup or json.dumps({"inline_keyboard": []}))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore") if hasattr(exc, "read") else ""
        if "not modified" not in body.lower():
            print(f"WARNING: could not update the urgent menu: HTTP {exc.code} {body[:150]}")
    except Exception as exc:
        print(f"WARNING: could not update the urgent menu ({type(exc).__name__}).")

def wiz_start(st, first, last):
    opts = [f"days={st['days']}", f"wd={st['mask']}", f"from={first}", f"to={last}"]
    if st["main"]:
        opts.append("main")
    flag = read_flag()
    if flag and flag["opts"] == opts:
        say(f"🚨 Urgent mode is already on with these settings ({opts_text(opts)}).")
        return False
    set_urgent(True, opts)
    dispatch("urgent")
    today = dt.datetime.now(ZoneInfo("Europe/Vienna")).date()
    n_days = sum(1 for i in range(st["days"]) if st["mask"][(today + dt.timedelta(days=i)).weekday()] == "1")
    minutes = n_days * (4 if st["main"] else 8) * 2.8 / 60
    text = (f"🚨 Urgent search {'switched' if flag else 'ON'}\n"
            f"📅 Next {st['days']} days (today counts as day 1)\n"
            f"🗓 {wd_text(st['mask'])}\n"
            f"🕕 Starts {first:02d}:00-{last:02d}:{'30' if last >= 21 else '00'}, 2h block\n"
            f"📍 {'Main 4 locations only' if st['main'] else 'All locations'}\n"
            "🔁 About every 5 minutes, ends after 48 hours, no checks 23:00-07:00.")
    if minutes >= 4:
        text += (f"\n⏱ One round takes about {minutes:.0f} minutes with this many days, so a message can come a "
                 "little later." + ("" if st["main"] else " 'Main 4 only' makes it about twice as fast."))
    say(text + "\n\nButtons below: Extend · Stop · Mute")
    return True

def wiz_run(ops):
    started = False
    cache = {}
    for mid, data in ops:
        parts = data.split(":")
        kind = parts[0]
        if kind == "x":
            cache[mid] = "cancel"
            continue
        if kind == "d":
            try:
                n = int(parts[1])
            except (IndexError, ValueError):
                continue
            if 1 <= n <= WIZ_MAX_DAYS:
                cache[mid] = {"days": n, "mask": "1111000", "main": 0, "frm": None, "step": 2}
            continue
        st = cache.get(mid)
        if isinstance(st, str):
            continue
        if kind in ("m", "u", "r"):
            cache[mid] = ("mute", *mute_apply(kind, parts[1:]))
            continue
        if isinstance(st, tuple):
            continue
        if st is None:
            st = wiz_unpack(parts[1]) if len(parts) > 1 else None
        if st is None:
            continue
        try:
            if kind == "t":
                i = int(parts[2])
                mask = list(st["mask"])
                mask[i] = "0" if mask[i] == "1" else "1"
                st["mask"], st["step"] = "".join(mask), 2
            elif kind == "p":
                if len(parts[2]) == 7 and "1" in parts[2] and set(parts[2]) <= {"0", "1"}:
                    st["mask"] = parts[2]
                    st["step"], st["frm"] = 3, None
            elif kind == "b":
                st["step"], st["frm"] = 2, None
            elif kind == "n":
                if "1" in st["mask"]:
                    st["step"], st["frm"] = 3, None
            elif kind == "f":
                st["step"], st["frm"] = 3, int(parts[2])
            elif kind == "o":
                st["main"] = 0 if st["main"] else 1
                st["step"] = 3
            elif kind == "g":
                first, last = int(parts[2]), int(parts[3])
                if "1" in st["mask"] and 0 <= first <= last <= 23:
                    if wiz_start(st, first, last):
                        started = True
                    cache[mid] = "done"
                    continue
        except (IndexError, ValueError):
            continue
        cache[mid] = st
    for mid, st in cache.items():
        if isinstance(st, tuple):
            wiz_edit(mid, st[1], st[2])
        elif st == "cancel":
            wiz_edit(mid, "❌ Cancelled.")
        elif st == "done":
            wiz_edit(mid, "✅ Urgent search set up, details in the message below.")
        else:
            wiz_edit(mid, *wiz_render(st))
    return started

def hours_label(h):
    return f"{h} h" if h < 24 else f"{h // 24} day" + ("s" if h >= 48 else "")

def mute_items():
    mutes = W.mute_read()
    items = [(k, MUTE_LABELS[k], mutes.get(k)) for k in W.MUTE_NAMES]
    flag = read_flag()
    if flag:
        paused = flag["pause"] if flag["pause"] and flag["pause"] > dt.datetime.now(dt.timezone.utc) else None
        items.append(("urgent", "🚨 Urgent search", paused))
    return items

def mute_keys(key):
    if key == "all":
        return [k for k, _, _ in mute_items()]
    return [key] if key in [k for k, _, _ in mute_items()] else []

def mute_set(keys, until):
    mutes = W.mute_read()
    for k in keys:
        if k == "urgent":
            flag = read_flag()
            if flag:
                write_flag(True, flag["opts"], since=flag["since"], pause=until,
                           message="Urgent alerts paused" if until else "Urgent alerts resumed")
        elif until:
            mutes[k] = until
        else:
            mutes.pop(k, None)
    os.makedirs("state", exist_ok=True)
    with open(W.MUTE_FILE, "w", encoding="utf-8") as f:
        f.write(W.mute_text(mutes))
    commit_files("Mute list changed", add=[str(W.MUTE_FILE)])

def mute_render(pick=None):
    items = mute_items()
    if pick is None:
        lines = ["<b><u>🔕 MUTE ALERTS</u></b>", "", "<b>What should be quiet?</b>", ""]
        for _, label, until in items:
            lines.append(f"{label}: " + (f"🔕 muted until <b>{vienna(until):%a %d.%m. %H:%M}</b>" if until else "🔔 on"))
        lines += ["", "While muted, nothing is checked. When the mute ends you get what is new since before."]
        btns = [wiz_btn(("🔕 " if until else "") + label, f"m:{key}") for key, label, until in items]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append([wiz_btn("🔕 Everything", "m:all")])
        if any(until for _, _, until in items):
            rows.append([wiz_btn("🔔 Unmute all", "r:all")])
        rows.append([wiz_btn("❌ Close", "x")])
        return "\n".join(lines), json.dumps({"inline_keyboard": rows})
    names = dict((k, label) for k, label, _ in items)
    label = "All alerts" if pick == "all" else names.get(pick, pick)
    muted_now = any(until for k, _, until in items if pick == "all" or k == pick)
    text = f"<b><u>🔕 MUTE ALERTS</u></b>\n{label}\n\n<b>For how long?</b>"
    btns = [wiz_btn(hours_label(h), f"u:{pick}:{h}") for h in MUTE_HOURS]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    if muted_now:
        rows.append([wiz_btn("🔔 Unmute now", f"r:{pick}")])
    rows.append([wiz_btn("⬅️ Back", "m:menu"), wiz_btn("❌ Cancel", "x")])
    return text, json.dumps({"inline_keyboard": rows})

def mute_apply(kind, args):
    try:
        if kind == "m":
            return mute_render(None if args[0] == "menu" else args[0])
        keys = mute_keys(args[0])
        if not keys:
            return mute_render(None)
        names = dict((k, label) for k, label, _ in mute_items())
        label = "All alerts" if args[0] == "all" else names[args[0]]
        if kind == "u":
            hours = int(args[1])
            if hours not in MUTE_HOURS:
                return mute_render(None)
            until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
            mute_set(keys, until)
            text = (f"🔕 <b>{label}</b> muted for {hours_label(hours)}, until <b>{vienna(until):%a %d.%m. %H:%M}</b>.\n"
                    "Then you get what is new since before. To end it earlier: tap Mute again.")
            if "urgent" in keys:
                text += "\n🚨 The urgent search is paused until then."
        else:
            mute_set(keys, None)
            text = f"🔔 <b>{label}</b> switched on again."
        return text, json.dumps({"inline_keyboard": []})
    except (IndexError, ValueError):
        return mute_render(None)
    except Exception as exc:
        print(f"ERROR: mute menu failed ({type(exc).__name__}).")
        return "⚠️ Could not change the mute. See the run log on GitHub.", json.dumps({"inline_keyboard": []})

def mute_send_menu():
    text, markup = mute_render(None)
    try:
        tg("sendMessage", chat_id=CHAT, text=text, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        print(f"WARNING: could not send the mute menu ({type(exc).__name__}).")

CALLBACKS = {"start_all": "start_all", "start_evenings": "start_evenings", "start_main": "start_main"}

HELP = (
    "Buttons: Check all options · Padel-Austria tournaments · Urgent padel · Sun Set events · Meetup events.\n"
    "Mute: silence Sun Set, tournaments, Meetup or the urgent search for 2 hours up to 7 days.\n"
    "Urgent padel opens a 3-step menu: days ahead, weekdays, start times.\n"
    "While urgent mode is on you also get Stop and Extend. To pause the urgent search, use Mute.\n\n"
    "You can also type: stop · pause · extend · evenings · main 4 · sun set · status · help"
)

def tg(method, **params):
    http_timeout = params.pop("_timeout", 20)
    data = urllib.parse.urlencode(params).encode("utf-8")
    for attempt in range(4):
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            err = exc
        except (urllib.error.URLError, OSError) as exc:
            err = exc
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise err

def say(text, markup=None):
    try:
        tg("sendMessage", chat_id=CHAT, text=text, reply_markup=markup or keyboard())
    except Exception as exc:
        print(f"WARNING: reply failed ({type(exc).__name__}).")

def gh(path, body=None, method=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method or ("POST" if body is not None else "GET"),
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}

def dispatch(mode, workflow=None):
    gh(f"actions/workflows/{workflow or WORKFLOW}/dispatches", {"ref": REF, "inputs": {"mode": mode}})

def last_run():
    runs = gh(f"actions/workflows/{WORKFLOW}/runs?per_page=1").get("workflow_runs", [])
    return runs[0] if runs else None

def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)

def commit_files(message, add=(), remove=()):
    W.save_files(message, add, remove)

def read_flag():
    try:
        text = open(FLAG, encoding="utf-8").read().strip()
    except OSError:
        return None
    words = text.split()
    if not words or words[0].lower() != "on":
        return None
    since, pause, opts = None, None, []
    for w in words[1:]:
        if w in ("evenings", "main") or w.split("=")[0] in ("days", "wd", "from", "to"):
            opts.append(w)
        elif w.startswith("pause="):
            try:
                pause = dt.datetime.fromisoformat(w[6:])
            except ValueError:
                pass
        elif since is None:
            try:
                since = dt.datetime.fromisoformat(w)
            except ValueError:
                pass
    return {"since": since or dt.datetime.now(dt.timezone.utc), "opts": opts, "pause": pause}

def urgent_since():
    flag = read_flag()
    return flag["since"] if flag else None

def write_flag(on, opts=(), since=None, pause=None, message=None, reset_state=False):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if on:
        parts = ["on", (since or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), *opts]
        if pause:
            parts.append("pause=" + pause.isoformat(timespec="seconds"))
        line = " ".join(parts)
    else:
        line = "off"
    os.makedirs("state", exist_ok=True)
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write(line + "\n")
    remove = [URGENT_STATE] if (reset_state or not on) else []
    commit_files(message or ("Urgent mode " + ("on" if on else "off")), add=[FLAG], remove=remove)

def set_urgent(on, opts=()):
    write_flag(on, opts, reset_state=True)

def wd_text(mask):
    days = [i for i, c in enumerate(mask) if c == "1"]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if len(days) == 7:
        return "any day"
    if len(days) > 1 and days == list(range(days[0], days[-1] + 1)):
        return f"{names[days[0]]}-{names[days[-1]]}"
    return ", ".join(names[i] for i in days)

def opts_text(opts):
    cfg = {w.split("=")[0]: w.split("=", 1)[1] for w in opts if "=" in w}
    parts = []
    if "days" in cfg:
        parts.append(f"next {cfg['days']} days")
    if len(cfg.get("wd", "")) == 7:
        parts.append(wd_text(cfg["wd"]))
    if "from" in cfg or "to" in cfg:
        first = int(cfg.get("from", 7))
        last = int(cfg.get("to", 21))
        parts.append(f"starts {first:02d}:00-{last:02d}:{'30' if last >= 21 else '00'}")
    if "evenings" in opts:
        parts.append("starts from 17:00 only")
    if "main" in opts:
        parts.append("main 4 locations only")
    elif cfg:
        parts.append("all locations")
    return " · ".join(parts) or "all locations, any time"

def vienna(t):
    return t.astimezone(ZoneInfo("Europe/Vienna"))

def events_text():
    try:
        lines = [l.strip() for l in open(EVENTS_FILE, encoding="utf-8") if l.strip()]
    except OSError:
        return "No event list saved yet."
    out = ["📅 SUN SET EVENTS (list from the last check)", ""]
    for l in lines:
        title, _, rest = l.partition(" | ")
        out.append(f"• {title} ({rest.replace(' / ', ' of ')} spots taken)" if rest and rest != "?" else f"• {title}")
    out += ["", "https://sunsetpadel.at/events"]
    return "\n".join(out)

def say_html(text):
    try:
        tg("sendMessage", chat_id=CHAT, text=text, parse_mode="HTML", reply_markup=keyboard(),
           disable_web_page_preview="true")
    except Exception as exc:
        print(f"WARNING: reply failed ({type(exc).__name__}).")

def meetup_fetch():
    try:
        req = urllib.request.Request(MEETUP_URL, headers={"User-Agent": "padel-watch"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"WARNING: Meetup list not reachable ({type(exc).__name__}).")
        return None

def meetup_text():
    if not MEETUP_URL:
        return "🤝 The Meetup list is not connected yet."
    try:
        req = urllib.request.Request(MEETUP_URL, headers={"User-Agent": "padel-watch"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"WARNING: Meetup list not reachable ({type(exc).__name__}).")
        return "🤝 Could not read the Meetup list right now."
    if not body.startswith("# v1"):
        return "🤝 The Meetup list address did not answer correctly."
    today = dt.datetime.now(ZoneInfo("Europe/Vienna")).date()
    rows = []
    for line in body.splitlines()[1:]:
        f = (line.split("|") + [""] * 7)[:7]
        try:
            day = dt.date.fromisoformat(f[0])
        except ValueError:
            continue
        if day >= today:
            rows.append((day, *f[1:]))
    e = html.escape
    out = ["<b><u>🤝 MEETUP · upcoming padel events</u></b>", "",]
    if not rows:
        out.append("No upcoming padel events known yet.")
    last = None
    for day, tm, title, venue, price, going, url in rows:
        if day != last:
            if last is not None:
                out.append("")
            out.append(f"<b><u>{day:%a %d.%m.}</u></b>")
            last = day
        name = f'<a href="{e(url)}">{e(title)}</a>' if url else e(title)
        out.append(f"{'⏰ ' + tm + '  ' if tm else ''}🏆 <i>{name}</i>")
        extra = [x for x in (f"📍 <b>{e(venue)}</b>" if venue else "",
                             f"💶 {e(price)}" if price else "",
                             f"👥 {e(going)} going" if going else "") if x]
        if extra:
            out.append("   " + " · ".join(extra))
    return "\n".join(out)

# ---- WhatsApp bridge: every request is signed with the shared key (the key itself is never sent) ----
def wa_configured():
    return bool(WISPBYTE_URL and WISPBYTE_KEY)

def wa_get(path, timeout=10):
    """Reads a JSON answer from the bridge. The server deletes expired requests (48 h) itself."""
    import hashlib, hmac
    ts = str(int(time.time()))
    sig = hmac.new(WISPBYTE_KEY.encode(), f"{ts}\n{path}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(WISPBYTE_URL + path,
                                 headers={"User-Agent": "padel-watch", "x-ts": ts, "x-sig": sig})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def wa_summary_text(data):
    """Telegram HTML for the open requests. Everything from WhatsApp is escaped."""
    return wa_summary(data)


# ---- WhatsApp summary layout: grouped by day, key facts first -----------------------------------
VIENNA = ZoneInfo("Europe/Vienna")
LINK_RE = re.compile(r"https://chat\.whatsapp\.com/[A-Za-z0-9]+")
WEEKDAYS = {"montag": 0, "monday": 0, "dienstag": 1, "tuesday": 1, "mittwoch": 2, "wednesday": 2,
            "donnerstag": 3, "thursday": 3, "freitag": 4, "friday": 4, "samstag": 5, "saturday": 5,
            "sonntag": 6, "sunday": 6}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12}
VENUES = r"padeldome|the dome|padelzone|\bpz\b|europahalle|racketworld|playpadel|sporthotel|alterlaa|stadlau|erdberg|süßenbrunn|süssenbrunn|alte donau"


def _clean_line(s):
    return re.sub(r"^[^\w€]+", "", s).strip()


def parse_day(text, today):
    """The day a post is about (a date) or None. today = date in Vienna."""
    t = text.lower()
    for m in re.finditer(r"\b(\d{1,2})\.\s?(\d{1,2})\.(\d{4}|\d{2})?(?!\d)", t):
        d, mo = int(m.group(1)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        y = int(m.group(3)) if m.group(3) else today.year
        y += 2000 if y < 100 else 0
        try:
            day = dt.date(y, mo, d)
        except ValueError:
            continue
        if not m.group(3) and day < today - dt.timedelta(days=5):
            day = dt.date(y + 1, mo, d)
        return day
    m = re.search(r"\b(\d{1,2})\.?\s+(jan|feb|mar|mär|apr|may|mai|jun|jul|aug|sep|oct|okt|nov|dec|dez)[a-zä]*", t)
    if m:
        try:
            day = dt.date(today.year, MONTHS[m.group(2)], int(m.group(1)))
            return day if day >= today - dt.timedelta(days=5) else day.replace(year=today.year + 1)
        except ValueError:
            pass
    if re.search(r"\b(heute|today)\b", t):
        return today
    if re.search(r"(?<!guten )\b(morgen|tomorrow)\b", t):
        return today + dt.timedelta(days=1)
    for name, wd in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            return today + dt.timedelta(days=(wd - today.weekday()) % 7)
    return None


def _hm(h, m):
    return f"{int(h)}:{m}" if m else f"{int(h)}"


def parse_time(text):
    """First time range like 20-22, 17:30-19:30, 13 bis 15 Uhr; else a single time; else None. Returns (label, start_minutes)."""
    for m in re.finditer(r"(?<![\d.,:])(\d{1,2})(?:[:.]([0-5]\d))?\s*(?:uhr)?\s*(?:-|–|—|bis|to)\s*(\d{1,2})(?:[:.]([0-5]\d))?(?![\d,])", text.lower()):
        h1, h2 = int(m.group(1)), int(m.group(3))
        if 6 <= h1 <= 23 and 0 <= h2 <= 24 and h1 != h2:
            start = h1 * 60 + int(m.group(2) or 0)
            return f"{_hm(h1, m.group(2))}–{_hm(h2, m.group(4))}", start
    m = re.search(r"(?<![\d.,])(\d{1,2}):([0-5]\d)(?![\d.])|(?<![\d.,])(\d{1,2})\s*uhr", text.lower())
    if m:
        h, mi = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), None)
        if 6 <= int(h) <= 23:
            return _hm(h, mi if mi and mi != "00" else None), int(h) * 60 + int(mi or 0)
    return None, 9999


def parse_place(text):
    for line in text.splitlines():
        if re.search(r"^[\s]*[📍📌]", line):
            return (_clean_line(line).split(',')[0])[:60] or None
    for line in text.splitlines():
        if re.search(VENUES, line.lower()) and len(line) < 80:
            return _clean_line(re.sub(r"(?i)^(ort|location|wo)\s*:\s*", "", _clean_line(line))).split(',')[0][:60]
    return None


def parse_level(text):
    for line in text.splitlines():
        if re.search(r"^[\s]*[📈🎯]", line) or re.match(r"(?i)\s*level\s*:", line):
            return re.sub(r"(?i)^level\s*:\s*", "", _clean_line(line))[:50] or None
    m = re.search(r"(?i)\b((?:low|mid|high)[\s–/-]*(?:mid|high)?[\s–/-]*(?:advanced|adv|intermediate|exp\w*)|anfänger\w*|beginner|starter|c[12])\b", text)
    return m.group(1) if m else None


def group_html(name, link):
    g = html.escape(str(name or "Unknown"))
    link = str(link or "")
    return f'<a href="{link}">{g}</a>' if LINK_RE.fullmatch(link) else g


def ago(minutes):
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} min ago"
    return f"{int(minutes // 60)} h ago"


def day_header(day, today):
    label = day.strftime("%a %d.%m.")
    if day == today:
        return f"TODAY · {label}"
    if day == today + dt.timedelta(days=1):
        return f"TOMORROW · {label}"
    return label


def wa_summary(data, now=None, limit=3900):
    now = now or dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(VIENNA)
    today = local.date()
    items, hidden = [], 0
    for m in data.values():
        text = str(m.get("text", ""))
        day = parse_day(text, today)
        if day and day < today:
            hidden += 1                     # the game day has passed
            continue
        label, start = parse_time(text)
        items.append({"m": m, "text": text, "day": day, "time": label, "start": start,
                      "place": parse_place(text), "level": parse_level(text)})
    items.sort(key=lambda x: (x["day"] is None, x["day"] or today, x["start"]))

    head = f"🎾 <b>Padel requests from WhatsApp · {len(items)} open</b>\nUpdated {local:%H:%M}"
    blocks, last = [], "-"
    n = 0
    for it in items:
        m = it["m"]
        key = it["day"]
        if key != last:
            title = day_header(key, today) if key else "OTHER · day not recognised"
            blocks.append(f"\n<b>━━ {html.escape(title)} ━━</b>")
            last = key
        n += 1
        facts = " · ".join(x for x in (f"⏰ {html.escape(it['time'])}" if it["time"] else "",
                                       f"📍 {html.escape(it['place'])}" if it["place"] else "") if x)
        lines = [f"<b>{n}.</b> {facts}" if facts else f"<b>{n}.</b>"]
        who = f"👤 {html.escape(str(m.get('sender', 'Unknown')))}"
        ts = m.get("timestamp")
        if ts and not m.get("timestampGuess"):
            posted = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc)
            mins = (now - posted).total_seconds() / 60
            who += f" · posted {posted.astimezone(VIENNA):%H:%M} ({ago(mins)})" + (" 🆕" if mins < 60 else "")
        lines.append(who)
        if it["level"]:
            lines.append(f"📈 {html.escape(it['level'])}")
        known = sum(bool(x) for x in (it["time"], it["place"], it["level"]))
        flat = re.sub(r"\s+", " ", it["text"]).strip()
        cut = 90 if known >= 2 else 300
        lines.append(f"<i>“{html.escape(flat[:cut])}{'…' if len(flat) > cut else ''}”</i>")
        groups = [group_html(m.get("group"), m.get("link"))] + [
            group_html(a.get("name"), a.get("link")) for a in (m.get("also") or []) if isinstance(a, dict)]
        shown = " + ".join(groups[:3]) + (f" + {len(groups) - 3} more" if len(groups) > 3 else "")
        if m.get("queued"):
            shown += " · 🌙 in the morning digest"
        lines.append(f"👥 {shown}")
        blocks.append(("\n" if not blocks[-1].startswith("\n<b>━━") else "") + "\n".join(lines))
    foot = f"\n\n<i>{hidden} older request(s) hidden (day already passed)</i>" if hidden else ""
    out, used = [head], len(head) + len(foot)
    for i, b in enumerate(blocks):
        if used + len(b) + 1 > limit:
            out.append(f"\n…and more (list too long)")
            break
        out.append(b)
        used += len(b) + 1
    return "\n".join(out) + foot

def norm(text):
    text = re.sub(r"[^\w\s/+.-]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()

def read_commands(wait=0):
    res = tg("getUpdates", timeout=wait, _timeout=wait + 15,
             allowed_updates=json.dumps(["message", "callback_query"]))
    updates = res.get("result", [])
    cmds, other, last_id, now = [], False, 0, time.time()
    for u in updates:
        last_id = max(last_id, u["update_id"])
        cb = u.get("callback_query")
        if cb:
            if str((cb.get("message") or {}).get("chat", {}).get("id")) != str(CHAT):
                continue
            try:
                tg("answerCallbackQuery", callback_query_id=cb["id"])
            except Exception:
                pass
            mid = (cb.get("message") or {}).get("message_id")
            data = cb.get("data") or ""
            if data in RENEW_KEYS:                     # ✅ in the pinned renewals message (keeps its buttons)
                RENEW.append(RENEW_KEYS[data])
                continue
            if mid and data[:1] in WIZ_KINDS and (data == "x" or data[1:2] == ":"):
                WIZ.append((mid, data))
                continue
            if mid:
                try:
                    tg("editMessageReplyMarkup", chat_id=CHAT, message_id=mid,
                       reply_markup=json.dumps({"inline_keyboard": []}))
                except Exception:
                    pass
            if cb.get("data") in CALLBACKS:
                cmds.append(CALLBACKS[cb["data"]])
            continue
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue
        if now - m.get("date", 0) > MAX_AGE_SEC:
            continue
        text = norm(m.get("text"))
        if text in COMMANDS:
            cmds.append(COMMANDS[text])
        elif text:
            TEXTS.append(str(m.get("text")))
            other = True
    if updates:
        tg("getUpdates", offset=last_id + 1, limit=1, timeout=0)
    return cmds, other

def run_times(workflow):
    runs = gh(f"actions/workflows/{workflow}/runs?per_page=8").get("workflow_runs", [])
    done = [r for r in runs if r.get("status") == "completed"]
    ok = next((r for r in done if r.get("conclusion") == "success"), None)
    t = dt.datetime.fromisoformat(ok["updated_at"].replace("Z", "+00:00")) if ok else None
    running = next((r for r in runs if r.get("status") == "in_progress"), None)
    if running and running.get("run_started_at"):
        started = dt.datetime.fromisoformat(running["run_started_at"].replace("Z", "+00:00"))
        if t is None or started > t:
            t = started
    failed = bool(done) and done[0].get("conclusion") not in ("success", "cancelled", "skipped")
    return t, failed

def stamp(t, now=None):
    return f"{vienna(t):%a %d.%m. %H:%M}"

# ==== THE THREE PINNED MESSAGES ===================================================================
# 📌 status (what is watched, updated … ago) · 📌 renewals (what must be renewed when) · 📌 problems
# They are edited in place (no new messages, no sound). New problems and due renewals send ONE short
# notification each, which is deleted again when the problem is solved or the item renewed.
PINNED_FILE = "state/pinned_messages.txt"
RENEWALS_FILE = "state/renewals.txt"
PROBLEMS_FILE = "state/problems.txt"
CYCLE_DAYS = W.get("renewals", "cycle_days", 14)
GITHUB_TOKEN_EXPIRES = _date(W.get("renewals", "github_token_expires", W.get("bot", "token_expires", "2026-12-12")),
                             dt.date(2026, 12, 12))
LINKS = {
    "github_token": "https://github.com/settings/personal-access-tokens",
    "wispbyte": "https://wispbyte.com/client/servers",
    "server_key": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/settings/secrets/actions",
    "bot_token": "https://t.me/BotFather",
    "cron": "https://console.cron-job.org/jobs",
    "google": "https://script.google.com/home",
}
RENEW = []        # renewal buttons pressed in this run
TEXTS = []        # free texts sent in this run (the new GitHub token date)


def _json_read(path):
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _json_write(path, data):
    os.makedirs("state", exist_ok=True)
    text = json.dumps(data, indent=1, sort_keys=True) + "\n"
    try:
        if open(path, encoding="utf-8").read() == text:
            return False
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def time_ago(t, now):
    """3 min ago · 2 h ago · yesterday 18:05 · Mon 23.11."""
    if t is None:
        return "not run yet"
    mins = (now - t).total_seconds() / 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{int(mins)} min ago"
    if mins < 24 * 60:
        return f"{int(mins // 60)} h ago"
    if mins < 48 * 60:
        return f"yesterday {vienna(t):%H:%M}"
    return f"{vienna(t):%a %d.%m.}"


def is_quiet(now):
    h = vienna(now).hour
    return h >= QUIET_FROM or h < QUIET_TO


def _count_lines(path, skip_header=True, today=None):
    try:
        lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return None
    if skip_header:
        lines = [l for l in lines if not l.startswith("#")]
    if today:
        lines = [l for l in lines if l[:10] >= today]
    return len(lines)


def gather(now):
    """Everything the three messages show, read once per update."""
    info = {"now": now, "flag": read_flag(), "watchers": [], "mutes": W.mute_read()}
    today = vienna(now).date().isoformat()
    counts = {
        "sunset_events.yml": (_count_lines(EVENTS_FILE, skip_header=False), "event", "events"),
        "tournaments.yml": (_count_lines("state/tournaments.txt", today=today), "open", "open"),
        "free_courts.yml": (_count_lines("state/free_courts.txt"), "window", "windows"),
    }
    for label, workflow, limit in WATCHERS:
        w = {"label": label, "workflow": workflow, "cadence": CADENCE[workflow], "t": None, "failed": False,
             "error": None, "muted": info["mutes"].get(MUTE_KEY.get(workflow, "")), "health": HEALTH_KEY.get(workflow)}
        n, one, many = counts.get(workflow, (None, "", ""))
        w["count"] = None if n is None else f"{n} {one if n == 1 else many}"
        try:
            w["t"], w["failed"] = run_times(workflow)
        except Exception as exc:  # noqa: BLE001
            w["error"] = type(exc).__name__
        if workflow == "free_courts.yml":
            flag = info["flag"]
            watching = flag and not is_quiet(now) and not (flag["pause"] and flag["pause"] > now)
            limit = 15 if watching else None
        w["stale"] = (limit is not None and not w["muted"] and w["error"] is None
                      and (w["t"] is None or (now - w["t"]).total_seconds() / 60 > limit))
        info["watchers"].append(w)
    # Meetup (Google script)
    m = {"configured": bool(MEETUP_URL), "muted": info["mutes"].get("meetup"), "t": None, "count": None, "ok": False}
    if m["configured"] and not m["muted"]:
        body = meetup_fetch()
        if body and body.startswith("# v1"):
            m["ok"] = True
            hit = re.search(r"checked=(\S+)", body.splitlines()[0])
            try:
                m["t"] = dt.datetime.fromisoformat(hit.group(1).replace("Z", "+00:00")) if hit else None
            except ValueError:
                m["t"] = None
            m["count"] = len([l for l in body.splitlines()[1:] if l.strip()])
    info["meetup"] = m
    # WhatsApp bridge
    wa = {"configured": wa_configured(), "state": None, "n": 0, "queued": 0}
    if wa["configured"]:
        try:
            data = wa_get("/status", timeout=5)
            wa["state"] = str(data.get("whatsapp", "connected"))
            wa["n"] = int(data.get("activeGames", 0) or 0)
            wa["queued"] = int(data.get("queued", 0) or 0)
        except Exception:  # noqa: BLE001
            wa["state"] = "unreachable"
    info["wa"] = wa
    try:
        info["urgent_t"], info["urgent_failed"] = run_times(WORKFLOW)
    except Exception:  # noqa: BLE001
        info["urgent_t"], info["urgent_failed"] = None, False
    return info


# ---- 📌 1 · status -------------------------------------------------------------------------------
def status_text(info):
    now = info["now"]
    lines = ["📌 <b>PADEL WATCH · status</b>", ""]
    for w in info["watchers"]:
        if w["muted"]:
            when = f"🔕 muted until {stamp(w['muted'])}"
        elif w["error"]:
            when = "❓ could not read"
        else:
            when = ("⚠️ last run failed · " if w["failed"] else "") + ("⚠️ " if w["stale"] else "") + "updated " + time_ago(w["t"], now)
            when = when.replace("updated not run yet", "not run yet")
        count = f" · {w['count']}" if w["count"] else ""
        lines.append(f"{w['label']} — {when}{count} ({w['cadence']})")
    m = info["meetup"]
    if m["muted"]:
        lines.append(f"🤝 Meetup — 🔕 muted until {stamp(m['muted'])}")
    elif not m["configured"]:
        lines.append("🤝 Meetup — not connected")
    elif not m["ok"]:
        lines.append(f"🤝 Meetup — ⚠️ could not read the list ({CADENCE['meetup']})")
    else:
        late = m["t"] is None or (now - m["t"]).total_seconds() > 20 * 60
        count = f" · {m['count']} event{'' if m['count'] == 1 else 's'}" if m["count"] is not None else ""
        lines.append(f"🤝 Meetup — {'⚠️ ' if late else ''}updated {time_ago(m['t'], now)}{count} ({CADENCE['meetup']})")
    wa = info["wa"]
    req = f"{wa['n']} open request{'' if wa['n'] == 1 else 's'}"
    if not wa["configured"]:
        lines.append("📱 WhatsApp — not connected")
    elif wa["state"] == "logged_out":
        lines.append("📱 WhatsApp — ⚠️ logged out · scan QR code")
    elif wa["state"] == "unreachable":
        lines.append("📱 WhatsApp — ⚠️ server not reachable")
    elif wa["state"] == "reconnecting":
        lines.append(f"📱 WhatsApp — ⚠️ reconnecting · {req}")
    else:
        lines.append(f"📱 WhatsApp — live tracking · {req}"
                     + (f" · 🌙 {wa['queued']} waiting for the morning" if wa["queued"] else ""))
    flag = info["flag"]
    if not flag:
        lines.append("🚨 Urgent search — off")
    else:
        ends = vienna(flag["since"] + dt.timedelta(hours=URGENT_MAX_HOURS))
        lines.append(f"🚨 Urgent search — ON until {ends:%a %d.%m. %H:%M}")
        if flag["pause"] and flag["pause"] > now:
            nxt = f"paused until {stamp(flag['pause'])}"
        elif is_quiet(now):
            nxt = f"resting at night until {QUIET_TO:02d}:00"
        else:
            t = info.get("urgent_t")
            wait = URGENT_BACKOFF_SEC if info.get("urgent_failed") else URGENT_EVERY_SEC
            left = None if t is None else int(((t - now).total_seconds() + wait) // 60)
            nxt = "next check any moment" if left is None or left <= 0 else f"next check in ~{left} min"
        lines.append(f"      {html.escape(opts_text(flag['opts']))} · {nxt}")
    return "\n".join(lines)


# ---- 📌 2 · renewals ------------------------------------------------------------------------------
def renewals_state(today):
    data = _json_read(RENEWALS_FILE)
    changed = False
    for k in ("whatsapp", "wispbyte"):            # the countdown starts on the first day
        if not data.get(k):
            data[k] = today.isoformat()
            changed = True
    return data, changed


def renewal_items(data, today):
    """[(key, due date, days left)] for the items with a date."""
    gh_date = GITHUB_TOKEN_EXPIRES
    try:
        gh_date = max(gh_date, dt.date.fromisoformat(data.get("github_token", "")))
    except ValueError:
        pass
    items = []
    for k in ("whatsapp", "wispbyte"):
        try:
            due = dt.date.fromisoformat(data[k]) + dt.timedelta(days=CYCLE_DAYS)
        except (KeyError, ValueError):
            due = today + dt.timedelta(days=CYCLE_DAYS)
        items.append((k, due, (due - today).days))
    items.append(("github_token", gh_date, (gh_date - today).days))
    return sorted(items, key=lambda x: x[1])


def _due_mark(key, left):
    warn = 14 if key == "github_token" else 4
    if left <= 1 or (key == "github_token" and left <= 3):
        return "🔴 "
    return "⚠️ " if left <= warn else ""


def renewals_text(data, today):
    a = lambda key, text: f'<a href="{LINKS[key]}">{text}</a>'
    lines = ["📌 <b>PADEL WATCH · renewals</b>", ""]
    for key, due, left in renewal_items(data, today):
        mark = _due_mark(key, left)
        if key == "whatsapp":
            when = f"overdue since {due:%a %d.%m.}" if left < 0 else f"open WhatsApp on the spare phone by {due:%a %d.%m.}"
            lines.append(f"📱 WhatsApp login — {mark}{when}")
        elif key == "wispbyte":
            when = f"overdue since {due:%a %d.%m.}" if left < 0 else f"log in by {due:%a %d.%m.}"
            lines.append(f"🖥 {a('wispbyte', 'Wispbyte server')} — {mark}{when}\n      otherwise the WhatsApp bridge stops")
        else:
            if left < 0:
                when = f"expired on {due:%a %d.%m.} — the checks have stopped"
            elif left == 0:
                when = f"expires TODAY · {due:%a %d.%m.}"
            else:
                when = f"in {left} day{'s' if left != 1 else ''} · {due:%a %d.%m.%Y}"
            lines.append(f"🔑 {a('github_token', 'GitHub token')} — {mark}{when}\n      used by cron-job.org to start the checks")
    lines += [f"🔐 {a('server_key', 'Server key')} — never expires",
              f"🤖 {a('bot_token', 'Telegram bot token')} — never expires",
              f"⏰ {a('cron', 'cron-job.org')} — never expires",
              f"🤝 {a('google', 'Google script')} — never expires"]
    return "\n".join(lines)


RENEW_BUTTONS = {"whatsapp": ("✅ WhatsApp opened", "rn:wa"), "wispbyte": ("✅ Logged in to Wispbyte", "rn:wb"),
                 "github_token": ("✅ GitHub token renewed", "rn:gh")}
RENEW_KEYS = {"rn:wa": "whatsapp", "rn:wb": "wispbyte", "rn:gh": "github_token"}


def renewals_markup(data, today):
    row = [{"text": RENEW_BUTTONS[k][0], "callback_data": RENEW_BUTTONS[k][1]}
           for k, _, left in renewal_items(data, today) if _due_mark(k, left)]
    return {"inline_keyboard": [row[i:i + 2] for i in range(0, len(row), 2)]}


def renewal_reminders(data, now):
    """At 09:00: one notification for due items (deleted when you press ✅). Returns True when data changed."""
    local = vienna(now)
    if local.hour != 9:
        return False
    changed = False
    sent = data.setdefault("sent", [])
    for key, due, left in renewal_items(data, local.date()):
        days = REMIND_DAYS if key == "github_token" else (2, 0)
        if left not in days:
            continue
        tag = f"{key}:{due.isoformat()}:{left}"
        if tag in sent:
            continue
        what = {"whatsapp": "open WhatsApp on the spare phone (otherwise the bridge is logged out)",
                "wispbyte": "log in to Wispbyte (otherwise the WhatsApp bridge stops)",
                "github_token": "renew the GitHub token for cron-job.org (otherwise the checks stop)"}[key]
        when = "today" if left == 0 else ("tomorrow" if left == 1 else f"within {left} days")
        mid = W.telegram_send(f"🔔 Reminder: {what} — {when}.\nAfterwards press ✅ in the pinned 'renewals' message. "
                              "This reminder then disappears.")
        sent.append(tag)
        if mid:
            data.setdefault("reminders", {}).setdefault(key, []).append(mid)
        changed = True
    data["sent"] = sent[-40:]
    return changed


def renew_done(data, key, today, new_date=None):
    """A ✅ button (or the new token date): restart the countdown and remove its reminders."""
    if key == "github_token":
        data["github_token"] = new_date.isoformat()
    else:
        data[key] = today.isoformat()
    for mid in data.get("reminders", {}).pop(key, []):
        W.telegram_resolve(mid, "✅ Done.")


def parse_expiry(text, today):
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text) or re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if not m:
        return None
    try:
        a, b, c = (int(x) for x in m.groups())
        d = dt.date(a, b, c) if a > 31 else dt.date(c, b, a)
    except ValueError:
        return None
    return d if d > today else None


def handle_renewals(data, now):
    """Button presses and the typed date. Returns True when data changed."""
    today = vienna(now).date()
    changed = False
    for key in RENEW:
        if key == "github_token":
            data["await_date"] = now.isoformat(timespec="seconds")
            say("🔑 Send me the new expiry date of the GitHub token, for example 12.03.2027 "
                "(it is shown on the token page on GitHub).")
        else:
            renew_done(data, key, today)
            nxt = dt.date.fromisoformat(data[key]) + dt.timedelta(days=CYCLE_DAYS)
            say(f"✅ Noted. Next time by {nxt:%a %d.%m.}")
        changed = True
    RENEW.clear()
    waiting = data.get("await_date")
    if waiting and TEXTS:
        try:
            fresh = (now - dt.datetime.fromisoformat(waiting)).total_seconds() < 30 * 60
        except ValueError:
            fresh = False
        if fresh:
            day = next((d for d in (parse_expiry(t, today) for t in TEXTS) if d), None)
            if day:
                renew_done(data, "github_token", today, day)
                data.pop("await_date", None)
                say(f"✅ Saved: the GitHub token now expires {day:%a %d.%m.%Y}. "
                    "Remember to put the new token into cron-job.org.")
            else:
                say("Please send the date like this: 12.03.2027")
            changed = True
        else:
            data.pop("await_date", None)
            changed = True
    TEXTS.clear()
    return changed


# ---- 📌 3 · problems ------------------------------------------------------------------------------
def find_problems(info, pstate):
    """{key: (label, what happened, what to do)} for everything that is broken right now."""
    now = info["now"]
    out = {}
    for w in info["watchers"]:
        h = W.health_read(w["health"]) if w["health"] and not w["muted"] else None
        if h and h["fails"] >= 2:
            problem, action = W.explain(h["detail"] or "no data found")
            out[w["health"]] = (w["label"], problem, action)
        elif w["stale"]:
            out["late_" + (w["health"] or w["workflow"])] = (
                w["label"], f"did not run for a while (last run {time_ago(w['t'], now)})" if w["t"] else "has not run yet",
                "Check that its job is active on cron-job.org; if the GitHub token expired, renew it.")
    for key, a in W._alerts_read().items():
        out["alert_" + key] = ("🤖 Telegram bot", a.get("problem", "unexpected error"), "Press the button again; if it repeats, send the log to Claude.")
    wa = info["wa"]
    if wa["state"] == "unreachable":
        since = pstate.get("wa_down_since") or now.isoformat(timespec="seconds")
        pstate["wa_down_since"] = since
        if (now - dt.datetime.fromisoformat(since)).total_seconds() > 30 * 60:
            out["wa_down"] = ("📱 WhatsApp bridge", "the Wispbyte server does not answer",
                              "Open the Wispbyte console and press Start if the server is off.")
    else:
        pstate.pop("wa_down_since", None)
    if wa["state"] == "logged_out":
        out["wa_logout"] = ("📱 WhatsApp bridge", "WhatsApp logged the bridge out",
                            "Open the Wispbyte console and scan the QR code with the spare phone.")
    m = info["meetup"]
    if m["configured"] and not m["muted"] and m["ok"] and m["t"] and (now - m["t"]).total_seconds() > 30 * 60:
        out["meetup_late"] = ("🤝 Meetup", f"the Google script did not run for a while (last run {time_ago(m['t'], now)})",
                              "Open script.google.com → your project → Executions and look for errors.")
    return out


def problems_text(problems, pstate, now):
    lines = ["📌 <b>PADEL WATCH · problems</b>", ""]
    if not problems:
        return "\n".join(lines + ["✅ No problems. Everything works."])
    lines[0] += f"   {len(problems)} open"
    opened = pstate.get("open", {})
    for key, (label, problem, action) in problems.items():
        since = opened.get(key, {}).get("since")
        when = f" · since {vienna(dt.datetime.fromisoformat(since)):%H:%M}" if since else ""
        lines += [f"⚠️ {label}{when}", f"   {html.escape(problem[0].upper() + problem[1:])}.",
                  f"   What to do: {html.escape(action)}", ""]
    return "\n".join(lines).rstrip()


def problem_notes(problems, pstate, now):
    """One notification per new problem (held from 23:00 to 07:00), deleted when it is solved.
    Returns True when pstate changed."""
    opened = pstate.setdefault("open", {})
    changed = False
    for key in [k for k in opened if k not in problems]:           # solved
        note = opened.pop(key).get("note")
        if note:
            W.telegram_resolve(note, "✅ Solved.")
        changed = True
    for key, (label, problem, action) in problems.items():
        entry = opened.get(key)
        if entry is None:
            entry = opened[key] = {"since": now.isoformat(timespec="seconds"), "note": None,
                                   "pending": not key.startswith("alert_")}   # bot alerts already sent their own message
            changed = True
        if entry.get("pending") and not is_quiet(now):
            entry["note"] = W.telegram_send(
                f"⚠️ {label}: {problem}.\nWhat to do: {action}\n\nDetails in the pinned 'problems' message. "
                "This message disappears when it is solved.")
            entry["pending"] = False
            changed = True
    return changed


# ---- showing and pinning --------------------------------------------------------------------------
def pinned_ids():
    ids = _json_read(PINNED_FILE)
    if "status" not in ids:                        # the status message from before the rename
        try:
            ids["status"] = int(open(STATUS_FILE, encoding="utf-8").read().strip())
        except (OSError, ValueError):
            pass
    return ids


def pinned_show(ids, kind, text, markup=None, create=False):
    """Edits the pinned message of this kind; creates and pins it when it is missing.
    Returns True when a new message was created (its id must be saved)."""
    params = dict(chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview="true",
                  reply_markup=json.dumps(markup or {"inline_keyboard": []}))
    mid = None if create else ids.get(kind)
    if mid:
        try:
            tg("editMessageText", message_id=mid, **params)
            return False
        except urllib.error.HTTPError as exc:
            low = (exc.read().decode("utf-8", "ignore") if hasattr(exc, "read") else "").lower()
            if "not modified" in low:
                return False
            if not ("not found" in low or "can't be edited" in low or "invalid" in low):
                return False                                       # temporary error: next time
    res = tg("sendMessage", **params)
    ids[kind] = res["result"]["message_id"]
    try:
        tg("pinChatMessage", chat_id=CHAT, message_id=ids[kind], disable_notification="true")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not pin the {kind} message ({type(exc).__name__}).")
    return True


def update_status(force=False, create=False):
    """Refreshes the three pinned messages (about every 5 minutes, or at once after a button)."""
    now = dt.datetime.now(dt.timezone.utc)
    if not force and vienna(now).minute % STATUS_EVERY_MIN > 1:
        return
    try:
        info = gather(now)
        today = vienna(now).date()
        save = []
        rdata, rchanged = renewals_state(today)
        rchanged = handle_renewals(rdata, now) or rchanged
        rchanged = renewal_reminders(rdata, now) or rchanged
        pstate = _json_read(PROBLEMS_FILE)
        before = json.dumps(pstate, sort_keys=True)
        problems = find_problems(info, pstate)
        problem_notes(problems, pstate, now)
        ids = pinned_ids()
        new_ids = False
        # order: problems, renewals, status — the status is pinned last, so it shows at the top
        new_ids |= pinned_show(ids, "problems", problems_text(problems, pstate, now))
        new_ids |= pinned_show(ids, "renewals", renewals_text(rdata, today), renewals_markup(rdata, today))
        new_status = pinned_show(ids, "status", status_text(info), create=create)
        if new_ids and not new_status and ids.get("status"):
            try:                                   # keep the status on top of the pinned bar
                tg("pinChatMessage", chat_id=CHAT, message_id=ids["status"], disable_notification="true")
            except Exception:  # noqa: BLE001
                pass
        new_ids |= new_status
        if new_ids and _json_write(PINNED_FILE, ids):
            save.append(PINNED_FILE)
        if rchanged and _json_write(RENEWALS_FILE, rdata):
            save.append(RENEWALS_FILE)
        if json.dumps(pstate, sort_keys=True) != before and _json_write(PROBLEMS_FILE, pstate):
            save.append(PROBLEMS_FILE)
        if save:
            commit_files("Pinned messages updated", add=save)
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
        # A slow or unreachable network is temporary: the next refresh tries again, no message to you.
        print(f"WARNING: pinned messages not updated this time, network problem ({type(exc).__name__}).")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: pinned messages failed ({type(exc).__name__}: {exc}).")
        W.annotate("Telegram bot · pinned messages", exc)


def process(cmds, other):
    started_now = False
    failed = False
    wiz_ops = list(WIZ)
    WIZ.clear()

    for cmd in dict.fromkeys(cmds):
        try:
            if cmd == "check":
                dispatch("report")
                say("🔎 Checking all locations now. The answer follows in about 3 minutes.")
            elif cmd == "urgent":
                wiz_send_first()
            elif cmd in ("start_all", "start_evenings", "start_main"):
                opts = {"start_all": [], "start_evenings": ["evenings"], "start_main": ["main"]}[cmd]
                flag = read_flag()
                if flag and flag["opts"] == opts:
                    say(f"🚨 Urgent mode is already on ({opts_text(opts)}).")
                else:
                    set_urgent(True, opts)
                    dispatch("urgent")
                    started_now = True
                    say(f"🚨 Urgent mode {'switched' if flag else 'ON'}: {opts_text(opts)}.")
            elif cmd == "extend":
                flag = read_flag()
                if not flag:
                    say("Urgent mode is not running.")
                else:
                    write_flag(True, flag["opts"], pause=flag["pause"], message="Urgent mode extended")
                    ends = vienna(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=URGENT_MAX_HOURS))
                    say(f"⏱ Urgent mode extended. It now ends {ends:%a %d.%m. %H:%M}.")
                    started_now = True
            elif cmd == "pause":
                flag = read_flag()
                now_utc = dt.datetime.now(dt.timezone.utc)
                if not flag:
                    say("Urgent mode is not running.")
                elif flag["pause"] and flag["pause"] > now_utc:
                    write_flag(True, flag["opts"], since=flag["since"], message="Urgent alerts resumed")
                    say("▶️ Alerts resumed.")
                else:
                    until = now_utc + dt.timedelta(hours=PAUSE_HOURS)
                    write_flag(True, flag["opts"], since=flag["since"], pause=until, message="Urgent alerts paused")
                    say(f"🔕 Alerts paused until {vienna(until):%H:%M}.")
                    started_now = True
            elif cmd == "events":
                say(events_text())
            elif cmd == "meetup":
                say_html(meetup_text())
            elif cmd == "mute":
                mute_send_menu()
            elif cmd == "whatsapp_summary":
                if not wa_configured():
                    say("⚪ WhatsApp bridge is not connected (secrets WISPBYTE_URL / WISPBYTE_KEY missing).")
                else:
                    try:
                        data = wa_get("/summary")
                        if not data:
                            say("No active WhatsApp games found right now.")
                        else:
                            say_html(wa_summary_text(data))
                    except Exception as exc:
                        print(f"WARNING: WhatsApp summary failed ({type(exc).__name__}).")
                        say("❌ Could not reach the WhatsApp server (offline, or the key does not match).")
            elif cmd == "padel_events":
                dispatch("report", EVENTS_WORKFLOW)
                say("🏆 Reading the Padel-Austria tournaments...")
            elif cmd == "status":
                update_status(force=True, create=True)
                say("📌 Status updated.")
            elif cmd == "menu":
                say(HELP)
            elif cmd == "stop":
                if urgent_since():
                    set_urgent(False)
                    say("🛑 Urgent check stopped.")
                else:
                    say("Urgent mode was not running.")
        except Exception as exc:
            print(f"ERROR: could not handle '{cmd}' ({type(exc).__name__}).")
            W.annotate(f"Telegram bot · {cmd}", exc)
            failed = True
            try:
                say(f"⚠️ That did not work this time ({type(exc).__name__}). Please press the button again.")
            except Exception:  # noqa: BLE001
                pass
    awaiting = bool(_json_read(RENEWALS_FILE).get("await_date"))
    if other and not cmds and not wiz_ops and not awaiting:
        say(HELP)
    if wiz_ops:
        try:
            if wiz_run(wiz_ops):
                started_now = True
        except Exception as exc:
            print(f"ERROR: urgent menu failed ({type(exc).__name__}).")
            failed = True

    flag = read_flag()
    since = flag["since"] if flag else None
    if since and not started_now:
        try:
            age_h = (dt.datetime.now(dt.timezone.utc) - since).total_seconds() / 3600
            if age_h >= URGENT_MAX_HOURS:
                set_urgent(False)
                say("🛑 Urgent mode ended by itself.")
            else:
                run = last_run()
                busy = run and run.get("status") in ("queued", "in_progress", "waiting", "pending")
                created = run and dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                failed_last = bool(run) and run.get("conclusion") == "failure"
                due = (not run) or (
                    (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
                    >= (URGENT_BACKOFF_SEC if failed_last else URGENT_EVERY_SEC)
                )
                hour = dt.datetime.now(ZoneInfo("Europe/Vienna")).hour
                quiet = hour >= QUIET_FROM or hour < QUIET_TO
                paused = flag["pause"] and flag["pause"] > dt.datetime.now(dt.timezone.utc)
                if flag["pause"] and not paused:
                    write_flag(True, flag["opts"], since=flag["since"], message="Urgent alerts resumed")
                    say("▶️ Pause is over. Urgent checks continue.")
                if not paused and not quiet and due and not busy:
                    dispatch("urgent")
        except Exception as exc:
            print(f"ERROR: urgent check could not be started ({type(exc).__name__}).")
            failed = True
    update_status(force=bool(cmds) or bool(wiz_ops) or bool(RENEW) or bool(TEXTS))
    return failed

def main():
    if not TOKEN or not CHAT:
        sys.exit(3)
    deadline = time.time() + LISTEN_SECONDS
    failed, first = False, True
    while True:
        wait = 0 if first else int(min(20, max(1, deadline - time.time())))
        t0 = time.time()
        try:
            cmds, other = read_commands(wait)
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code not in (429, 409):
                W.annotate("Telegram bot", "telegram refused the bot token" if exc.code in (401, 404) else exc)
                sys.exit(1)
            if first and LISTEN_SECONDS <= 0:
                return
            first = False
            if time.time() >= deadline:
                break
            time.sleep(2)
            continue
        if first or cmds or other or WIZ:
            failed = process(cmds, other) or failed
        first = False
        if time.time() >= deadline - 1:
            break
        if not cmds and time.time() - t0 < 1:
            time.sleep(1)
    if failed:
        sys.exit(1)

def save_alerts(message):
    path = str(W.ALERTS_FILE)
    commit_files(message, add=[path] if os.path.exists(path) else [], remove=[] if os.path.exists(path) else [path])


def tidy_up():
    """Once after the rename: memory goes into state/, the old files are deleted from GitHub (one commit)."""
    W.prepare_state()
    old = W.leftover_files()
    if not old:
        return
    keep = [str(W.state_file(n)) for n in W.STATE_OLD_NAMES if W.state_file(n).is_file()]
    print(f"Moving memory into state/ and removing {len(old)} old file(s).")
    commit_files("Memory moved to state/, old files removed", add=keep, remove=old)


def run():
    """Runs the bot. A crash is reported ONCE in Telegram in plain words; that message is deleted again
    by the next run that works."""
    try:
        tidy_up()
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: tidy-up after the rename failed ({type(exc).__name__}); tried again next run.")
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        W.annotate("Telegram bot", exc)
        try:
            if W.alert("bot", "Telegram bot", exc):
                save_alerts("Problem reported")
        except Exception as exc2:  # noqa: BLE001
            print(f"WARNING: could not report the problem ({type(exc2).__name__}).")
        raise
    try:
        if W.alert_clear("bot", "Telegram bot"):
            save_alerts("Problem solved")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not remove the old problem message ({type(exc).__name__}).")


if __name__ == "__main__":
    run()
