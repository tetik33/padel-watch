"""Shared helpers for all watchers: settings file, mute list, page-health tracking.

 * config.toml   all settings you may want to change (locations, weekdays, times, filters ...)
                A missing or broken file never stops anything: the built-in defaults are used.
 * mute.txt      "<watcher> <until, UTC>" lines written by the Telegram menu "Mute"
 * health_*.txt  one file per watcher: how many runs in a row could not read the website
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

CONFIG_FILE = pathlib.Path(os.environ.get("PADEL_CONFIG", "config.toml"))
MUTE_FILE = pathlib.Path("state/mute.txt")
MUTE_NAMES = ("sunset", "events", "meetup")           # watchers that can be muted ("urgent" has its own pause)
HEALTH_NAMES = {"sunset": "📅 Sun Set events", "courts": "🎾 Court checks", "events": "🏆 Padel-Austria tournaments"}

_cache = {}


# ---- settings ---------------------------------------------------------------------------------
def config():
    """The parsed config.toml ({} when it is missing or has a typing mistake)."""
    if "cfg" not in _cache:
        try:
            _cache["cfg"] = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _cache["cfg"] = {}
        except (OSError, ValueError) as exc:   # tomllib.TOMLDecodeError is a ValueError
            print(f"WARNING: {CONFIG_FILE} could not be read ({exc}). Built-in defaults are used.")
            _cache["cfg"] = {}
    return _cache["cfg"]


def reload_config():
    _cache.pop("cfg", None)


def get(section, key, default):
    """config.toml value, or the default when it is missing or has the wrong kind (number / text / list)."""
    value = config().get(section, {}).get(key)
    if value is None:
        return default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(value, bool):
        return default
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return value if isinstance(value, list) and value else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else default
    return value


def table(section, key, fields, default):
    """A list of tables like [[courts.locations]]: returns a list of dicts that contain all `fields` as text."""
    rows = config().get(section, {}).get(key)
    if not isinstance(rows, list) or not rows:
        return default
    out = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(f), str) and row.get(f) for f in fields):
            print(f"WARNING: config.toml [{section}.{key}] has an incomplete entry, defaults are used.")
            return default
        out.append(row)
    return out


WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekday_set(names, default):
    """['Mon','Tue'] -> {0, 1}. Anything unknown -> default."""
    try:
        out = {WEEKDAY_SHORT.index(str(n).strip()[:3].title()) for n in names}
    except ValueError:
        return default
    return out or default


def minutes(text, default):
    """'18:00' -> 1080"""
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return default


# ---- Telegram (plain text, never prints the token) --------------------------------------------
# ---- bot memory: all files the bot writes live in state/ ---------------------------------------
STATE_DIR = pathlib.Path("state")
# new name in state/  ->  old name in the main folder (before the files were renamed)
STATE_OLD_NAMES = {
    "sunset_events.txt": "events.txt",
    "tournaments.txt": "padel_events_state.txt",
    "free_courts.txt": "courts_state.txt",
    "urgent_reported.txt": "urgent_state.txt",
    "urgent_settings.txt": "urgent.txt",
    "status_message_id.txt": "status_msg.txt",
    "reminders.txt": "reminders.txt",
    "mute.txt": "mute.txt",
    "alerts.txt": "alerts.txt",
    "health_sunset.txt": "health_sunset.txt",
    "health_courts.txt": "health_courts.txt",
    "health_events.txt": "health_events.txt",
}
# Old program files that the new ones replace: (old file, new file that must exist before the old one is deleted)
OLD_FILES = [
    ("check.py", "sunset_events.py"), ("events_padel.py", "tournaments.py"), ("courts.py", "free_courts.py"),
    ("tg_listen.py", "telegram_bot.py"), ("watchlib.py", "shared.py"), ("wa_format.py", "telegram_bot.py"),
    ("run_tests.py", "tests/run_tests.py"), ("meetup_to_telegram.gs", "backup/meetup_google_script.gs"),
] + [(f, "tests/run_tests.py") for f in (
    "test_backoff.py", "test_config.py", "test_courts_urgent.py", "test_ev2.py", "test_health.py",
    "test_listen_loop.py", "test_meetup.js", "test_meetup_py.py", "test_mute.py", "test_status2.py", "test_tg.py",
    "test_wa_format.py", "test_whatsapp.py", "test_wizard.py")
] + [(f"whatsapp_server/{f}", "backup/whatsapp_server/index.js") for f in (
    "README.txt", "core.js", "index.js", "package.json", "test_wa_server.js", "group_links.example.json",
    "secrets.example.json", "settings.example.json")]


def state_file(name):
    return STATE_DIR / name


def prepare_state():
    """Creates state/ and, once, moves the memory of the old file names into it (copy; the old file is
    deleted later by the Telegram bot). Called at the start of every program."""
    STATE_DIR.mkdir(exist_ok=True)
    for new, old in STATE_OLD_NAMES.items():
        target, source = STATE_DIR / new, pathlib.Path(old)
        if not target.exists() and source.is_file():
            target.write_bytes(source.read_bytes())


def leftover_files():
    """Old files that can be deleted now: old memory files already moved to state/, and old program files
    whose replacement exists. (Workflow files cannot be deleted by the bot; GitHub does not allow it.)"""
    out = []
    for new, old in STATE_OLD_NAMES.items():
        if pathlib.Path(old).is_file() and (STATE_DIR / new).is_file():
            out.append(old)
    for old, new in OLD_FILES:
        if pathlib.Path(old).is_file() and pathlib.Path(new).is_file():
            out.append(old)
    return out


def _git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def save_files(message, add=(), remove=()):
    """Saves files to GitHub (used by the Telegram bot and by every workflow). If another run changed the same file at the same moment (for example the urgent
    check saved urgent_state.txt while you pressed Stop), our change is put on top of the newest version."""
    # GitHub's own bot identity (the old name "watcher" belonged to a stranger's GitHub account)
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    wanted = {}
    for f in add:                                   # remember what we want to save
        try:
            wanted[f] = open(f, "rb").read()
        except OSError:
            pass

    def stage():
        for f, data in wanted.items():
            with open(f, "wb") as fh:
                fh.write(data)
            _git("add", f)
        for f in remove:
            _git("rm", "-f", "--ignore-unmatch", "-q", f, check=False)
            try:
                os.remove(f)
            except OSError:
                pass
        if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False                            # nothing to save
        _git("commit", "-q", "-m", message)
        return True

    if not stage():
        return
    for attempt in range(4):
        if _git("push", "-q", check=False).returncode == 0:
            return
        # Someone else saved first: take the newest version and put our change on top of it.
        _git("rebase", "--abort", check=False)
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        if _git("fetch", "-q", "origin", branch, check=False).returncode != 0:
            time.sleep(3 + attempt * 3)
            continue
        _git("reset", "-q", "--hard", f"origin/{branch}")
        if not stage():
            return                                  # the newest version already has our change
        time.sleep(1 + attempt)
    raise RuntimeError("push failed")

def launch_browser(p):
    """Starts Chrome. On GitHub, Google Chrome is already installed, so no browser download is needed.
    Falls back to Playwright's own Chromium (for example on a computer without Chrome)."""
    try:
        return p.chromium.launch(channel="chrome")
    except Exception:  # noqa: BLE001
        return p.chromium.launch()


