"""Plain-English errors, warnings that disappear when the problem is solved, and the bot's own alerts."""
import contextlib, datetime as dt, io, os, sys, tempfile
sys.path.insert(0, os.getcwd())   # tests run from the repo folder
import shared as W
os.chdir(tempfile.mkdtemp()); os.makedirs("state", exist_ok=True)
T = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dt.timezone.utc)

# ---- plain-English explanations
cases = {
    "Page.goto: net::ERR_CERT_COMMON_NAME_INVALID at https://sunsetpadel.at/events": "security certificate",
    "Page.goto: net::ERR_CONNECTION_TIMED_OUT": "did not answer",
    "TimeoutError: Timeout 30000ms exceeded": "did not answer",
    "no events found on the page": "looks different",
    "RuntimeError: push failed": "another check saved",
    "telegram HTTP Error 401: Unauthorized": "bot token",
    "KeyError: 'xyz'": "unexpected error",
}
for text, want in cases.items():
    problem, action = W.explain(text)
    assert want in problem and action, (text, problem)
assert "bot token" in W.explain("telegram refused the bot token")[0]

# ---- GitHub error line is one clean line
out = io.StringIO()
with contextlib.redirect_stdout(out):
    W.annotate("Sun Set events", RuntimeError("Page.goto: net::ERR_CERT_COMMON_NAME_INVALID\nCall log: x"))
line = out.getvalue().strip()
assert line.startswith("::error title=Sun Set events::The website sent a wrong security certificate") and "\n" not in line, line
assert "What to do: Nothing to do" in line

# ---- watcher warning: sent once with its message id, DELETED when the page works again
sent, resolved = [], []
send = lambda t: (sent.append(t), 4242)[1]
resolve = lambda mid, text: (resolved.append((mid, text)), True)[1]
with contextlib.redirect_stdout(io.StringIO()):
    W.health_report("sunset", False, "Error: net::ERR_CERT_COMMON_NAME_INVALID", send=send, resolve=resolve, now=T)
    W.health_report("sunset", False, "Error: net::ERR_CERT_COMMON_NAME_INVALID", send=send, resolve=resolve, now=T)
    W.health_report("sunset", False, "Error: net::ERR_CERT_COMMON_NAME_INVALID", send=send, resolve=resolve, now=T)
assert len(sent) == 1, "one warning only"
msg = sent[0]
assert "Sun Set events" in msg and "wrong security certificate" in msg and "What to do:" in msg
assert "disappears by itself" in msg and "Details: Error: net::ERR_CERT" in msg
assert W.health_read("sunset")["msg"] == 4242, "message id is remembered"
W.health_report("sunset", True, send=send, resolve=resolve, now=T)
assert resolved == [(4242, "✅ Solved · 📅 Sun Set events: the page can be read again.")], resolved
assert len(sent) == 1, "no extra 'works again' message: the warning is simply removed"
assert W.health_read("sunset") is None

# ---- Telegram: delete, or (older than 48 h) change the text to "solved"
calls = []
def fake_tg(method, **p):
    calls.append(method)
    return None if method == "deleteMessage" else {"ok": True}
W._tg = fake_tg
assert W.telegram_resolve(1, "✅ solved") and calls == ["deleteMessage", "editMessageText"]

# ---- the bot's own alerts: one message per problem, removed by the next good run
sent.clear(); resolved.clear()
send = lambda t: (sent.append(t), 777)[1]
assert W.alert("bot", "Telegram bot", RuntimeError("push failed"), send=send) is True
assert W.alert("bot", "Telegram bot", RuntimeError("push failed"), send=send) is False, "same problem: no repeat"
assert len(sent) == 1 and "another check saved" in sent[0] and "disappears by itself" in sent[0]
assert W.ALERTS_FILE.exists()
assert W.alert_clear("bot", "Telegram bot", resolve=resolve) is True
assert resolved == [(777, "✅ Telegram bot: solved, everything works again.")]
assert not W.ALERTS_FILE.exists(), "file removed when there is no open problem"
assert W.alert_clear("bot", "Telegram bot", resolve=resolve) is False, "nothing to clear"
W.ALERTS_FILE.write_text("garbage")
assert W.alert_clear("bot", "Telegram bot", resolve=resolve) is False, "broken file is treated as empty"

# ---- a crash in a checker is explained on GitHub and the run still fails
out = io.StringIO()
def boom(): raise RuntimeError("Page.goto: net::ERR_CONNECTION_RESET")
try:
    with contextlib.redirect_stdout(out):
        W.run_main("Court checks", boom)
    raise SystemExit("a crash must still fail the run")
except RuntimeError:
    pass
assert "::error title=Court checks::The website did not answer" in out.getvalue()
print("test_alerts OK")
