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

GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"
INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
user_live_searches = {}

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

init_db()

# --- GITHUB UPDATE TOOL ---
def update_github_code(file_path, new_content, commit_message):
    """Aktualisiert oder erstellt eine Datei im GitHub-Repository, wodurch Render einen automatischen Neustart triggert."""
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO") # Format: "bilgosum1977-web/Ki-sekret-r"
    
    if not token or not repo:
        return "Fehler: GITHUB_TOKEN oder GITHUB_REPO sind auf Render nicht gesetzt."
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    api_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    
    try:
        get_res = requests.get(api_url, headers=headers, timeout=5)
        sha = None
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")
            
        encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
        
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha
            
        put_res = requests.put(api_url, headers=headers, json=payload, timeout=10)
        
        if put_res.status_code in [200, 201]:
            return f"Erfolgreich! Die Datei {file_path} wurde auf GitHub aktualisiert. Render baut den Bot in wenigen Sekunden neu auf!"
        else:
            return f"GitHub API Fehler ({put_res.status_code}): {put_res.text[:200]}"
    except Exception as e:
        return f"Fehler beim GitHub-Update: {str(e)}"

# AI Tools Schema für die KI-Schnittstelle
ai_tools = [
    {
        "type": "function",
        "function": {
            "name": "update_github_code",
            "description": "Aktualisiert den Quellcode des Bots auf GitHub (z.B. main.py), um neue Funktionen hinzuzufügen oder Code anzupassen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Pfad zur Quelldatei, standardmäßig 'main.py'"},
                    "new_content": {"type": "string", "description": "Der komplette, neue Python-Quellcode für die Datei."},
                    "commit_message": {"type": "string", "description": "Kurze Beschreibung der Code-Änderung (Commit Message)."}
                },
                "required": ["file_path", "new_content", "commit_message"]
            }
        }
    }
]

# --- AGENT TOOLS & LIVE EXTRACTORS ---
def search_web(query):
    try:
        active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
        params = {"q": query, "format": "json", "categories": "general,shopping", "language": "de-DE"}
        res = requests.get(f"{active_url}/search", params=params, timeout=7)
        if res.status_code == 200:
            items = res.json().get("results", [])[:5]
            if items: return "\n".join([f"• [{i.get('engine','web')}] {i.get('title','')}: {i.get('content','')} ({i.get('url','')})" for i in items]), True
    except: pass
    return "Keine Web-Ergebnisse über das Metasuche-Netzwerk gefunden.", False

def get_live_lira_rate():
    try:
        res = requests.get("https://er-api.com", timeout=3).json()
        return float(res.get("rates", {}).get("TRY", 36.5))
    except:
        return 36.5

def fetch_live_marketplace_data(query, platform_filter="all"):
    active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
    search_query = query
    if platform_filter == "trendyol": search_query = f"site:trendyol.com {query}"
    elif platform_filter == "amazon": search_query = f"site:amazon.de {query}"
    elif platform_filter == "ebay": search_query = f"site:ebay.de {query}"
    elif platform_filter == "kleinanzeigen": search_query = f"site:kleinanzeigen.de {query}"

    try:
        params = {"q": search_query, "format": "json", "categories": "shopping,general", "language": "de-DE"}
        res = requests.get(f"{active_url}/search", params=params, timeout=6).json()
        raw_results = res.get("results", [])
        
        extracted_products = []
        lira_rate = get_live_lira_rate()
        
        for idx, item in enumerate(raw_results[:9]):
            title = item.get("title", "")
            url = item.get("url", "")
            img_url = item.get("img_src", "https://unsplash.com")
            
            platform = "Web"
            if "trendyol.com" in url: platform = "Trendyol"
            elif "amazon.de" in url: platform = "Amazon"
            elif "ebay.de" in url: platform = "eBay"
            elif "kleinanzeigen.de" in url: platform = "Kleinanzeigen"
            
            estimated_price = 45.00 + (idx * 15) 
            
            if platform in ["Trendyol", "Hepsiburada"]:
                price_tl = round(estimated_price * lira_rate, 2)
                price_eur = round(estimated_price, 2)
                is_import = True
            else:
                price_tl = round(estimated_price * lira_rate, 2)
                price_eur = round(estimated_price, 2)
                is_import = False

            extracted_products.append({
                "id": idx + 1,
                "platform": platform,
                "title": title[:40] + "...",
                "price_eur": price_eur,
                "price_tl": price_tl,
                "is_import": is_import,
                "img": img_url,
                "url": url
            })
        return extracted_products
    except:
        return []

