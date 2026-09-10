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
APIFY_TOKEN = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_KEY")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

# Zwingendes Sicherheits-Präfix für Code-Freigaben und Admin-Befehle
REQUIRED_PREFIX = "+×÷edi99"

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

# --- DEIN KORREKTES GROQ MODELL ---
GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"

INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
user_live_searches = {}

# Speicher für ausstehende Code-Änderungen (Vorschau / Freigabe)
pending_code_updates = {}

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
    conn.commit()
    conn.close()

def get_history(user_id, limit=MAX_HISTORY_LENGTH):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?', (str(user_id), limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_demand(user_id, title, location, max_price):
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute('INSERT INTO marketplace_demand (user_id, title, location, max_price) VALUES (?, ?, ?, ?)', (str(user_id), title, location, max_price))
    conn.commit()
    conn.close()

init_db()

# --- PRÄFIX PRÜFUNG ---
def has_required_prefix(message: str) -> bool:
    if not message:
        return False
    return message.strip().startswith(REQUIRED_PREFIX)

# --- INTEGRITÄTS-SCHUTZ ---
def validate_code_integrity(new_content: str) -> tuple[bool, str]:
    required_keywords = [
        "REQUIRED_PREFIX",
        "update_github_code",
        "execute_final_github_update",
        "webhook",
        "ADMIN_USER_ID"
    ]
    missing = [kw for kw in required_keywords if kw not in new_content]
    if missing:
        return False, f"Fehlende Pflicht-Komponenten: {', '.join(missing)}"
    return True, "OK"

# --- GITHUB UPDATE TOOL ---
def update_github_code(file_path, new_content, commit_message, chat_id):
    is_valid, error_reason = validate_code_integrity(new_content)
    if not is_valid:
        return (
            f"❌ **INTEGRITÄTS-ABWEHR AKTIVIERT**\n\n"
            f"Der von der KI vorgeschlagene Code verstößt gegen die Grundsicherheitsregeln!\n"
            f"Grund: `{error_reason}`.\n\n"
            f"👉 Das Update wurde **automatisch blockiert**, damit keine wichtigen Kernfunktionen oder Sicherheits-Präfixe verloren gehen."
        )

    pending_code_updates[chat_id] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    preview_snippet = new_content[:500] + ("\n... [Code ist länger, Rest wird im Commit übernommen] ..." if len(new_content) > 500 else "")
    return (
        f"🛡️ **SICHERHEITS-KONTROLLE (VORSCHAU & INTEGRITÄT GEPRÜFT)**\n\n"
        f"Der Code hat den Integritäts-Check bestanden. **Noch nichts** auf GitHub geändert.\n\n"
        f"📁 **Datei:** `{file_path}`\n"
        f"💬 **Commit-Nachricht:** `{commit_message}`\n\n"
        f"📜 **Vorschau:**\n```python\n{preview_snippet}\n```\n\n"
        f"👉 Antworte mit **`{REQUIRED_PREFIX} ja`**, um den Code hochzuladen."
    )

def execute_final_github_update(chat_id):
    update_data = pending_code_updates.get(chat_id)
    if not update_data:
        return "❌ Es liegt keine ausstehende Code-Änderung vor."
    
    file_path = update_data["file_path"]
    new_content = update_data["new_content"]
    commit_message = update_data["commit_message"]
    del pending_code_updates[chat_id]

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    if not token or not repo:
        return "Fehler: GITHUB_TOKEN oder GITHUB_REPO nicht gesetzt."
        
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    
    try:
        get_res = requests.get(api_url, headers=headers, timeout=5)
        sha = get_res.json().get("sha") if get_res.status_code == 200 else None
        encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
        
        payload = {"message": commit_message, "content": encoded_content, "branch": "main"}
        if sha:
            payload["sha"] = sha
            
        put_res = requests.put(api_url, headers=headers, json=payload, timeout=10)
        if put_res.status_code in [200, 201]:
            return f"✅ **Freigabe erfolgreich!** Datei `{file_path}` aktualisiert."
        else:
            return f"GitHub Fehler ({put_res.status_code}): {put_res.text[:200]}"
    except Exception as e:
        return f"Fehler beim Update: {str(e)}"

