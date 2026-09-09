import os, io, sqlite3, json, base64, time, threading, concurrent.futures, smtplib
from email.mime.text import MIMEText
from flask import Flask, request
import requests
from groq import Groq
from duckduckgo_search import DDGS
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

app = Flask(__name__)

# --- CONFIGURATION & KEYS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "8874543115")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

# Konfiguriert auf das offene Modell über Groq (kein Llama):
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"
INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
user_balances = {}

# --- MASTER MULTI-PLATTFORM DATENBANK (GLOBAL DYNAMIC POOL V2) ---
DYNAMIC_SHOPPING_POOL = [
    # --- TÜRKEI IMPORT (TRENDYOL & HEPSIBURADA) ---
    {"id": 1, "platform": "Trendyol", "title": "Trendyol Man Erkek Siyah Mont (Puffer)", "price_tl": 849, "price_eur": 23.50},
    {"id": 2, "platform": "Trendyol", "title": "Defacto Erkek Waterproof Winter-Parka", "price_tl": 1199, "price_eur": 33.20},
    {"id": 3, "platform": "Hepsiburada", "title": "Hepsiburada: Koton Slim-Fit Steppjacke", "price_tl": 950, "price_eur": 26.30},
    
    # --- DEUTSCHLAND NEUWARE (AMAZON) ---
    {"id": 4, "platform": "Amazon", "title": "Amazon: The North Face Puffer Jacke DE", "price_tl": 4320, "price_eur": 120.00},
    {"id": 5, "platform": "Amazon", "title": "Amazon: Columbia Winterparka Hooded", "price_tl": 3420, "price_eur": 95.00},
    
    # --- GLOBALER SECOND-HAND & GEBRAUCHTMARKT (EBAY & VINTED) ---
    {"id": 6, "platform": "eBay", "title": "eBay: Vintage Nike Bomberjacke (Gebraucht)", "price_tl": 1620, "price_eur": 45.00},
    {"id": 7, "platform": "Vinted", "title": "Vinted: Adidas Originals Windbreaker (Wie neu)", "price_tl": 1080, "price_eur": 30.00},
    
    # --- LOKALER UMKREISMARKT (KLEINANZEIGEN - 20KM RADIUS) ---
    {"id": 8, "platform": "Kleinanzeigen", "title": "Kleinanzeigen: Wellensteyn Jacke Bochum", "price_tl": 5400, "price_eur": 150.00}
]

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS user_profile (user_id TEXT, fact_key TEXT, fact_value TEXT, PRIMARY KEY (user_id, fact_key))')
    cursor.execute('CREATE TABLE IF NOT EXISTS marketplace_demand (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, title TEXT, location TEXT, max_price REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit(); conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)', (str(user_id), role, content))
    conn.commit(); conn.close()

def get_history(user_id, limit=MAX_HISTORY_LENGTH):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?', (str(user_id), limit))
    rows = cursor.fetchall(); conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_user_fact(user_id, key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO user_profile (user_id, fact_key, fact_value) VALUES (?, ?, ?) ON CONFLICT(user_id, fact_key) DO UPDATE SET fact_value = excluded.fact_value', (str(user_id), key, value))
    conn.commit(); conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT fact_key, fact_value FROM user_profile WHERE user_id = ?', (str(user_id),))
    rows = cursor.fetchall(); conn.close()
    return {r[0]: r[1] for r in rows}

def add_market_demand(user_id, title, location, max_price):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO marketplace_demand (user_id, title, location, max_price) VALUES (?, ?, ?, ?)', (str(user_id), title, location, float(max_price)))
    conn.commit(); conn.close()
    return "Suchauftrag erfolgreich hinterlegt! Ich scanne das Netzwerk nun alle 30 Minuten autonom im 20km-Radius sowie auf internationalen Handelsplattformen."

init_db()

# --- AGENT TOOLS ---
def search_web(query):
    try:
        active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
        print(f"[ADMIN LOG] 🌐 Zünde SearXNG-Metasuche über: {active_url} for Begriff: {query}", flush=True)
        params = {
            "q": query,
            "format": "json",
            "categories": "general,shopping",
            "language": "de-DE"
        }
        res = requests.get(f"{active_url}/search", params=params, timeout=8)
        if res.status_code == 200:
            raw_results = res.json().get("results", [])
            items = raw_results[:10]
            if items:
                formatted_data = []
                for i in items:
                    title = i.get('title', 'Produkt')
                    content = i.get('content', i.get('snippet', 'Keine Beschreibung'))
                    url = i.get('url', '')
                    engine = i.get('engine', 'Unknown Engine')
                    formatted_data.append(f"• [{engine}] {title}: {content} ({url})")
                return "\n".join(formatted_data), True
    except Exception as e: 
        print(f"[ADMIN LOG] ⚠️ SearXNG-Schnittstelle blockiert, nutze DuckDuckGo-Ausweichroute: {e}", flush=True)
    
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(query, max_results=5))
            if items: return "\n".join([f"• [DuckDuckGo] {i.get('title','')}: {i.get('body','')} ({i.get('href','')})" for i in items]), True
    except: pass
    return "Keine Web-Ergebnisse über das Metasuche-Netzwerk gefunden.", False