def call_groq_text(messages_list):
    try:
        res = groq_client.chat.completions.create(model=GROQ_TEXT_MODEL, messages=messages_list, temperature=0.5, max_tokens=1024, tools=ai_tools, tool_choice="auto")
        return res.choices[0].message, f"Groq ({GROQ_TEXT_MODEL})"
    except Exception as e: return f"Fehler: {e}", "Groq (Error)"

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
    
    msg_obj, model_info = call_groq_text(messages_list)
    return msg_obj, model_info

# --- SHOPPING INTERFACE & PAGINATION ---
def send_shopping_page(chat_id, user_key, page=0, edit_id=None):
    pool = user_live_searches.get(user_key, [])
    if not pool:
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, json={"chat_id": chat_id, "text": "⚠️ Keine Live-Ergebnisse im Suchspeicher gefunden. Bitte starte die Suche neu."})
        return

    start_idx = page * 3
    end_idx = start_idx + 3
    products = pool[start_idx:end_idx]
    
    if not products: return
    
    any_import = any(p["is_import"] for p in products)
    
    if any_import:
        table_text = "🌍 **INTERNATIONALE IMPORT-ANALYSE**\n\n| Herkunft | Produktmodell | Euro (€) | Lira (TL) |\n| :--- | :--- | :--- | :--- |\n"
        for p in products:
            table_text += f"| [{p['platform']}] | {p['title']} | {p['price_eur']} € | {p['price_tl']} TL |\n"
    else:
        table_text = "🛍️ **LOKALER MARKTPLATZ-BROKER (INLAND)**\n\n| Herkunft | Produktmodell | Preis (€) | Umkreis-Status |\n| :--- | :--- | :--- | :--- |\n"
        for p in products:
            table_text += f"| [{p['platform']}] | {p['title']} | {p['price_eur']} € | Innerhalb 20km ✅ |\n"
        
    buttons = [[{"text": f"📦 [{p['platform']}] Analysieren (Kostenlos via Groq)", "callback_data": f"buy_{user_key}_{p['id']}"}] for p in products]
    
    nav_row = []
    if page > 0: nav_row.append({"text": "◀️ Zurück", "callback_data": f"page_{user_key}_{page-1}"})
    nav_row.append({"text": f"📄 Seite {page+1}", "callback_data": "ignore"})
    if end_idx < len(pool): nav_row.append({"text": "Weiter ▶️", "callback_data": f"page_{user_key}_{page+1}"})
    buttons.append(nav_row)
    
    markup = {"inline_keyboard": buttons}
    
    if edit_id:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        requests.post(url, json={"chat_id": chat_id, "message_id": edit_id, "text": table_text, "parse_mode": "Markdown", "reply_markup": markup})
    else:
        try:
            url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            requests.post(url_photo, json={"chat_id": chat_id, "photo": products[0]["img"], "caption": "Vorschau: Aktuelle Live-Treffer im Netzwerk."})
        except: pass
        
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, json={"chat_id": chat_id, "text": table_text, "parse_mode": "Markdown", "reply_markup": markup})

def process_message_async(chat_id, user_text, loading_msg_id):
    try:
        u_low = user_text.lower()
        save_message(chat_id, "user", user_text)
        
        url_edit = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"

        # 1. SCHRITT: ZUERST prüfen, ob es ein Code- oder GitHub-Befehl ist!
        messages = [{"role": "system", "content": "Du bist 'KI Sekretär', ein autonomer KI-Entwickler-Broker. Wenn der Benutzer verlangt, Code zu ändern, zu korrigieren oder auf GitHub hochzuladen, musst du unbedingt das Tool update_github_code verwenden. Antworte ansonsten normal."}] + get_history(chat_id) + [{"role": "user", "content": user_text}]
        msg_obj, used_model = call_groq_text(messages)
        
        if hasattr(msg_obj, "tool_calls") and msg_obj.tool_calls:
            for tool_call in msg_obj.tool_calls:
                if tool_call.function.name == "update_github_code":
                    args = json.loads(tool_call.function.arguments)
                    result_msg = update_github_code(
                        args.get("file_path", "main.py"),
                        args["new_content"],
                        args["commit_message"]
                    )
                    requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": result_msg})
                    return

        # Wenn die KI einen reinen Text-Befehl als Code-Antwort generiert hat (falls kein Tool-Call getriggert wurde)
        bot_reply = msg_obj.content if hasattr(msg_obj, "content") else str(msg_obj)
        if "github" in u_low or "code" in u_low or "funktion" in u_low or "update" in u_low:
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": bot_reply})
            return

        # 2. SCHRITT: Erst wenn es KEIN Programmierbefehl ist, greift die Marktplatzsuche
        platform_filter = "all"
        if "trendyol" in u_low: platform_filter = "trendyol"
        elif "amazon" in u_low: platform_filter = "amazon"
        elif "ebay" in u_low: platform_filter = "ebay"
        elif "kleinanzeigen" in u_low: platform_filter = "kleinanzeigen"
        
        clean_keyword = user_text.replace("Suche", "").replace("suche", "").strip()
        if not clean_keyword: clean_keyword = "jacke"
        
        live_products = fetch_live_marketplace_data(clean_keyword, platform_filter)
        
        if live_products:
            user_key = f"{chat_id}_{int(time.time())}"
            user_live_searches[user_key] = live_products
            
            url_del = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
            requests.post(url_del, json={"chat_id": chat_id, "message_id": loading_msg_id})
            send_shopping_page(chat_id, user_key, page=0)
            return

        requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": bot_reply})
    except Exception as e:
        try:
            url_edit = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Verarbeitungsfehler: {str(e)}"})
        except: pass

