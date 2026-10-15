import os
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME")

# הגדרות ספים
WIZZ_URL = "https://wizzair.com/en-gb"
MAX_FLIGHT_PRICE_EUR = 50

# רשימת המשחקים למעקב
GAMES_TO_TRACK = [
    {
        "name": "EA Sports FC 27 (Xbox)",
        "url": "https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-xbox-series-x-s-code-compare-prices/",
        "max_price": 50.0
    },
    {
        "name": "NBA 2K27 (Xbox)",
        "url": "https://www.allkeyshop.com/blog/buy-nba-2k27-xbox-series-x-s-code-compare-prices/",
        "max_price": 50.0
    }
]

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
                "carriers": {"include": ["W6", "W4", "5W"]},
                "price": {"end": MAX_FLIGHT_PRICE_EUR}
            },
            "options": {
                "currency": "EUR",
                "sortBy": "PRICE",
                "limit": 5
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
                if price <= MAX_FLIGHT_PRICE_EUR:
                    seg = item["sector"]["sectorSegments"][0]["segment"]
                    dest_city = seg["destination"]["station"]["name"]
                    dest_code = seg["destination"]["station"]["code"]
                    dep_time = seg["source"]["localTime"][:10]
                    dep_formatted = datetime.strptime(dep_time, "%Y-%m-%d").strftime("%d/%m/%Y")
                    cheap_flights.append(
                        f"✈️ <b>{dest_city} ({dest_code})</b> | {dep_formatted} | <b>{price:.0f}€</b>"
                    )
    except Exception as e:
        print(f"Flight API check note: {e}")
        
    return cheap_flights

def check_games_prices():
    triggered_games = []
    game_status_reports = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    for game in GAMES_TO_TRACK:
        lowest_price = None
        try:
            print(f"Checking price for {game['name']}...")
            response = requests.get(game["url"], headers=headers, timeout=20)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                price_elements = soup.find_all(class_=lambda x: x and 'price' in x.lower())
                
                found_prices = []
                for el in price_elements:
                    text = el.get_text().strip()
                    if '$' in text or 'USD' in text:
                        match = re.search(r'\$?(\d{1,3}\.\d{2})', text)
                        if match:
                            try:
                                p = float(match.group(1))
                                if 5.0 <= p <= 200.0:
                                    found_prices.append(p)
                            except:
                                pass
                                
                if found_prices:
                    lowest_price = min(found_prices)
                    if lowest_price <= game["max_price"]:
                        triggered_games.append(
                            f"🎮 <b>{game['name']}</b> ירד ל-<b>${lowest_price:.2f}</b>!\n<a href='{game['url']}'>לרכישת הקוד</a>"
                        )
        except Exception as e:
            print(f"Error checking {game['name']}: {e}")
            
        current_str = f"${lowest_price:.2f}" if lowest_price else "לא זוהה"
        game_status_reports.append(f"• {game['name']}: {current_str}")
        
    return triggered_games, game_status_reports

def run_all_checks():
    found_promos = set()
    
    # 1. בדיקת Wizz Air (דפדפן)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        try:
            page.goto(WIZZ_URL, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            body_text = page.inner_text("body")
            for line in body_text.split("\n"):
                clean_line = line.strip()
                if 5 < len(clean_line) < 200:
                    lower_line = clean_line.lower()
                    if any(ignore in lower_line for ignore in IGNORE_PHRASES):
                        continue
                    for pattern in PROMO_PATTERNS:
                        if re.search(pattern, clean_line, re.IGNORECASE):
                            found_promos.add(clean_line)
        except Exception as e:
            print(f"Error scanning Wizz Air: {e}")
        finally:
            browser.close()

    # 2. בדיקת טיסות זולות מתל אביב
    cheap_tlv_flights = check_cheap_flights_graphql()

    # 3. בדיקת מחירי המשחקים
    triggered_games, game_status_reports = check_games_prices()

    # בניית הודעה כוללת
    message_parts = []
    
    if found_promos:
        promo_list = "\n• ".join(list(found_promos)[:4])
        message_parts.append(f"🎉 <b>מבצע באתר Wizz Air:</b>\n• {promo_list}")
        
    if cheap_tlv_flights:
        flights_list = "\n".join(cheap_tlv_flights[:5])
        message_parts.append(f"✈️ <b>טיסות Wizz Air מתל אביב מתחת ל-50€:</b>\n{flights_list}")
        
    if triggered_games:
        games_list = "\n\n".join(triggered_games)
        message_parts.append(f"🔥 <b>התרעת ירידת מחיר למשחקים!</b>\n\n{games_list}")

    if message_parts:
        final_msg = "\n\n".join(message_parts)
        send_telegram_message(final_msg)
        print("Alerts sent successfully!")
    else:
        print("No active alerts triggered.")
        # אם הופעל ידנית לבדיקת מערכת
        if EVENT_NAME == "workflow_dispatch":
            games_report_str = "\n".join(game_status_reports)
            test_msg = (
                "✅ <b>הבוט המשולב פועל ותקין!</b>\n\n"
                "📊 <b>סטטוס נוכחי:</b>\n"
                "• Wizz Air (טיסות ומבצעים): אין מציאות כרגע מתחת לרף.\n"
                f"• מחירי משחקים נסרקים:\n{games_report_str}\n\n"
                "אמשיך לרוץ אוטומטית ברקע ולעדכן אותך ברגע שישתנה משהו!"
            )
            send_telegram_message(test_msg)

if __name__ == "__main__":
    run_all_checks()