def calculate_local_distance(location_a, location_b):
    try:
        geo = Nominatim(user_agent="tg_matching_broker")
        loc_a, loc_b = geo.geocode(location_a), geo.geocode(location_b)
        if loc_a and loc_b:
            dist = geodesic((loc_a.latitude, loc_a.longitude), (loc_b.latitude, loc_b.longitude)).km
            return json.dumps({"distance_km": round(dist, 2), "status": "success"})
    except Exception as e: return json.dumps({"error": str(e), "status": "failed"})
    return json.dumps({"error": "Standort nicht auflösbar", "status": "failed"})

def verify_reviews_authenticity(target_name):
    data, _ = search_web(f"{target_name} erfahrungen bewertungen forum kritik")
    return f"Ergebnisse für '{target_name}':\n\n{data}"

def search_protected_marketplace(platform, query):
    try:
        p_low = platform.lower()
        if "trendyol" in p_low or "hepsiburada" in p_low:
            domain = "trendyol.com" if "trendyol" in p_low else "hepsiburada.com"
            target_query = f"site:{domain} {query}"
            print(f"[ADMIN LOG] 🌐 Deep-Link-Suche über SearXNG für {platform}: {target_query}", flush=True)
            res_text, _ = search_web(target_query)
            return res_text

        if not APIFY_TOKEN: return "Apify Token fehlt für Marktplatz-Suche."
        actor = "apify/kleinanzeigen-scraper"
        url = f"https://apify.com{actor}/run-sync?token={APIFY_TOKEN}"
        res = requests.post(url, json={"searchQueries": [query], "maxItems": 3}, timeout=30)
        if res.status_code == 200: return json.dumps(res.json()[:3], ensure_ascii=False)
    except Exception as e:
        return f"Fehler bei der Marktplatz-Suche auf {platform}: {e}"
    return f"Keine Daten auf {platform} gefunden."

def send_negotiation_email(to_email, subject, body):
    if not all([SMTP_USER, SMTP_PASSWORD]): return "SMTP Konfiguration fehlt."
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'], msg['From'], msg['To'] = subject, SMTP_USER, to_email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls(); server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string()); server.quit()
        return f"E-Mail erfolgreich an {to_email} gesendet!"
    except Exception as e: return f"E-Mail Fehler: {e}"

