"""The three pinned messages: status, renewals, problems."""
import os
os.environ.setdefault("LISTEN_SECONDS", "0")
import datetime as dt, io, json, sys, tempfile, urllib.error
os.environ.update(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="1", GITHUB_TOKEN="g", GITHUB_REPOSITORY="o/r")
sys.path.insert(0, os.getcwd())   # tests run from the repo folder
import telegram_bot as L
import shared as W
os.chdir(tempfile.mkdtemp()); os.makedirs("state", exist_ok=True)
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 23, 13, 0, tzinfo=UTC)        # 14:00 Vienna
commits, calls, notes, resolved = [], [], [], []
L.commit_files = lambda msg, add=(), remove=(): commits.append((msg, list(add)))
L.run_times = lambda wf: (NOW - dt.timedelta(minutes=3), False)
L.wa_configured = lambda: True
WA = {"whatsapp": "connected", "activeGames": 6, "queued": 0}
L.wa_get = lambda path, timeout=10: WA
L.meetup_fetch = lambda: None
L.MEETUP_URL = ""
EDIT_ERR = {"v": ""}
next_id = {"v": 100}
def fake_tg(method, **p):
    calls.append((method, p))
    if method == "sendMessage":
        next_id["v"] += 1
        return {"result": {"message_id": next_id["v"]}}
    if method == "editMessageText" and EDIT_ERR["v"]:
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(EDIT_ERR["v"].encode()))
    return {"ok": True}
L.tg = fake_tg
W.telegram_send = lambda text: (notes.append(text), 900 + len(notes))[1]
W.telegram_resolve = lambda mid, text: (resolved.append(mid), True)[1]
open("state/sunset_events.txt", "w").write("A 26.11 | 1 / 2\nB 28.11 | 5 / 5\nC 03.12 | 1 / 8\n")
open("state/tournaments.txt", "w").write("# v2\n2026-11-24|18:30|T1|P|0\n2026-11-20|18:30|old|P|0\n")
open("state/free_courts.txt", "w").write("# v3\n2667|2026-11-30|20:00|x\n")

# ---- 📌 status text
info = L.gather(NOW)
text = L.status_text(info)
print(text)
lines = text.splitlines()
check(lines[2] == "📅 Sun Set events — updated 3 min ago · 3 events (every 5 min)", "Sun Set line")
check("🏆 Padel-Austria tournaments — updated 3 min ago · 1 open (hourly)" in text, "tournaments count only upcoming")
check("🎾 Court checks — updated 3 min ago · 1 window (on request + urgent)" in text, "courts line")
check("🤝 Meetup — not connected" in text, "Meetup not connected")
check("📱 WhatsApp — live tracking · 6 open requests" in text, "WhatsApp says live tracking")
check(lines[-1] == "🚨 Urgent search — off", "urgent search is the last line")
check("✅" not in text and "Muted" not in text and "GITHUB" not in text, "no green ticks, no muted block, no token here")
open("state/urgent_settings.txt", "w").write("on 2026-11-23T12:00:00+00:00 days=10 wd=1111111 from=17 to=21\n")
text = L.status_text(L.gather(NOW))
check("🚨 Urgent search — ON until Wed 25.11. 13:00" in text and "next check in ~1 min" in text, "urgent ON line with next check")
check(text.splitlines()[-2].startswith("🚨"), "urgent stays at the end")
os.remove("state/urgent_settings.txt")
L.run_times = lambda wf: (NOW - dt.timedelta(hours=2), False)
check("📅 Sun Set events — ⚠️ updated 2 h ago" in L.status_text(L.gather(NOW)), "late watcher gets ⚠️")
L.run_times = lambda wf: (NOW - dt.timedelta(minutes=3), False)
check(L.time_ago(NOW - dt.timedelta(hours=30), NOW).startswith("yesterday"), "yesterday")