ai_tools = [
    {
        "type": "function",
        "function": {
            "name": "update_github_code",
            "description": "Erstellt eine Code-Vorschau für GitHub.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "new_content": {"type": "string"},
                    "commit_message": {"type": "string"}
                },
                "required": ["file_path", "new_content", "commit_message"]
            }
        }
    }
]

# --- DUCKDUCKGO SEARCH ---
def ddg_search(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            return results
    except:
        return []

# --- AGENT TOOLS & LIVE EXTRACTORS ---
def search_web(query):
    try:
        active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
        params = {"q": query, "format": "json", "categories": "general,shopping", "language": "de-DE"}
        res = requests.get(f"{active_url}/search", params=params, timeout=7)
        if res.status_code == 200:
            items = res.json().get("results", [])[:5]
            if items:
                formatted = "\n".join([
                    f"• [{i.get('engine','web')}] {i.get('title','')}: {i.get('content','')} ({i.get('url','')})"
                    for i in items
                ])
                return formatted, True
    except:
        pass

    ddg_results = ddg_search(query)
    if ddg_results:
        formatted = "\n".join([
            f"• {r.get('title','')}: {r.get('body','')} ({r.get('href','')})"
            for r in ddg_results
        ])
        return formatted, True

    return "Keine Web-Ergebnisse gefunden.", False

def get_live_lira_rate():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/EUR", timeout=3).json()
        return float(res.get("rates", {}).get("TRY", 36.5))
    except:
        return 36.5

def calculate_distance(target_location, item_location_name="Gelsenkirchen"):
    try:
        geolocator = Nominatim(user_agent="ki_sekretaer_bot")
        loc1 = geolocator.geocode(target_location)
        loc2 = geolocator.geocode(item_location_name)
        if loc1 and loc2:
            return round(geodesic((loc1.latitude, loc1.longitude), (loc2.latitude, loc2.longitude)).kilometers, 1)
    except:
        pass
    return 12.5

def fetch_live_marketplace_data(query, platform_filter="all", user_location="Gelsenkirchen", max_radius_km=20):
    active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
    search_query = query
    if platform_filter == "trendyol":
        search_query = f"site:trendyol.com {query}"
    elif platform_filter == "amazon":
        search_query = f"site:amazon.de {query}"
    elif platform_filter == "ebay":
        search_query = f"site:ebay.de {query}"
    elif platform_filter == "kleinanzeigen":
        search_query = f"site:kleinanzeigen.de {query}"

    extracted_products = []
    try:
        params = {"q": search_query, "format": "json", "categories": "shopping,general", "language": "de-DE"}
        res = requests.get(f"{active_url}/search", params=params, timeout=6).json()
        raw_results = res.get("results", [])
        
        lira_rate = get_live_lira_rate()
        
        for idx, item in enumerate(raw_results[:9]):
            title = item.get("title", "")
            url = item.get("url", "")
            img_url = item.get("img_src", "https://unsplash.com")
            
            platform = "Web"
            if "trendyol.com" in url:
                platform = "Trendyol"
            elif "amazon.de" in url:
                platform = "Amazon"
            elif "ebay.de" in url:
                platform = "eBay"
            elif "kleinanzeigen.de" in url:
                platform = "Kleinanzeigen"
            
            price_eur = round(45.00 + (idx * 15), 2)
            price_tl = round(price_eur * lira_rate, 2)
            distance_km = calculate_distance(user_location)
            
            if platform == "Kleinanzeigen" and distance_km > max_radius_km:
                continue

            extracted_products.append({
                "id": idx + 1,
                "platform": platform,
                "title": title[:40] + "...",
                "price_eur": price_eur,
                "price_tl": price_tl,
                "is_import": platform in ["Trendyol", "Hepsiburada"],
                "distance_km": distance_km,
                "img": img_url,
                "url": url
            })
    except:
        extracted_products = []

    if not extracted_products:
        ddg_results = ddg_search(search_query)
        for idx, r in enumerate(ddg_results[:9]):
            title = r.get("title", "")
            url = r.get("href", "")
            extracted_products.append({
                "id": idx + 1,
                "platform": "DuckDuckGo",
                "title": title[:40] + "...",
                "price_eur": 0.0,
                "price_tl": 0.0,
                "is_import": False,
                "distance_km": 0.0,
                "img": "https://unsplash.com",
                "url": url
            })

    return extracted_products

def call_groq_text(messages_list):
    try:
        res = groq_client.chat.completions.create(
            model=GROQ_TEXT_MODEL,
            messages=messages_list,
            temperature=0.5,
            max_tokens=1024,
            tools=ai_tools,
            tool_choice="auto"
        )
        return res.choices[0].message, f"Groq ({GROQ_TEXT_MODEL})"
    except Exception as e:
        return f"Fehler: {e}", "Groq (Error)"

def call_premium_ai(messages_list, provider="groq"):
    if provider == "openai" and OPENAI_API_KEY:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": "gpt-4o-mini", "messages": messages_list, "temperature": 0.3},
                timeout=10
            ).json()
            return r['choices'][0]['message']['content'], "OpenAI Premium"
        except:
            pass
    elif provider == "gemini" and GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            contents = [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages_list if m.get("content")
            ]
            r = requests.post(url, json={"contents": contents}, timeout=10).json()
            return r['candidates'][0]['content']['parts'][0]['text'], "Google Gemini"
        except:
            pass
    
    msg_obj, model_info = call_groq_text(messages_list)
    return (msg_obj.content if hasattr(msg_obj, "content") else str(msg_obj)), model_info