ai_tools = [
    {"type": "function", "function": {"name": "save_user_fact", "description": "Speichert Fakten über den User.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "search_web", "description": "Websuche über SearXNG.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate_local_distance", "description": "Berechnet Distanz (in km) für 20km-Matching.", "parameters": {"type": "object", "properties": {"location_a": {"type": "string"}, "location_b": {"type": "string"}}, "required": ["location_a", "location_b"]}}},
    {"type": "function", "function": {"name": "verify_reviews_authenticity", "description": "Sammelt Rezensionen zur Fake-Analyse.", "parameters": {"type": "object", "properties": {"target_name": {"type": "string"}}, "required": ["target_name"]}}},
    {"type": "function", "function": {"name": "search_protected_marketplace", "description": "Durchsucht geschützte Plattformen via Apify oder Deep-Link-Router.", "parameters": {"type": "object", "properties": {"platform": {"type": "string", "description": "Plattformname: 'Kleinanzeigen', 'Trendyol' oder 'Hepsiburada'"}, "query": {"type": "string"}}, "required": ["platform", "query"]}}},
    {"type": "function", "function": {"name": "send_negotiation_email", "description": "Sendet Verhandlungs-Mails.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}}},
    {"type": "function", "function": {"name": "add_market_demand", "description": "Hinterlegt eine dauerhafte Matching-Aufgabe.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "location": {"type": "string"}, "max_price": {"type": "number"}}, "required": ["title", "location", "max_price"]}}}
]

SYSTEM_PROMPT = (
    "Du bist 'KI Sekretär', ein autonomer Broker und globaler Matchmaker.\n"
    "Regeln für Werkzeuge:\n"
    "1. Wenn der User etwas sucht oder bietet, frage als ALLERERSTES den internen Netzwerk-Pool ab.\n"
    "2. Nutze 'calculate_local_distance' für den lokalen 20km Radius (Maximalgrenze: 20km!).\n"
    "3. Prüfe Rezensionen mit 'verify_reviews_authenticity' auf Fake-Muster.\n"
    "4. Nutze 'search_protected_marketplace' für Kleinanzeigen sowie gezielt für türkische Produkte auf 'Trendyol' oder 'Hepsiburada'.\n"
    "5. Führe Verhandlungen via 'send_negotiation_email'."
)

# --- ROUTER & PIPELINES ---
def call_groq_text(messages_list):
    try:
        res = groq_client.chat.completions.create(model=GROQ_TEXT_MODEL, messages=messages_list, tools=ai_tools, tool_choice="auto", temperature=0.5, max_tokens=1024)
        msg = res.choices[0].message
        content = msg.content or ""
        if "</think>" in content: content = content.split("</think>")[-1].strip()
        return content, f"Groq ({GROQ_TEXT_MODEL})", getattr(msg, 'tool_calls', None)
    except Exception as e: return f"Fehler: {e}", "Groq (Error)", None

def call_groq_vision(user_text, image_bytes):
    try:
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        res = groq_client.chat.completions.create(model=GROQ_VISION_MODEL, messages=[{"role": "user", "content": [{"type": "text", "text": user_text or "Analysiere das Bild für den Verhandlungsloop."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}], temperature=0.5)
        content = res.choices[0].message.content
        if "</think>" in content: content = content.split("</think>")[-1].strip()
        return content, "Groq Vision"
    except Exception as e: return f"Vision Fehler: {e}", "Groq Vision (Error)"

def call_premium_ai(messages_list, provider="groq"):
    if provider == "openai" and OPENAI_API_KEY:
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, json={"model": "gpt-4o-mini", "messages": messages_list, "temperature": 0.3}, timeout=10).json()
            return r['choices'][0]['message']['content'], "OpenAI Premium"
        except: pass
    elif provider == "gemini" and GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages_list if m.get("content")]
            r = requests.post(url, json={"contents": contents}, timeout=10).json()
            return r['candidates'][0]['content']['parts'][0]['text'], "Google Gemini"
        except: pass
    content, model_info, _ = call_groq_text(messages_list)
    return content, model_info

def send_telegram_message(chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    final = f"{text}\n\n--- [ADMIN] ---\n🤖 {model_name}" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try: return requests.post(url, json={"chat_id": chat_id, "text": final}, timeout=5).json().get("result", {}).get("message_id")
    except: return None

def edit_telegram_message(message_id, chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    final = f"{text}\n\n--- [ADMIN] ---\n🤖 {model_name}" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try: requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": final}, timeout=5)
    except: pass

def get_telegram_file_bytes(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=5).json()
        if r.get("ok"): return requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{r['result']['file_path']}", timeout=10).content
    except: pass
    return None

def autonomous_broker_loop():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("SELECT user_id, title, location, max_price FROM marketplace_demand")
            demands = cursor.fetchall(); conn.close()
            for uid, title, loc, price in demands:
                raw, success = search_web(f"\"{title}\" {loc}")
                if success:
                    decision, _ = call_premium_ai([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Match-Suche für {title} in {loc} bis {price}€. Treffer:\n{raw}"}])
                    if any(w in decision.lower() for w in ["match", "angebot", "vermittlung", "treffer"]):
                        send_telegram_message(uid, f"🚨 AUTONOMER TREFFER GEFUNDEN:\n\n{decision}")
        except: pass
        time.sleep(1800)

threading.Thread(target=autonomous_broker_loop, daemon=True).start()

# --- DYNAMISCHER SHOPPING-BILD SCHIRM (AMAZON + TRENDYOL WEICHE) ---
def send_shopping_page(chat_id, page=0, edit_id=None):
    start_idx = page * 3
    end_idx = start_idx + 3
    products = DYNAMIC_SHOPPING_POOL[start_idx:end_idx]
    
    if not products: return
    
    table_text = "🛍️ **GLOBAL MULTI-MARKETPLACE BROKER**\n\n| Herkunft | Produktmodell | Euro (€) | Lira (TL) |\n| :--- | :--- | :--- | :--- |\n"
    for p in products:
        table_text += f"| [{p['platform']}] | {p['title']} | {p['price_eur']} € | {p['price_tl']} TL |\n"
        
    buttons = [[{"text": f"📦 [{p['platform']}] {p['title'][:25]}...", "callback_data": f"buy_{p['id']}"}] for p in products]
    
    nav_row = []
    if page > 0: nav_row.append({"text": "◀️ Zurück", "callback_data": f"page_{page-1}"})
    nav_row.append({"text": f"📄 Kachel {page+1}", "callback_data": "ignore"})
    if end_idx < len(DYNAMIC_SHOPPING_POOL): nav_row.append({"text": "Weiter ▶️", "callback_data": f"page_{page+1}"})
    buttons.append(nav_row)
    
    markup = {"inline_keyboard": buttons}
    
    if edit_id:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        requests.post(url, json={"chat_id": chat_id, "message_id": edit_id, "text": table_text, "parse_mode": "Markdown", "reply_markup": markup})
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": table_text, "parse_mode": "Markdown", "reply_markup": markup})

def process_message_async(chat_id, user_text, loading_msg_id):
    try:
        u_low = user_text.lower()
        if any(k in u_low for k in ["trendyol", "amazon", "hepsiburada", "jacke", "mont", "produkt"]):
            url_del = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
            requests.post(url_del, json={"chat_id": chat_id, "message_id": loading_msg_id})
            send_shopping_page(chat_id, page=0)
            return
            
        save_message(chat_id, "user", user_text)
        db_context = "\n--- AKTUELLER INTERNER NETZWERK-POOL (DATENBANK) ---\n"
        try:
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor(); cursor.execute("SELECT user_id, title, location, max_price FROM marketplace_demand"); all_demands = cursor.fetchall(); conn.close()
            if all_demands:
                for uid, t, l, p in all_demands: db_context += f"- User {uid} sucht/bietet: '{t}' in '{l}' ({p}€)\n"
            else: db_context += "(Leer)\n"
        except: pass

        provider = "openai" if any(k in u_low for k in ["verhandle", "kaufen", "preis drücken", "match"]) and OPENAI_API_KEY else "groq"
        SYSTEM_PROMPT = "Du bist 'KI Sekretär', ein internationaler Import-Broker. Vergleiche Preise und schreibe schlüsselfertige Angebote."
        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n{db_context}"}] + get_history(chat_id) + [{"role": "user", "content": user_text}]
        
        bot_reply, used_model = call_premium_ai(messages, provider=provider)
        save_message(chat_id, "assistant", bot_reply)
        
        url_edit = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        final_text = f"{bot_reply}\n\n--- [ADMIN] ---\n🤖 {used_model}" if str(chat_id) == ADMIN_USER_ID else bot_reply
        requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": final_text})
    except: pass

def handle_callback_query(callback_data, chat_id, message_id):
    if callback_data.startswith("page_"):
        next_page = int(callback_data.split("_")[-1])
        send_shopping_page(chat_id, page=next_page, edit_id=message_id)
    elif callback_data.startswith("buy_"):
        prod_id = int(callback_data.split("_")[-1])
        prod = next((p for p in DYNAMIC_SHOPPING_POOL if p["id"] == prod_id), None)
        
        if not prod: return
        
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, json={"chat_id": chat_id, "text": f"⏳ **Broker-Aktion gestartet...**\nDu hast das Produkt gewählt: {prod['title']} von {prod['platform']}. Ich zünde OpenAI Premium, berechne Zoll/Importvorteile und verhandle..."})
        
        SYSTEM_PROMPT = "Du bist 'KI Sekretär', ein internationaler Import-Broker. Vergleiche Preise und schreibe schlüsselfertige Angebote."
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Ich habe über dein Interface das Produkt '{prod['title']}' auf {prod['platform']} für {prod['price_eur']} EUR ({prod['price_tl']} TL) ausgewählt. Berechne den Importvorteil zu Deutschland, analysiere den Deal und schreibe mir ein perfektes Import-Verhandlungsanschreiben!"}
        ]
        bot_reply, used_model = call_premium_ai(messages, provider="openai" if OPENAI_API_KEY else "groq")
        
        final_text = f"{bot_reply}\n\n--- [ADMIN] ---\n🤖 {used_model}" if str(chat_id) == ADMIN_USER_ID else bot_reply
        requests.post(url_msg, json={"chat_id": chat_id, "text": final_text})

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# --- TELEGRAM WEBHOOK ENDPOINT ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data: return "OK", 200
        
        if "callback_query" in data:
            cb = data["callback_query"]
            chat_id = str(cb["message"]["chat"]["id"])
            msg_id = cb["message"]["message_id"]
            executor.submit(handle_callback_query, cb["data"], chat_id, msg_id)
            return "OK", 200
            
        if "message" in data:
            msg = data["message"]
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", msg.get("caption", ""))
            if text:
                url_loading = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                res = requests.post(url_loading, json={"chat_id": chat_id, "text": "Analysiere Marktplatz..."}).json()
                lid = res.get("result", {}).get("message_id")
                if lid: executor.submit(process_message_async, chat_id, text, lid)
    except: pass
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping(): return "Bot is alive!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
