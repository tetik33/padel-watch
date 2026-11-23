"""After the rename: memory is moved into state/ once, and only safe old files are deleted."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())   # tests run from the repo folder
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
import shared as W
import telegram_bot as L

os.chdir(tempfile.mkdtemp())
def put(path, text="x\n"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)

# the repo as it is today (old names) plus the uploaded new files
put("events.txt", "Perfect Match 26.11 | 20 / 20\n")
put("padel_events_state.txt", "# v2\n2026-11-24|18:30|T|P|0\n")
put("courts_state.txt", "# v3\n"); put("urgent.txt", "on 2026-11-22T18:54:35+00:00 days=10\n")
put("status_msg.txt", "1234\n"); put("health_sunset.txt", "ok\n")
for f in ("check.py", "tg_listen.py", "watchlib.py", "courts.py", "test_ev2.py", "run_tests.py",
          "whatsapp_server/index.js", "whatsapp_server/README.txt", "meetup_to_telegram.gs"):
    put(f)
for f in ("sunset_events.py", "telegram_bot.py", "shared.py", "tests/run_tests.py",
          "backup/whatsapp_server/index.js", "backup/meetup_google_script.gs"):
    put(f)
put(".github/workflows/watch.yml", "name: old\n")
# courts.py's replacement free_courts.py is NOT uploaded (for example a partial upload)

W.prepare_state()
assert open("state/sunset_events.txt").read() == "Perfect Match 26.11 | 20 / 20\n", "memory copied"
assert open("state/urgent_settings.txt").read().startswith("on "), "urgent settings copied"
assert open("state/status_message_id.txt").read().strip() == "1234"
assert os.path.exists("events.txt"), "old file kept until the bot deletes it"

# a newer memory file in state/ is never overwritten by the old one
put("state/sunset_events.txt", "NEW\n"); W.prepare_state()
assert open("state/sunset_events.txt").read() == "NEW\n"

old = set(W.leftover_files())
assert {"events.txt", "padel_events_state.txt", "courts_state.txt", "urgent.txt", "status_msg.txt",
        "health_sunset.txt", "check.py", "tg_listen.py", "watchlib.py", "test_ev2.py", "run_tests.py",
        "whatsapp_server/index.js", "whatsapp_server/README.txt", "meetup_to_telegram.gs"} <= old, old
assert "courts.py" not in old, "old program is kept while its replacement is missing"
assert not any(f.startswith(".github") for f in old), "workflow files are never touched by the bot"
assert not any(f.startswith(("state/", "tests/", "backup/")) for f in old)

# the bot does it in ONE save: new memory files added, old files removed
calls = []
L.commit_files = lambda msg, add=(), remove=(): calls.append((msg, list(add), list(remove)))
L.tidy_up()
assert len(calls) == 1 and "state/sunset_events.txt" in calls[0][1] and "events.txt" in calls[0][2]
for f in calls[0][2]:
    os.remove(f)
calls.clear(); L.tidy_up()
assert calls == [], "nothing to do the second time"
print("test_state_move OK")