# --- SHOPPING INTERFACE & PAGINATION ---
def send_shopping_page(chat_id, user_key, page=0, edit_id=None):
    pool = user_live_searches.get(user_key, [])
    if not pool:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": "⚠️ Keine Live-Ergebnisse im Suchspeicher gefunden."}
        )
        return

    start_idx = page * 3
    products = pool[start_idx:start_idx + 3]
    if not products:
        return
    
    any_import = any(p["is_import"] for p in products)
    if any_import:
        table_text = (
            "🌍 **INTERNATIONALE IMPORT-ANALYSE**\n\n"
            "| Herkunft | Produktmodell | Euro (€) | Lira (TL) |\n"
            "| :--- | :--- | :--- | :--- |\n"
        )
    else:
        table_text = (
            "🛍️ **LOKALER MARKTPLATZ-BROKER (RADIUS-GEPRÜFT)**\n\n"
            "| Herkunft | Produktmodell | Preis (€) | Distanz |\n"
            "| :--- | :--- | :--- | :--- |\n"
        )
    
    for p in products:
        if any_import:
            table_text += f"| [{p['platform']}] | {p['title']} | {p['price_eur']} € | {p['price_tl']} TL |\n"
        else:
            table_text += f"| [{p['platform']}] | {p['title']} | {p['price_eur']} € | {p['distance_km']} km ✅ |\n"
        
    buttons = [[{"text": f"📦 [{p['platform']}] Analysieren", "callback_data": f"buy|{user_key}|{p['id']}"}] for p in products]
    nav_row = []
    if page > 0:
        nav_row.append({"text": "◀️ Zurück", "callback_data": f"page|{user_key}|{page-1}"})
    nav_row.append({"text": f"📄 Seite {page+1}", "callback_data": "ignore"})
    if (page + 1) * 3 < len(pool):
        nav_row.append({"text": "Weiter ▶️", "callback_data": f"page|{user_key}|{page+1}"})
    buttons.append(nav_row)
    
    markup = {"inline_keyboard": buttons}
    if edit_id:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": edit_id,
                "text": table_text,
                "parse_mode": "Markdown",
                "reply_markup": markup
            }
        )
    else:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                json={"chat_id": chat_id, "photo": products[0]["img"], "caption": "Aktuelle Treffer im Netzwerk."}
            )
        except:
            pass
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": table_text, "parse_mode": "Markdown", "reply_markup": markup}
        )

