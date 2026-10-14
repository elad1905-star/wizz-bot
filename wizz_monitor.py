import os
import re
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME")

WIZZ_URL = "https://wizzair.com/en-gb"

# ביטויים של מבצעי טיסות אמיתיים
PROMO_PATTERNS = [
    r"\b\d{1,2}%\s*(?:off|discount)\b",
    r"\bflash\s*sale\b",
    r"\bselected\s*flights\b"
]

# ביטויים קבועים באתר שמתעלמים מהם (כמו מלונות, השכרת רכב או שדה קופון ריק)
IGNORE_PHRASES = [
    "hotel booking",
    "home away from home",
    "wizz credit",
    "car hire",
    "apply promo code",
    "booking.com",
    "rentalcars"
]

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    res = requests.post(url, json=payload)
    print(f"Telegram response status: {res.status_code}")

def check_wizz_promos():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        try:
            print("Connecting to Wizz Air...")
            page.goto(WIZZ_URL, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            
            body_text = page.inner_text("body")
            
            found_promos = set()
            for line in body_text.split("\n"):
                clean_line = line.strip()
                if 5 < len(clean_line) < 200:
                    lower_line = clean_line.lower()
                    
                    # דילוג על פרסומות קבועות של מלונות ורכבים
                    if any(ignore in lower_line for ignore in IGNORE_PHRASES):
                        continue
                        
                    for pattern in PROMO_PATTERNS:
                        if re.search(pattern, clean_line, re.IGNORECASE):
                            found_promos.add(clean_line)
            
            if found_promos:
                promo_list = "\n• ".join(list(found_promos)[:6])
                message = (
                    f"🎉 <b>זוהה מבצע חדש באתר Wizz Air!</b>\n\n"
                    f"הטקסטים שאותרו בעמוד הבית:\n• {promo_list}\n\n"
                    f"<a href='{WIZZ_URL}'>לכניסה לאתר Wizz Air</a>"
                )
                send_telegram_message(message)
                print("Promos found! Notification sent.")
            else:
                print("No special flight promos found in this check.")
                if EVENT_NAME == "workflow_dispatch":
                    test_msg = (
                        "✅ <b>הבוט של Wizz Air מעודכן ומוכן!</b>\n\n"
                        "סיננתי את הפרסומות הקבועות של המלונות. כרגע אין מבצע מיוחד על טיסות – "
                        "אמשיך לבדוק בשקט כל שעה ואעדכן אותך ברגע שיעלה מבצע אמיתי!"
                    )
                    send_telegram_message(test_msg)
                    print("Test notification sent to Telegram!")
                
        except Exception as e:
            print(f"Error during scan: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    check_wizz_promos()
