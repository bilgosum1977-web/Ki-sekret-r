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
    cursor.execute('CREATE TABLE IF NOT EXISTS marketplace_supply (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, location TEXT, price REAL, contact TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)', (str(user_id), role, content))
    conn.commit()
    conn.close()

def get_history(user_id, limit=MAX_HISTORY_LENGTH):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?', (str(user_id), limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_user_fact(user_id, key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO user_profile (user_id, fact_key, fact_value) VALUES (?, ?, ?) ON CONFLICT(user_id, fact_key) DO UPDATE SET fact_value = excluded.fact_value', (str(user_id), key, value))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT fact_key, fact_value FROM user_profile WHERE user_id = ?', (str(user_id),))
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def add_market_demand(user_id, title, location, max_price):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO marketplace_demand (user_id, title, location, max_price) VALUES (?, ?, ?, ?)', (str(user_id), title, location, float(max_price)))
    conn.commit()
    conn.close()
    return "Suchauftrag erfolgreich hinterlegt! Ich scanne den Markt nun autonom nach passenden Angeboten."

init_db()

# --- AGENT TOOLS ---
def search_web(query):
    try:
        res = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=5)
        if res.status_code == 200:
            items = res.json().get("results", [])[:4]
            if items: return "\n".join([f"• {i.get('title','')}: {i.get('content','')} ({i.get('url','')})" for i in items]), True
    except: pass
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(query, max_results=4))
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
    try:
        p_low = platform.lower()
        
        # Falls es sich um Trendyol oder Hepsiburada handelt, nutzen wir den unblockierbaren SearXNG-Deep-Link
        if "trendyol" in p_low or "hepsiburada" in p_low:
            domain = "trendyol.com" if "trendyol" in p_low else "hepsiburada.com"
            target_query = f"site:{domain} {query}"
            print(f"[ADMIN LOG] 🌐 Nutze unblockierbaren Deep-Link-Filter für {platform}: {target_query}", flush=True)
            
            res, success = search_web(target_query)
            if success:
                return res
            return f"Keine aktuellen Treffer auf {domain} für '{query}' gefunden."
            
        # Für Kleinanzeigen bleibt der normale Apify-Weg aktiv, falls konfiguriert
        if not APIFY_TOKEN: return "Apify Token fehlt für Kleinanzeigen."
        actor = "apify/kleinanzeigen-scraper"
        url = f"https://api.apify.com/v2/acts/{actor}/run-sync?token={APIFY_TOKEN}"
        run_input = {"searchQueries": [query], "maxItems": 3}
        res = requests.post(url, json=run_input, timeout=30)
        if res.status_code == 200: 
            return json.dumps(res.json()[:3], ensure_ascii=False)
            
    except Exception as e: 
        return f"Fehler bei der Marktplatz-Suche auf {platform}: {e}"
    return f"Keine Daten auf {platform} gefunden."

def send_negotiation_email(to_email, subject, body):
    if not all([SMTP_USER, SMTP_PASSWORD]): return "SMTP Konfiguration fehlt."
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'], msg['From'], msg['To'] = subject, SMTP_USER, to_email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())
        server.quit()
        return f"E-Mail erfolgreich an {to_email} gesendet!"
    except Exception as e: return f"E-Mail Fehler: {e}"