# ---- 📌 renewals
today = L.vienna(NOW).date()
data, changed = L.renewals_state(today)
check(changed and data["whatsapp"] == data["wispbyte"] == "2026-11-23", "14-day countdowns start today")
rt = L.renewals_text(data, today)
print(rt)
check(rt.index("WhatsApp login") < rt.index("Wispbyte server") < rt.index("GitHub token"), "soonest first")
check("🔑 <a href=\"https://github.com/settings/personal-access-tokens\">GitHub token</a> — in 19 days · Sat 12.12.2026" in rt, "GitHub token line with link")
check("⚠️" not in rt.split("GitHub token")[0], "WhatsApp/Wispbyte not marked 14 days ahead")
check('href="https://wispbyte.com/client/servers"' in rt and "📱 WhatsApp login —" in rt and "WhatsApp login</a>" not in rt, "Wispbyte linked, WhatsApp not")
check(rt.count("never expires") == 4 and "/o/r/settings/secrets/actions" in rt, "never-expiring items with links")
mk = L.renewals_markup(data, today)
check(mk["inline_keyboard"] == [], "no buttons while nothing is due (token in 19 days, others in 14)")
data["whatsapp"] = "2026-11-11"                                    # due Wed 25.11. = in 2 days
rt = L.renewals_text(data, today); mk = L.renewals_markup(data, today)
check("📱 WhatsApp login — ⚠️ open WhatsApp on the spare phone by Wed 25.11." in rt, "WhatsApp ⚠️ 2 days before")
check("✅ WhatsApp opened" in json.dumps(mk, ensure_ascii=False), "WhatsApp button appears")
# reminder at 09:xx Vienna, once
nine = dt.datetime(2026, 11, 23, 8, 10, tzinfo=UTC)
notes.clear()
check(L.renewal_reminders(data, nine) and len(notes) == 1 and "open WhatsApp on the spare phone" in notes[0], "reminder 2 days before at 09:00")
check(not L.renewal_reminders(data, nine) and len(notes) == 1, "no second reminder")
check(not L.renewal_reminders(data, NOW), "no reminders outside 09:00")
# ✅ button
calls.clear(); resolved.clear()
L.RENEW.append("whatsapp")
check(L.handle_renewals(data, NOW) and data["whatsapp"] == "2026-11-23", "✅ WhatsApp opened restarts the countdown")
check(resolved == [901], "its reminder is deleted")
check(any("Next time by Mon 07.12." in p.get("text", "") for m, p in calls), "confirmation with next date")
# GitHub token: button, then typed date
calls.clear()
L.RENEW.append("github_token"); L.handle_renewals(data, NOW)
check("await_date" in data and any("new expiry date" in p.get("text", "") for m, p in calls), "asks for the new date")
L.TEXTS.append("hello"); L.handle_renewals(data, NOW)
check("await_date" in data and any("like this: 12.03.2027" in p.get("text", "") for m, p in calls), "wrong text: asks again")
L.TEXTS.append("12.03.2027"); L.handle_renewals(data, NOW)
check(data["github_token"] == "2027-03-12" and "await_date" not in data, "date saved")
check("in 109 days · Fri 12.03.2027" in L.renewals_text(data, today), "renewals shows the new date")
check(L.parse_expiry("2027-03-12", today) == dt.date(2027, 3, 12) and L.parse_expiry("01.01.2020", today) is None, "date formats; past dates refused")

