import os
import requests
import datetime as dt
from playwright.sync_api import sync_playwright

# ==========================================
# 🎾 YOUR PADELZONE LOCATIONS
# ==========================================
LOCATIONS = {
    "Sporthotel (1030)": "https://www.eversports.at/api/slot?facilityId=84958&courts%5B%5D=116220&courts%5B%5D=116221&courts%5B%5D=116222&courts%5B%5D=116223&courts%5B%5D=116224",
    "Floridsdorf (1210)": "https://www.eversports.at/api/slot?facilityId=78472&courts%5B%5D=98662&courts%5B%5D=98663&courts%5B%5D=98664&courts%5B%5D=98665&courts%5B%5D=99442&courts%5B%5D=99443&courts%5B%5D=99444&courts%5B%5D=99445",
    "Hagenbrunn (2102)": "https://www.eversports.at/api/slot?facilityId=78898&courts%5B%5D=100400&courts%5B%5D=100401&courts%5B%5D=100402&courts%5B%5D=100403&courts%5B%5D=100404&courts%5B%5D=100405&courts%5B%5D=100406&courts%5B%5D=100407",
    "Wienerberg (1100)": "https://www.eversports.at/api/slot?facilityId=77636&courts%5B%5D=90282&courts%5B%5D=90283&courts%5B%5D=91073", 
    "Stockerau (2000)": "https://www.eversports.at/api/slot?facilityId=85272&courts%5B%5D=117107&courts%5B%5D=117108&courts%5B%5D=117109&courts%5B%5D=117110"
}

EXCLUDE_COURTS = {100407, 91073}

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
EMAIL = os.environ.get("EVERTSPORTS_EMAIL")
PASSWORD = os.environ.get("EVERTSPORTS_PASSWORD")

def send_telegram(text):
    if not TOKEN or not CHAT:
        print("⚠️ Telegram not configured.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=10)
        print("✅ Sent to Telegram!")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

def time_to_min(t_str):
    return int(t_str[:2]) * 60 + int(t_str[2:])

def min_to_time(m_int):
    if m_int >= 1440: return "24:00"
    return f"{m_int // 60:02d}:{m_int % 60:02d}"

def get_cookies():
    print("Launching stealth browser to get Cloudflare cookie...")
    with sync_playwright() as p:
        # headless=True is required for GitHub Actions
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
        )
        page = context.new_page()
        
        # Go to the site (Cloudflare challenge will happen here)
        page.goto("https://www.eversports.at/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000) # Give it extra time to solve the JS challenge
        
        # Grab cookies
        cookies = context.cookies()
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        browser.close()
        return cookie_str

def check_all_eversports():
    # 1. Get the automatic Cloudflare cookie
    cookie_str = get_cookies()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
        "Accept": "*/*",
        "Referer": "https://www.eversports.at/",
        "Cookie": cookie_str,
        "X-Requested-With": "XMLHttpRequest"
    }

    # (We only do the API calls for 1 location here to test if Cloudflare blocks us)
    # If this works, we will add the full loop back in!
    test_loc = "Sporthotel (1030)"
    base_url = LOCATIONS[test_loc]
    
    today = dt.date.today()
    today_str = today.strftime('%Y-%m-%d')
    api_url = f"{base_url}&startDate={today_str}"
    
    print(f"Testing API call for {test_loc}...")
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        print(f"API Status Code: {response.status_code}")
        
        if response.status_code == 200:
            send_telegram("🎾 <b>GITHUB ACTIONS TEST SUCCESS!</b>\nCloudflare allowed the request. We can now enable the full 14-day script.")
            print("✅ SUCCESS! Cloudflare let GitHub Actions through.")
        else:
            send_telegram(f"❌ <b>GITHUB ACTIONS BLOCKED</b>\nCloudflare returned HTTP {response.status_code}. We need a proxy.")
            print(f"❌ BLOCKED. Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check_all_eversports()
