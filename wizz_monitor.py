import os
import re
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME")

WIZZ_URL = "https://wizzair.com/en-gb"
MAX_PRICE_EUR = 50
MAX_PRICE_ILS = 205  # שווה ערך לכ-50 אירו

PROMO_PATTERNS = [
    r"\b\d{1,2}%\s*(?:off|discount)\b",
    r"\bflash\s*sale\b",
    r"\bselected\s*flights\b"
]

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
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload)
    print(f"Telegram response status: {res.status_code}")

def check_cheap_flights_graphql():
    """
    שליפת טיסות ישירות של Wizz Air מתל אביב לחצי השנה הקרובה מתחת ל-50 אירו
    דרך ה-API הפתוח של Kiwi Umbrella (ללא צורך במפתח וללא חסימות בוטים)
    """
    cheap_flights = []
    today = datetime.now()
    six_months = today + timedelta(days=180)
    
    url = "https://api.skypicker.com/umbrella/v2/graphql"
    query = """
    query SearchCheapWizz($search: SearchOnewayInput) {
      onewayItineraries(search: $search) {
        ... on Itineraries {
          itineraries {
            price { amount }
            sector {
              sectorSegments {
                segment {
                  source { station { name code } localTime }
                  destination { station { name code country { name } } localTime }
                  carrier { name code }
                }
              }
            }
          }
        }
      }
    }
    """
    
    variables = {
        "search": {
            "itinerary": {
                "source": {"ids": ["Station:airport:TLV"]},
                "outboundDepartureDate": {
                    "start": today.strftime("%Y-%m-%dT00:00:00"),
                    "end": six_months.strftime("%Y-%m-%dT23:59:59")
                }
            },
            "passengers": {"adults": 1},
            "filters": {
                "maxStopovers": 0,
                "carriers": {"include": ["W6", "W4", "5W"]}, # קודי החברות של Wizz Air
                "price": {"end": MAX_PRICE_EUR}
            },
            "options": {
                "currency": "EUR",
                "sortBy": "PRICE",
                "limit": 10
            }
        }
    }
    
    try:
        res = requests.post(url, json={"query": query, "variables": variables}, timeout=20)
        if res.status_code == 200:
            data = res.json()
            itineraries = (
                data.get("data", {})
                .get("onewayItineraries", {})
                .get("itineraries", [])
            )
            for item in itineraries:
                price = float(item["price"]["amount"])
                if price <= MAX_PRICE_EUR:
                    seg = item["sector"]["sectorSegments"][0]["segment"]
                    dest_city = seg["destination"]["station"]["name"]
                    dest_code = seg["destination"]["station"]["code"]
                    dep_time = seg["source"]["localTime"][:10]
                    # המרת תאריך לפורמט נוח DD/MM/YYYY
                    dep_formatted = datetime.strptime(dep_time, "%Y-%m-%d").strftime("%d/%m/%Y")
                    cheap_flights.append(
                        f"✈️ <b>{dest_city} ({dest_code})</b> | {dep_formatted} | <b>{price:.0f}€</b>"
                    )
    except Exception as e:
        print(f"Flight API check note: {e}")
        
    return cheap_flights

def check_wizz_promos():
    found_promos = set()
    low_fares_on_site = set()
    
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
            
            for line in body_text.split("\n"):
                clean_line = line.strip()
                if 5 < len(clean_line) < 200:
                    lower_line = clean_line.lower()
                    
                    if any(ignore in lower_line for ignore in IGNORE_PHRASES):
                        continue
                        
                    # 1. בדיקת באנרים של מבצעים כלליים
                    for pattern in PROMO_PATTERNS:
                        if re.search(pattern, clean_line, re.IGNORECASE):
                            found_promos.add(clean_line)
                            
                    # 2. בדיקת מחירים מתחת ל-50 אירו / 205 ש"ח שמופיעים בעמוד
                    eur_match = re.search(r"(?:€|EUR)\s*(\d{1,2})(?:\.\d{2})?\b|\b(\d{1,2})(?:\.\d{2})?\s*(?:€|EUR)", clean_line)
                    if eur_match:
                        val = int(eur_match.group(1) or eur_match.group(2))
                        if 9 <= val <= MAX_PRICE_EUR:
                            low_fares_on_site.add(clean_line)
                            
                    ils_match = re.search(r"(?:₪|ILS)\s*(\d{2,3})\b|\b(\d{2,3})\s*(?:₪|ILS)", clean_line)
                    if ils_match:
                        val = int(ils_match.group(1) or ils_match.group(2))
                        if 35 <= val <= MAX_PRICE_ILS:
                            low_fares_on_site.add(clean_line)
                            
        except Exception as e:
            print(f"Error during site scan: {e}")
        finally:
            browser.close()

    # 3. בדיקת טיסות Wizz Air מתל אביב לחצי השנה הקרובה מתחת ל-50 אירו
    print("Checking TLV Wizz Air flights under 50 EUR for the next 6 months...")
    cheap_tlv_flights = check_cheap_flights_graphql()

    # בניית ההודעה לטלגרם
    messages_parts = []
    
    if found_promos:
        promo_list = "\n• ".join(list(found_promos)[:5])
        messages_parts.append(f"🎉 <b>זוהה מבצע באתר Wizz Air:</b>\n• {promo_list}")
        
    if cheap_tlv_flights:
        flights_list = "\n".join(cheap_tlv_flights[:8])
        messages_parts.append(f"🔥 <b>טיסות Wizz Air מתל אביב מתחת ל-50€ (בחצי השנה הקרובה):</b>\n{flights_list}")
    elif low_fares_on_site:
        fares_list = "\n• ".join(list(low_fares_on_site)[:5])
        messages_parts.append(f"💸 <b>מחירי רצפה שזוהו באתר:</b>\n• {fares_list}")

    if messages_parts:
        full_msg = "\n\n".join(messages_parts) + f"\n\n<a href='{WIZZ_URL}'>לכניסה לאתר Wizz Air</a>"
        send_telegram_message(full_msg)
        print("Alerts found! Notification sent.")
    else:
        print("No promos or <50 EUR flights found right now.")
        if EVENT_NAME == "workflow_dispatch":
            test_msg = (
                "✅ <b>הבוט המשודרג של Wizz Air פועל!</b>\n\n"
                "נבדקו עכשיו שני הדברים:\n"
                "1️⃣ מבצעים והנחות בעמוד הבית של Wizz Air.\n"
                "2️⃣ טיסות ישירות מנתב\"ג לחצי השנה הקרובה מתחת ל-50€ לכיוון.\n\n"
                "כרגע אין מציאות מתחת לרף הזה – אמשיך לסרוק כל שעה ואקפיץ לך הודעה מיד כשיימצא משהו!"
            )
            send_telegram_message(test_msg)
            print("Test notification sent!")

if __name__ == "__main__":
    check_wizz_promos()