# ---- 📌 problems
pstate = {}
info = L.gather(NOW)
check(L.find_problems(info, pstate) == {}, "no problems")
check(L.problems_text({}, pstate, NOW).endswith("✅ No problems. Everything works."), "empty problems message")
W.health_write("sunset", {"fails": 2, "since": NOW, "warned": False, "detail": "net::ERR_CERT_COMMON_NAME_INVALID", "msg": None})
probs = L.find_problems(info, pstate)
check(list(probs) == ["sunset"] and "security certificate" in probs["sunset"][1], "watcher failing twice -> problem in plain words")
notes.clear()
L.problem_notes(probs, pstate, NOW)
check(len(notes) == 1 and "Sun Set events" in notes[0] and "disappears when it is solved" in notes[0], "one notification")
L.problem_notes(probs, pstate, NOW + dt.timedelta(minutes=5))
check(len(notes) == 1, "no repeat")
pt = L.problems_text(probs, pstate, NOW)
check("1 open" in pt and "since 14:00" in pt and "What to do: Nothing to do" in pt, "problems message lists it")
resolved.clear(); W.health_write("sunset", None)
probs = L.find_problems(L.gather(NOW), pstate); L.problem_notes(probs, pstate, NOW)
check(probs == {} and resolved == [901] and pstate["open"] == {}, "solved -> notification deleted")
# night: held until 07:00
night = dt.datetime(2026, 11, 23, 23, 30, tzinfo=UTC)               # 00:30 Vienna
WA["whatsapp"] = "logged_out"; notes.clear()
probs = L.find_problems(L.gather(night), pstate); L.problem_notes(probs, pstate, night)
check("wa_logout" in probs and notes == [] and pstate["open"]["wa_logout"]["pending"], "night: problem listed, notification held")
morning = dt.datetime(2026, 11, 24, 6, 5, tzinfo=UTC)               # 07:05 Vienna
L.problem_notes(probs, pstate, morning)
check(sum("scan the QR code" in n for n in notes) == 1, "07:00: held notification goes out")
check(any("did not run for a while (last run 10 h ago)" in n for n in notes), "late watchers are reported in plain words")
WA["whatsapp"] = "connected"
# server unreachable: only after 30 minutes
L.wa_get = lambda path, timeout=10: (_ for _ in ()).throw(OSError("down"))
p2 = {}
check(L.find_problems(L.gather(NOW), p2) == {} and "wa_down_since" in p2, "server down 0 min: not yet a problem")
check("wa_down" in L.find_problems(L.gather(NOW + dt.timedelta(minutes=31)), p2), "server down 31 min: problem")
check("📱 WhatsApp — ⚠️ server not reachable" in L.status_text(L.gather(NOW)), "status shows it too")
L.wa_get = lambda path, timeout=10: WA
L.find_problems(L.gather(NOW), p2)
check("wa_down_since" not in p2, "back online resets the timer")

# ---- creating, editing, pinning (real update cycle)
for f in ("state/pinned_messages.txt", "state/renewals.txt", "state/problems.txt"):
    if os.path.exists(f): os.remove(f)
calls.clear(); commits.clear()
L.update_status(force=True)
sends = [p for m, p in calls if m == "sendMessage"]
pins = [p["message_id"] for m, p in calls if m == "pinChatMessage"]
check(len(sends) == 3 and all(x["text"].startswith(h) for x, h in zip(sends, ["📌 <b>PADEL WATCH · problems</b>", "📌 <b>PADEL WATCH · renewals</b>", "📌 <b>PADEL WATCH · status</b>"])),
      "first run: problems, renewals, status created in this order (status pinned last = shown on top)")
check(len(pins) == 3, "all three pinned")
ids = json.load(open("state/pinned_messages.txt"))
check(set(ids) == {"problems", "renewals", "status"} and commits and "state/pinned_messages.txt" in commits[-1][1], "ids saved")
calls.clear(); commits.clear()
L.update_status(force=True)
check([m for m, p in calls] == ["editMessageText"] * 3, "next run: only edits")
EDIT_ERR["v"] = "Bad Request: message is not modified"; calls.clear()
L.update_status(force=True)
check([m for m, p in calls] == ["editMessageText"] * 3 and not commits, "unchanged: nothing else")
EDIT_ERR["v"] = "Bad Request: message to edit not found"; calls.clear()
L.update_status(force=True)
check([m for m, p in calls].count("sendMessage") == 3, "deleted messages are created again")
EDIT_ERR["v"] = ""
# the status message from before the rename is reused
os.remove("state/pinned_messages.txt"); open("state/status_message_id.txt", "w").write("55\n"); calls.clear()
L.update_status(force=True)
check(("editMessageText", 55) in [(m, p.get("message_id")) for m, p in calls], "old status message kept and edited")
check([p["message_id"] for m, p in calls if m == "pinChatMessage"][-1] == 55, "old status re-pinned last, so it stays on top")
# network trouble: no crash
def slow(method, **p): raise TimeoutError("timed out")
L.tg = slow
L.update_status(force=True)
check(True, "timeout: no crash")
if fails:
    print("FAILED:", fails); sys.exit(1)
print("ALL OK")
