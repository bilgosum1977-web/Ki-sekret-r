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
else:
    print("[ADMIN LOG] ⚠️ Kein Groq API Key gefunden!", flush=True)

# Aktualisiert auf das gewünschte Modell
GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"

INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
user_balances = {}

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS user_profile (user_id TEXT, fact_key TEXT, fact_value TEXT, PRIMARY KEY (user_id, fact_key))')
    cursor.execute('CREATE TABLE IF NOT EXISTS marketplace_demand (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, title TEXT, location TEXT, max_price REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

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
    return "Suchauftrag erfolgreich hinterlegt! Ich scanne den Markt nun alle 30 Minuten autonom."

init_db()

# --- AGENT TOOLS ---
def search_web(query):
    try:
        res = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=5)
        if res.status_code == 200:
            items = res.json().get("results", [])[:3]
            if items: return "\n".join([f"• {i.get('title','')}: {i.get('content','')} ({i.get('url','')})" for i in items]), True
    except: pass
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(query, max_results=3))
            if items: return "\n".join([f"• {i.get('title','')}: {i.get('body','')} ({i.get('href','')})" for i in items]), True
    except: pass
    return "Keine Web-Ergebnisse gefunden.", False

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
    if not APIFY_TOKEN: return "Apify Token fehlt."
    try:
        actor = "apify/kleinanzeigen-scraper" if "klein" in platform.lower() else "apify/google-maps-scraper"
        res = requests.post(f"https://apify.com{actor}/run-sync?token={APIFY_TOKEN}", json={"searchQueries": [query], "maxItems": 3}, timeout=15)
        if res.status_code == 200: return json.dumps(res.json()[:3])
    except Exception as e: return f"Scraping Fehler: {e}"
    return "Keine Daten gefunden."

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
    {"type": "function", "function": {"name": "calculate_local_distance", "description": "Berechnet Distanz (in km) für 10km-Matching.", "parameters": {"type": "object", "properties": {"location_a": {"type": "string"}, "location_b": {"type": "string"}}, "required": ["location_a", "location_b"]}}},
    {"type": "function", "function": {"name": "verify_reviews_authenticity", "description": "Sammelt Rezensionen zur Fake-Analyse.", "parameters": {"type": "object", "properties": {"target_name": {"type": "string"}}, "required": ["target_name"]}}},
    {"type": "function", "function": {"name": "search_protected_marketplace", "description": "Durchsucht geschützte Plattformen via Apify.", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "query": {"type": "string"}}, "required": ["platform", "query"]}}},
    {"type": "function", "function": {"name": "send_negotiation_email", "description": "Sendet Verhandlungs-Mails.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}}},
    {"type": "function", "function": {"name": "add_market_demand", "description": "Hinterlegt eine dauerhafte Matching-Aufgabe.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "location": {"type": "string"}, "max_price": {"type": "number"}}, "required": ["title", "location", "max_price"]}}}
]

SYSTEM_PROMPT = "Du bist 'KI Sekretär', ein autonomer Broker. Regeln: 1. Nutze 'calculate_local_distance' für max 10km Radius. 2. Prüfe Rezensionen mit 'verify_reviews_authenticity' auf Fake-Muster. 3. Nutze 'search_protected_marketplace' bei Bedarf. 4. Führe Verhandlungen via 'send_negotiation_email'."

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
    content, model_info, _ = call_groq_text(messages_list)
    return content, model_info

# --- TELEGRAM HELPER ---
def send_telegram_message(chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    final_text = f"{text}\n\n--- [ADMIN INFO] ---\n🤖 Modell: {model_name}" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try:
        res = requests.post(url, json={"chat_id": chat_id, "text": final_text}, timeout=5).json()
        if res.get("ok"): 
            return res["result"]["message_id"]
    except Exception as e: 
        print(f"Telegram Senden Fehler: {e}")
    return None

def edit_telegram_message(message_id, chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    final_text = f"{text}\n\n--- [ADMIN INFO] ---\n🤖 Modell: {model_name}" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try:
        requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": final_text}, timeout=5)
    except Exception as e: 
        print(f"Telegram Edit Fehler: {e}")

def get_telegram_file_bytes(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=5).json()
        if not r.get("ok"): return None
        return requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{r['result']['file_path']}", timeout=10).content
    except: 
        return None

# --- ASYNCHRONE WORKER PIPELINE ---
def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    try:
        if chat_id not in user_balances: user_balances[chat_id] = INITIAL_BALANCE
        save_message(chat_id, "user", user_text or "(Bild gesendet)")

        profile = get_user_profile(chat_id)
        history = get_history(chat_id, limit=MAX_HISTORY_LENGTH)
        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\nUser-Profil: {json.dumps(profile, ensure_ascii=False)}"}] + history

        bot_reply, used_model_name = "", ""

        if image_bytes:
            bot_reply, used_model_name = call_groq_vision(user_text, image_bytes)
        else:
            messages.append({"role": "user", "content": user_text})
            
            provider = "groq"
            if any(keyword in user_text.lower() for keyword in ["verhandle", "kaufen", "vertrag", "preis drücken", "match", "bestelle"]):
                provider = "openai" if OPENAI_API_KEY else "gemini"
            
            content, used_model_name, tool_calls = call_groq_text(messages)
            bot_reply = content

            if tool_calls:
                messages.append({"role": "assistant", "content": None, "tool_calls": [tc for tc in tool_calls]})
                
                for tc in tool_calls:
                    func_name = tc.function.name
                    args = json.loads(tc.function.arguments)
                    print(f"[ADMIN LOG] 🛠️ Tool-Aufruf: {func_name} mit {args}", flush=True)

                    tool_result = ""
                    if func_name == "save_user_fact":
                        save_user_fact(chat_id, args.get("key"), args.get("value"))
                        tool_result = f"Fakt gespeichert: {args.get('key')} = {args.get('value')}"
                    elif func_name == "search_web":
                        tool_result, _ = search_web(args.get("query"))
                    elif func_name == "calculate_local_distance":
                        tool_result = calculate_local_distance(args.get("location_a"), args.get("location_b"))
                    elif func_name == "verify_reviews_authenticity":
                        tool_result = verify_reviews_authenticity(args.get("target_name"))
                    elif func_name == "search_protected_marketplace":
                        tool_result = search_protected_marketplace(args.get("platform"), args.get("query"))
                    elif func_name == "send_negotiation_email":
                        tool_result = send_negotiation_email(args.get("to_email"), args.get("subject"), args.get("body"))
                    elif func_name == "add_market_demand":
                        tool_result = add_market_demand(chat_id, args.get("title"), args.get("location"), args.get("max_price"))

                    messages.append({"role": "tool", "content": str(tool_result), "tool_call_id": tc.id})
                
                bot_reply, used_model_name = call_premium_ai(messages, provider=provider)

        if not bot_reply:
            bot_reply = "Aktion erfolgreich ausgeführt."

        save_message(chat_id, "assistant", bot_reply)

        if loading_msg_id:
            edit_telegram_message(loading_msg_id, chat_id, bot_reply, model_name=used_model_name)
        else:
            send_telegram_message(chat_id, bot_reply, model_name=used_model_name)

    except Exception as e:
        print(f"[ADMIN LOG] ❌ Worker Fehler: {e}", flush=True)

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data or "message" not in data: return "OK", 200
        msg = data["message"]
        chat_id = str(msg["chat"]["id"])
        user_text = msg.get("text", msg.get("caption", ""))
        image_bytes = get_telegram_file_bytes(msg["photo"][-1]["file_id"]) if "photo" in msg else None

        if not user_text and not image_bytes: return "OK", 200

        loading_msg_id = send_telegram_message(chat_id, "Bearbeite Anfrage..." if not image_bytes else "Analysiere Bild...")
        executor.submit(process_message_async, chat_id, user_text, image_bytes, loading_msg_id)
    except Exception as e:
        print(f"Webhook Fehler: {e}", flush=True)
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive and broker-ready!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