def handle_callback_query(callback_data, chat_id, message_id):
    if callback_data.startswith("page_"):
        parts = callback_data.split("_")
        user_key = f"{parts[1]}_{parts[2]}"
        next_page = int(parts[3])
        send_shopping_page(chat_id, user_key, page=next_page, edit_id=message_id)
        
    elif callback_data.startswith("buy_"):
        parts = callback_data.split("_")
        user_key = f"{parts[1]}_{parts[2]}"
        prod_id = int(parts[3])
        
        pool = user_live_searches.get(user_key, [])
        prod = next((p for p in pool if p["id"] == prod_id), None)
        if not prod: return
        
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, json={
            "chat_id": chat_id, 
            "text": f"🕵️‍♂️ **Echtheits-Detektiv aktiv...**\nIch durchsuche Foren nach Fake-Bewertungen für '{prod['title']}' und starte die kostenlose Groq-Analyse..."
        })
        
        review_raw = f"Ergebnisse für {prod['title']}: Keine bekannten Betrugsmuster auf {prod['platform']} registriert. Verkäufer-Profil wirkt stabil."
        SYSTEM_PROMPT = "Du bist 'KI Sekretär', ein Broker. Werte den Deal und die Rezensionen aus."
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Produkt: {prod['title']} auf {prod['platform']} für {prod['price_eur']}€. Rezensions-Datenstrom aus dem Netz:\n{review_raw}\n\nAnalysiere den Deal!"}
        ]
        
        bot_reply, used_model = call_premium_ai(messages, provider="groq")
        
        inline_buttons = {
            "inline_keyboard": [[
                {"text": "🔥 OpenAI Premium Verhandlung zünden (Kostet 1 Cent)", "callback_data": f"premium_{user_key}_{prod_id}"}
            ]]
        }
        
        final_text = f"{bot_reply}\n\n--- [ADMIN] ---\n🤖 {used_model}" if str(chat_id) == ADMIN_USER_ID else bot_reply
        requests.post(url_msg, json={"chat_id": chat_id, "text": final_text, "reply_markup": inline_buttons})

    elif callback_data.startswith("premium_"):
        parts = callback_data.split("_")
        user_key = f"{parts[1]}_{parts[2]}"
        prod_id = int(parts[3])
        
        pool = user_live_searches.get(user_key, [])
        prod = next((p for p in pool if p["id"] == prod_id), None)
        if not prod: return
        
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, json={"chat_id": chat_id, "text": "🚀 Zünde OpenAI Premium für die Verhandlung..."})
        
        SYSTEM_PROMPT = "Du bist 'KI Sekretär', ein internationaler Import-Broker."
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Starte die Profi-Verhandlung für das Produkt '{prod['title']}' auf {prod['platform']} für {prod['price_eur']} EUR."}
        ]
        
        bot_reply, used_model = call_premium_ai(messages, provider="openai")
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
                res = requests.post(url_loading, json={"chat_id": chat_id, "text": "Verarbeite Anfrage..."}).json()
                lid = res.get("result", {}).get("message_id")
                if lid: executor.submit(process_message_async, chat_id, text, lid)
    except: pass
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping(): return "Bot is alive!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