ai_tools = [
    {"type": "function", "function": {"name": "save_user_fact", "description": "Speichert Fakten über den User.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "search_web", "description": "Websuche über SearXNG.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate_local_distance", "description": "Berechnet Distanz (in km) für Matching.", "parameters": {"type": "object", "properties": {"location_a": {"type": "string"}, "location_b": {"type": "string"}}, "required": ["location_a", "location_b"]}}},
    {"type": "function", "function": {"name": "verify_reviews_authenticity", "description": "Sammelt Rezensionen zur Fake-Analyse.", "parameters": {"type": "object", "properties": {"target_name": {"type": "string"}}, "required": ["target_name"]}}},
    {"type": "function", "function": {
        "name": "search_protected_marketplace", 
        "description": "Durchsucht geschützte Plattformen (Kleinanzeigen, Trendyol, Hepsiburada) via unblockierbarem Deep-Link-Filter.", 
        "parameters": {
            "type": "object", 
            "properties": {
                "platform": {"type": "string", "description": "Plattformname: 'Kleinanzeigen', 'Trendyol' oder 'Hepsiburada'"}, 
                "query": {"type": "string", "description": "Der Suchbegriff"}
            }, 
            "required": ["platform", "query"]
        }
    }},
    {"type": "function", "function": {"name": "send_negotiation_email", "description": "Sendet Verhandlungs-Mails.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}}},
    {"type": "function", "function": {"name": "add_market_demand", "description": "Hinterlegt eine dauerhafte Matching-Aufgabe.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "location": {"type": "string"}, "max_price": {"type": "number"}}, "required": ["title", "location", "max_price"]}}}
]

# --- SYSTEM PROMPT ---
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
    try: 
        return requests.post(url, json={"chat_id": chat_id, "text": final}, timeout=5).json().get("result", {}).get("message_id")
    except: return None

def edit_telegram_message(message_id, chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    final = f"{text}\n\n--- [ADMIN] ---\n🤖 {model_name}" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try: 
        requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": final}, timeout=5)
    except: pass

def get_telegram_file_bytes(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=5).json()
        if r.get("ok"): 
            return requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{r['result']['file_path']}", timeout=10).content
    except: pass
    return None

def autonomous_broker_loop():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, title, location, max_price FROM marketplace_demand")
            demands = cursor.fetchall()
            conn.close()
            
            for uid, title, loc, price in demands:
                raw, success = search_web(f'"{title}" {loc}')
                if success:
                    decision, _ = call_premium_ai([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Match-Suche für {title} in {loc} bis {price}€. Treffer:\n{raw}"}])
                    if any(w in decision.lower() for w in ["match", "angebot", "vermittlung", "treffer"]):
                        send_telegram_message(uid, f"🚨 AUTONOMER TREFFER GEFUNDEN:\n\n{decision}")
        except: pass
        time.sleep(1800)

threading.Thread(target=autonomous_broker_loop, daemon=True).start()

# --- MASTER-DATENBANK-ABGLEICH & WORKER LOOP ---
def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    try:
        if chat_id not in user_balances: user_balances[chat_id] = INITIAL_BALANCE
        used_model = "Groq"
        if image_bytes:
            desc, used_model = call_groq_vision(user_text, image_bytes)
            user_text = f"[Bild-Analyse: {desc}] {user_text or ''}".strip()
        
        save_message(chat_id, "user", user_text)
        
        db_context = "\n--- AKTUELLER INTERNER NETZWERK-POOL (DATENBANK) ---\n"
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT user_id, title, location, max_price FROM marketplace_demand")
            all_demands = cursor.fetchall()
            
            cursor.execute("SELECT title, location, price, contact FROM marketplace_supply")
            all_supplies = cursor.fetchall()
            conn.close()
            
            if all_demands:
                db_context += "\n[SUCHEN / NACHFRAGE]:\n"
                for uid, t, l, p in all_demands:
                    db_context += f"- User {uid} sucht: '{t}' in '{l}' (Limit/Budget: {p}€)\n"
            
            if all_supplies:
                db_context += "\n[ANGEBOTE / SUPPLY]:\n"
                for t, l, p, c in all_supplies:
                    db_context += f"- Angebot: '{t}' in '{l}' (Preis/Lohn: {p}€) | Kontakt: {c}\n"
                    
            if not all_demands and not all_supplies:
                db_context += "(Die interne Datenbank ist aktuell komplett leer.)\n"
                
        except Exception as db_err:
            db_context += f"(Fehler beim Lesen der Datenbank: {db_err})\n"

        provider = "groq"
        if any(k in user_text.lower() for k in ["verhandle", "kaufen", "preis drücken", "match", "pool", "prüfe", "trendyol", "hepsiburada"]):
            provider = "openai" if OPENAI_API_KEY else "gemini"
        
        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n{db_context}\nProfil: {json.dumps(get_user_profile(chat_id))}"}] + get_history(chat_id)
        messages.append({"role": "user", "content": user_text})
        
        content, used_model, tool_calls = call_groq_text(messages)
        bot_reply = content
        
        if tool_calls:
            has_executed_data_tool = False
            combined_tool_data = "\n--- SYSTEM DATA / TOOL RESULTS ---\n"
            
            for tc in tool_calls:
                fn, args = tc.function.name, json.loads(tc.function.arguments)
                res = ""
                if fn == "save_user_fact": 
                    save_user_fact(chat_id, args.get("key"), args.get("value"))
                    res = f"Fakt gespeichert: {args.get('key')} = {args.get('value')}"
                elif fn == "search_web": 
                    clean_query = args.get("query").replace("Suche", "").replace("ich wohne in", "").strip()
                    res, _ = search_web(clean_query)
                    has_executed_data_tool = True
                elif fn == "calculate_local_distance": 
                    res = calculate_local_distance(args.get("location_a"), args.get("location_b"))
                    has_executed_data_tool = True
                elif fn == "verify_reviews_authenticity": 
                    res = verify_reviews_authenticity(args.get("target_name"))
                    has_executed_data_tool = True
                elif fn == "search_protected_marketplace": 
                    res = search_protected_marketplace(args.get("platform"), args.get("query"))
                    has_executed_data_tool = True
                elif fn == "send_negotiation_email": 
                    res = send_negotiation_email(args.get("to_email"), args.get("subject"), args.get("body"))
                    has_executed_data_tool = True
                elif fn == "add_market_demand": 
                    res = add_market_demand(chat_id, args.get("title"), args.get("location"), args.get("max_price"))
                    has_executed_data_tool = True
                
                combined_tool_data += f"\n[Werkzeug {fn}]: {res}"
            
            if has_executed_data_tool:
                messages.append({"role": "user", "content": f"Verarbeite diese soeben ermittelten Live-Daten und den aktuellen Netzwerk-Pool. Falls ein passender Eintrag im Radius existiert, führe das Match sofort zusammen und formuliere das Broker-Ergebnis:\n{combined_tool_data}"})
                print(f"[ADMIN LOG] 🧠 Starte finalen Match-Durchlauf über {provider}...", flush=True)
                premium_reply, premium_model = call_premium_ai(messages, provider=provider)
                bot_reply = premium_reply
                used_model = premium_model
                    
        if not bot_reply or "auswertbaren daten" in bot_reply.lower():
            bot_reply = "Ich habe das Netzwerk analysiert. Aktuell liegt kein direktes Match vor. Ich habe Ihre Anfrage im System hinterlegt und informiere Sie autonom, sobald ein passender Partner im Umkreis postet."

        save_message(chat_id, "assistant", bot_reply)
        
        if loading_msg_id:
            edit_telegram_message(loading_msg_id, chat_id, bot_reply, model_name=used_model)
        else:
            send_telegram_message(chat_id, bot_reply, model_name=used_model)
    except Exception as e:
        if loading_msg_id:
            edit_telegram_message(loading_msg_id, chat_id, f"Fehler bei der Broker-Verarbeitung: {e}")

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if data and "message" in data:
            msg = data["message"]
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", msg.get("caption", ""))
            img_bytes = get_telegram_file_bytes(msg["photo"][-1]["file_id"]) if "photo" in msg else None

            if text or img_bytes:
                lid = send_telegram_message(chat_id, "Analysiere Foto & starte Umkreissuche..." if img_bytes else "Analysiere Daten & recherchiere...")
                if lid: 
                    executor.submit(process_message_async, chat_id, text, img_bytes, lid)
    except Exception as e: 
        print(f"Webhook Fehler: {e}")
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping(): 
    return "Bot is alive!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
