"""Saving survives a conflict: the urgent check saves urgent_state.txt while you press Stop (which deletes it)."""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())   # tests run from the repo folder
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
import telegram_bot as L

def run(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)

home = os.getcwd()
tmp = tempfile.mkdtemp()
remote, bot, other = (os.path.join(tmp, n) for n in ("remote.git", "bot", "other"))
run("git", "init", "-q", "--bare", "-b", "main", remote, cwd=tmp)
run("git", "clone", "-q", remote, other, cwd=tmp)
for k, v in (("user.name", "t"), ("user.email", "t@t")):
    run("git", "config", k, v, cwd=other)
open(os.path.join(other, "urgent.txt"), "w").write("on 2026-11-22T18:54:35+00:00 days=10\n")
open(os.path.join(other, "urgent_state.txt"), "w").write("old\n")
run("git", "add", ".", cwd=other); run("git", "commit", "-q", "-m", "start", cwd=other)
run("git", "push", "-q", "origin", "HEAD:main", cwd=other)
run("git", "clone", "-q", remote, bot, cwd=tmp)

# the urgent court check saves a new urgent_state.txt first ...
open(os.path.join(other, "urgent_state.txt"), "w").write("new courts\n")
run("git", "commit", "-q", "-am", "Update court snapshot", cwd=other); run("git", "push", "-q", cwd=other)

# ... then the bot (with the old copy) stops urgent mode: writes "off" and deletes urgent_state.txt
os.chdir(bot)
try:
    L.FLAG, L.URGENT_STATE = "urgent.txt", "urgent_state.txt"
    L.set_urgent(False)
finally:
    os.chdir(home)

check = os.path.join(tmp, "check")
run("git", "clone", "-q", remote, check, cwd=tmp)
assert open(os.path.join(check, "urgent.txt")).read().strip() == "off", "urgent mode is off on GitHub"
assert not os.path.exists(os.path.join(check, "urgent_state.txt")), "urgent_state.txt is deleted"

# saving again with nothing new does nothing and does not fail
os.chdir(bot)
try:
    L.set_urgent(False)
finally:
    os.chdir(home)
# two workflow runs save the same memory file at the same moment: the later one goes on top, nothing is lost
import shared as W
run("git", "pull", "-q", cwd=other)
open(os.path.join(other, "courts_state.txt"), "w").write("from run A\n")
run("git", "add", ".", cwd=other); run("git", "commit", "-q", "-m", "A", cwd=other); run("git", "push", "-q", cwd=other)
os.chdir(bot)
try:
    open("courts_state.txt", "w").write("from run B\n")
    W.save_files("Update court snapshot", ["courts_state.txt", "health_courts.txt"])   # health file missing: skipped
finally:
    os.chdir(home)
run("git", "pull", "-q", cwd=check)
assert open(os.path.join(check, "courts_state.txt")).read() == "from run B\n", "newest snapshot saved"
assert open(os.path.join(check, "urgent.txt")).read().strip() == "off", "earlier change kept"
print("test_save_conflict OK")
