"""Sun Set watcher: a connection/certificate hiccup is retried once; other errors and a second failure are not."""
import os, sys, types
sys.path.insert(0, os.getcwd())   # tests run from the repo folder
import sunset_events as CK

class Page:
    def __init__(self, errors): self.errors, self.waits, self.gotos = list(errors), [], 0
    def route(self, *a): pass
    def goto(self, url, wait_until=None):
        self.gotos += 1
        if self.errors:
            raise Exception(self.errors.pop(0))
    def wait_for_timeout(self, ms): self.waits.append(ms)
    def inner_text(self, sel): return "page text"
class Browser:
    def __init__(self, page): self.page = page
    def new_page(self): return self.page
    def close(self): pass

def run(errors):
    page = Page(errors)
    class P:
        def __enter__(self): return object()
        def __exit__(self, *a): return False
    sys.modules["playwright.sync_api"] = types.SimpleNamespace(sync_playwright=lambda: P())
    CK.W.launch_browser = lambda p: Browser(page)
    return page, CK.read_page

cert = "Page.goto: net::ERR_CERT_COMMON_NAME_INVALID at https://sunsetpadel.at/events"
page, rp = run([cert]); assert rp() == "page text" and page.gotos == 2 and 20000 in page.waits, "one hiccup: retried, page read"
page, rp = run([cert, cert])
try: rp(); raise SystemExit("two failures must raise")
except Exception as e: assert "ERR_CERT" in str(e) and page.gotos == 2
page, rp = run(["Timeout 30000ms exceeded"])
try: rp(); raise SystemExit("other errors are not retried")
except Exception as e: assert page.gotos == 1
print("test_sunset_retry OK")