def _tg(method, **params):
    """Calls the Telegram Bot API. Returns the result, or None when it failed (never raises)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return None
    data = urllib.parse.urlencode({"chat_id": chat, **params}).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/{method}", data=data), timeout=20) as r:
                return json.loads(r.read().decode("utf-8")).get("result")
        except urllib.error.HTTPError as exc:
            print(f"WARNING: Telegram {method} refused ({exc.code}).")
            return None                       # a refusal does not get better by trying again
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: Telegram {method} failed ({type(exc).__name__}), attempt {attempt + 1}.")
            time.sleep(2)
    return None


def telegram_send(text):
    """Sends a message. Returns its message id, or None."""
    res = _tg("sendMessage", text=text, disable_web_page_preview="true")
    return res.get("message_id") if isinstance(res, dict) else None


def telegram(text):
    return telegram_send(text) is not None


def telegram_resolve(message_id, solved_text):
    """Removes a warning that is no longer true. Telegram lets a bot delete its messages for 48 hours;
    an older warning is changed to the solved text instead."""
    if _tg("deleteMessage", message_id=message_id):
        return True
    return bool(_tg("editMessageText", message_id=message_id, text=solved_text))


# ---- plain-English errors -----------------------------------------------------------------
def explain(error):
    """Turns an error (exception or text) into (what happened, what to do) in plain words."""
    t = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    low = t.lower()
    rules = [
        (("err_cert", "certificate", "ssl"),
         "the website sent a wrong security certificate (a problem on their side)",
         "Nothing to do. It is checked again at the next run."),
        (("401", "unauthorized") if "telegram" in low else ("telegram refused the bot token",),
         "Telegram refused the bot token",
         "Check the secret TELEGRAM_BOT_TOKEN (BotFather -> API Token)."),
        (("push failed", "could not save"),
         "could not save to GitHub because another check saved at the same moment",
         "Press the button again. If it keeps happening, send the log to Claude."),
        (("err_name_not_resolved", "err_connection", "err_timed_out", "err_internet", "err_address",
          "timeout", "timed out", "did not answer", "urlerror", "connectionreset", "connection refused"),
         "the website did not answer",
         "Nothing to do if it is short. If it lasts, open the website on your phone; if it works there, send the log to Claude."),
        (("no events found", "no tournaments found", "no location could be read", "looks different", "page looks"),
         "the page looks different than expected",
         "The website may have changed its layout. Send the log to Claude so the script can be adjusted."),
        (("403", "bad credentials", "resource not accessible"),
         "GitHub refused the request (the token may have expired or lacks permission)",
         "Renew the GitHub token and put it into cron-job.org; check the repo's Actions permissions."),
    ]
    for keys, problem, action in rules:
        if any(k in low for k in keys):
            return problem, action
    return f"unexpected error ({t[:80]})", "Send the log to Claude."


def annotate(label, error):
    """Marks the error on GitHub in plain words (shown on the run page instead of only "exit code 1")."""
    problem, action = explain(error)
    clean = lambda x: str(x).replace("%", "%25").replace("\r", " ").replace("\n", " ")
    print(f"::error title={clean(label)}::{clean(problem[0].upper() + problem[1:])}. What to do: {clean(action)}")
    return problem, action


def run_main(label, main):
    """Runs a program; an unexpected crash is explained in plain words on GitHub and then shown as failed."""
    try:
        return main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        annotate(label, exc)
        raise


# ---- alerts of the Telegram bot itself (one message per problem, removed when solved) -------------
ALERTS_FILE = pathlib.Path("state/alerts.txt")


def _alerts_read():
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _alerts_write(data):
    if data:
        STATE_DIR.mkdir(exist_ok=True)
        ALERTS_FILE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    elif ALERTS_FILE.exists():
        ALERTS_FILE.unlink()


def alert(key, label, error, send=None):
    """Sends ONE warning for a problem (not again while it is still in the chat). Returns True when the
    alerts file changed (the caller saves it)."""
    send = send or telegram_send
    problem, action = explain(error)
    data = _alerts_read()
    if key in data and data[key].get("problem") == problem:
        return False                              # already in the chat
    text = (f"⚠️ PADEL WATCH · {label}\n\n{problem[0].upper() + problem[1:]}.\nWhat to do: {action}\n\n"
            "This message disappears by itself when it works again.")
    if key in data and data[key].get("id"):
        telegram_resolve(data[key]["id"], f"✅ {label}: solved.")
    mid = send(text)
    if not mid:
        return False
    data[key] = {"id": mid if isinstance(mid, int) and not isinstance(mid, bool) else None,
                 "problem": problem, "since": now_utc().isoformat(timespec="seconds")}
    _alerts_write(data)
    return True


def alert_clear(key, label, resolve=None):
    """The problem is gone: the warning is removed from the chat. Returns True when the alerts file changed."""
    resolve = resolve or telegram_resolve
    data = _alerts_read()
    if key not in data:
        return False
    if data[key].get("id"):
        resolve(data[key]["id"], f"✅ {label}: solved, everything works again.")
    del data[key]
    _alerts_write(data)
    return True


# ---- mute list --------------------------------------------------------------------------------
def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def mute_read(path=None, now=None):
    """{'sunset': until, ...} for mutes that are still running."""
    now = now or now_utc()
    out = {}
    try:
        lines = (path or MUTE_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or parts[0] not in MUTE_NAMES:
            continue
        try:
            until = dt.datetime.fromisoformat(parts[1])
        except ValueError:
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=dt.timezone.utc)
        if until > now:
            out[parts[0]] = until
    return out


def muted(name, now=None):
    """Until when this watcher is muted (None = not muted)."""
    return mute_read(now=now).get(name)


def mute_text(mutes):
    return "".join(f"{n} {u.isoformat(timespec='seconds')}\n" for n, u in sorted(mutes.items()))


# ---- page health ------------------------------------------------------------------------------
def health_path(name):
    return STATE_DIR / f"health_{name}.txt"


def health_read(name):
    """None when the last run was fine, else {'fails': n, 'since': datetime, 'warned': bool, 'detail': text}."""
    try:
        text = health_path(name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("fails="):
        return None
    d = {}
    for part in text.split(" | "):
        k, _, v = part.partition("=")
        d[k] = v
    try:
        return {"fails": int(d["fails"]), "since": dt.datetime.fromisoformat(d["since"]),
                "warned": d.get("warned") == "1", "detail": d.get("detail", ""),
                "msg": int(d["msg"]) if d.get("msg", "").isdigit() else None}
    except (KeyError, ValueError):
        return None


def health_write(name, state):
    text = "ok" if state is None else (f"fails={state['fails']} | since={state['since'].isoformat(timespec='seconds')}"
                                       f" | warned={'1' if state['warned'] else '0'}"
                                       + (f" | msg={state['msg']}" if state.get("msg") else "")
                                       + f" | detail={state['detail']}")
    STATE_DIR.mkdir(exist_ok=True)
    health_path(name).write_text(text + "\n", encoding="utf-8")


def health_report(name, ok, detail="", threshold=2, send=None, now=None, resolve=None):
    """Call once per run. A run that cannot read the website counts as a failure; after `threshold` failures
    in a row you get ONE Telegram warning in plain words. When the page works again, that warning is
    deleted from the chat. Returns the message that was sent (or None).
    Without `send` nothing is sent: the Telegram bot shows the problem in its pinned 'problems' message."""
    quiet = send is None
    send = send or (lambda text: None)
    resolve = resolve or telegram_resolve
    now = now or now_utc()
    old = health_read(name)
    label = HEALTH_NAMES.get(name, name)
    if ok:
        if old is None:
            return None
        health_write(name, None)
        if old["warned"] and old.get("msg"):
            resolve(old["msg"], f"✅ Solved · {label}: the page can be read again.")
            return None
        if old["warned"]:                          # warning from before message ids were saved
            msg = f"✅ {label}: the page can be read again. Everything works as before."
            send(msg)
            return msg
        return None
    state = old or {"fails": 0, "since": now, "warned": False, "detail": "", "msg": None}
    state["fails"] += 1
    state["detail"] = detail.replace("|", "/").replace("\n", " ")[:120]
    problem, action = annotate(label, state["detail"] or "no data found")
    msg = None
    if not quiet and not state["warned"] and state["fails"] >= threshold:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        times = f"{state['fails']} time{'s' if state['fails'] != 1 else ''} in a row"
        msg = (f"⚠️ PADEL WATCH · {label}\n\n{problem[0].upper() + problem[1:]}.\nHappened {times}.\n"
               f"What to do: {action}\n"
               f"Details: {state['detail'] or 'no data found'}\n\n"
               "Until then you get no alerts from this watcher. "
               "This message disappears by itself when it works again."
               + (f"\n\nRun log: https://github.com/{repo}/actions" if repo else ""))
        r = send(msg)
        if r:
            state["warned"] = True
            state["msg"] = r if isinstance(r, int) and not isinstance(r, bool) else None
    health_write(name, state)
    return msg