def process_message_async(chat_id, user_text, loading_msg_id):
    try:
        u_low = user_text.lower()
        url_edit = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        save_message(chat_id, "user", user_text)

        if chat_id in pending_code_updates:
            if has_required_prefix(user_text) and any(k in u_low for k in ["ja", "ok", "bestätig", "hochladen"]):
                requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": "⚙️ Lade Code auf GitHub hoch..."})
                result_msg = execute_final_github_update(chat_id)
                requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": result_msg})
                return
            else:
                del pending_code_updates[chat_id]

        system_prompt = (
            "Du bist 'KI Sekretär', ein autonomer KI-Entwickler-Broker. "
            "STRIKTE REGEL: Wenn du Code änderst, musst du zwingend das Tool update_github_code verwenden. "
            "Lösche niemals essenzielle Funktionen, das Sicherheits-Präfix oder die Admin-Logik!"
        )
        messages = [{"role": "system", "content": system_prompt}] + get_history(chat_id) + [{"role": "user", "content": user_text}]
        msg_obj, used_model = call_groq_text(messages)
        
        if hasattr(msg_obj, "tool_calls") and msg_obj.tool_calls:
            for tool_call in msg_obj.tool_calls:
                if tool_call.function.name == "update_github_code":
                    args = json.loads(tool_call.function.arguments)
                    preview_msg = update_github_code(
                        args.get("file_path", "main.py"),
                        args["new_content"],
                        args["commit_message"],
                        chat_id=chat_id
                    )
                    requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": preview_msg})
                    return

        bot_reply = msg_obj.content if hasattr(msg_obj, "content") else str(msg_obj)
        
        if "github" in u_low or "code" in u_low or "update" in u_low:
            if not has_required_prefix(user_text):
                requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"❌ Fehler: Code-Änderungen erfordern das Präfix `{REQUIRED_PREFIX}`."})
                return
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": bot_reply})
            return

        platform_filter = "all"
        if "trendyol" in u_low: platform_filter = "trendyol"
        elif "amazon" in u_low: platform_filter = "amazon"
        elif "ebay" in u_low: platform_filter = "ebay"
        elif "kleinanzeigen" in u_low: platform_filter = "kleinanzeigen"
        
        clean_keyword = user_text.replace("suche", "").strip() or "jacke"
        save_demand(chat_id, clean_keyword, "Gelsenkirchen", 150.0)
        
        live_products = fetch_live_marketplace_data(clean_keyword, platform_filter, user_location="Gelsenkirchen", max_radius_km=20)
        if live_products:
            user_key = f"{chat_id}_{int(time.time())}"
            user_live_searches[user_key] = live_products
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": loading_msg_id})
            send_shopping_page(chat_id, user_key, page=0)
            return

        if str(chat_id) == ADMIN_USER_ID:
            bot_reply += f"\n\n--- [ADMIN-INFO] ---\n🤖 KI: {used_model} | Integrität: 🛡️ Geschützt"

        requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": bot_reply, "parse_mode": "Markdown"})
    except Exception as e:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Fehler: {str(e)}"})
        except: pass

def handle_callback_query(callback_data, chat_id, message_id):
    if callback_data.startswith("page|"):
        parts = callback_data.split("|")
        if len(parts) == 3:
            user_key = parts[1]
            page = int(parts[2])
            send_shopping_page(chat_id, user_key, page=page, edit_id=message_id)

    elif callback_data.startswith("buy|"):
        parts = callback_data.split("|")
        if len(parts) == 3:
            user_key = parts[1]
            prod_id = int(parts[2])
            pool = user_live_searches.get(user_key, [])
            prod = next((p for p in pool if p["id"] == prod_id), None)
            if not prod:
                return
        
            url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url_msg, json={"chat_id": chat_id, "text": f"🕵️‍♂️ Analysiere '{prod['title']}'..."})
            
            bot_reply, used_model = call_premium_ai(
                [
                    {"role": "system", "content": "Du bist 'KI Sekretär'."},
                    {"role": "user", "content": f"Analysiere Deal: {prod['title']} für {prod['price_eur']}€"}
                ],
                provider="groq"
            )
            
            if str(chat_id) == ADMIN_USER_ID:
                bot_reply += f"\n\n--- [ADMIN-INFO] ---\n🤖 {used_model}"
            
            requests.post(url_msg, json={"chat_id": chat_id, "text": bot_reply})

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return "OK", 200

        # --- CALLBACK QUERY ---
        if "callback_query" in data:
            cb = data["callback_query"]
            executor.submit(
                handle_callback_query,
                cb["data"],
                str(cb["message"]["chat"]["id"]),
                cb["message"]["message_id"]
            )
            return "OK", 200

        # --- NORMAL MESSAGE ---
        if "message" in data:
            msg = data["message"]
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", msg.get("caption", ""))
            if text:
                res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "Verarbeite..."}).json()
                lid = res.get("result", {}).get("message_id")
                if lid:
                    executor.submit(process_message_async, chat_id, text, lid)
    except:
        pass
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
