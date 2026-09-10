import os, io, sqlite3, json, base64, time, threading, concurrent.futures, smtplib, re
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
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

REQUIRED_PREFIX = "+×÷edi99"

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"

INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
user_live_searches = {}
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

def has_required_prefix(message: str) -> bool:
    if not message:
        return False
    return message.strip().startswith(REQUIRED_PREFIX)

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

def update_github_code(file_path, new_content, commit_message, chat_id):
    is_valid, error_reason = validate_code_integrity(new_content)
    if not is_valid:
        return (
            "❌ **INTEGRITÄTS-ABWEHR AKTIVIERT**\n\n"
            "Der von der KI vorgeschlagene Code verstößt gegen die Grundsicherheitsregeln!\n"
            f"Grund: `{error_reason}`.\n\n"
            "👉 Das Update wurde **automatisch blockiert**, damit keine wichtigen Kernfunktionen oder Sicherheits-Präfixe verloren gehen."
        )

    pending_code_updates[chat_id] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    preview_snippet = new_content[:500] + ("\n... [Code ist länger, Rest wird im Commit übernommen] ..." if len(new_content) > 500 else "")
    return (
        "🛡️ **SICHERHEITS-KONTROLLE (VORSCHAU & INTEGRITÄT GEPRÜFT)**\n\n"
        "Der Code hat den Integritäts-Check bestanden. **Noch nichts** auf GitHub geändert.\n\n"
        f"📁 **Datei:** `{file_path}`\n"
        f"💬 **Commit-Nachricht:** `{commit_message}`\n\n"
        "📜 **Vorschau:**\n```python\n" + preview_snippet + "\n```\n\n"
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

# --- DUCKDUCKGO DDGS FALLBACK ---
def duckduckgo_fallback(keyword: str):
    items = []
    try:
        with DDGS(timeout=5) as ddgs:
            results = list(ddgs.text(f"{keyword} preis kaufen", max_results=5))
            for r in results:
                title = r.get("title", "")
                body = r.get("body", "")
                url = r.get("href", "")

                price = None
                m = re.search(r'(\d+[,\.]?\d*)\s?€', body + " " + title)
                if m:
                    price = float(m.group(1).replace(",", "."))

                items.append({
                    "title": title,
                    "price": price,
                    "currency": "EUR",
                    "image_url": None,
                    "platform": "duckduckgo",
                    "distance_km": None,
                    "condition": "unbekannt",
                    "seller": "unbekannt",
                    "url": url
                })
    except Exception:
        pass

    return items

def fetch_live_marketplace_data(keyword: str, platform_filter: str, user_location: str, max_radius_km: float):
    params = {
        "q": f"{keyword} preis kaufen",
        "categories": "shopping",
        "format": "json",
        "engines": "amazon,ebay,shopping"
    }

    results = []
    try:
        active_url = SEARXNG_URL if "localhost" not in SEARXNG_URL else "https://searx.be"
        r = requests.get(active_url, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
    except Exception:
        pass

    if not results:
        results = duckduckgo_fallback(keyword)

    return results

def extract_products(raw_results):
    products = []

    for r in raw_results:
        title = r.get("title") or r.get("name")
        if not title:
            continue

        body = r.get("body", "") or r.get("snippet", "")
        price = r.get("price")

        if price is None:
            m = re.search(r'(\d+[\.,]?\d*)\s?(?:€|EUR)', title + " " + body, re.IGNORECASE)
            if m:
                price = float(m.group(1).replace(".", "").replace(",", "."))

        if price is None:
            price = 0.0

        product = {
            "name": title,
            "price": price,
            "currency": r.get("currency", "EUR"),
            "image_url": r.get("image_url"),
            "platform": r.get("platform", "duckduckgo"),
            "distance_km": r.get("distance_km"),
            "condition": r.get("condition", "unbekannt"),
            "seller": r.get("seller", "unbekannt"),
            "url": r.get("url") or r.get("href", "")
        }

        products.append(product)

    return products

def normalize_price(price, currency: str):
    if price is None:
        return None
    if currency == "TRY":
        return round(price * 0.03, 2)
    return price

def call_groq_analysis(products, mode: str):
    messages = [
        {
            "role": "system",
            "content": (
                "Du lebst IMMER im aktuellen Datum (2026). "
                "Du nutzt IMMER die Live-Daten aus dem System. "
                "Du entscheidest NICHT selbst über Produkte, Preise oder Modelle. "
                "Du analysierst NUR die Daten, die dir das System liefert."
            )
        },
        {
            "role": "user",
            "content": f"Modus: {mode}\nAnalysiere diese Produkte:\n{products}"
        }
    ]

    try:
        r = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "mixtral-8x7b-32768",
                "messages": messages,
                "temperature": 0.2
            },
            timeout=15
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return "Analyse konnte nicht durchgeführt werden."

def send_shopping_page(chat_id: int, products, page: int = 0):
    text_lines = [f"Shopping-Ergebnisse (Seite {page}):"]
    for p in products[:5]:
        price_str = f"{p['price']} EUR" if p['price'] > 0 else "Preis unbekannt"
        text_lines.append(f"- {p['name']} ({p['platform']}) – {price_str}")
    text = "\n".join(text_lines)

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text}
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

        greetings = ["hallo", "hi", "guten morgen", "guten tag", "moin", "servus", "hey"]
        if any(g in u_low for g in greetings):
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": "Guten Tag! Wie kann ich dir helfen?"})
            return

        shopping_intents = ["suche", "kaufen", "preis", "angebot", "deal", "produkt", "modell"]
        compare_intents  = ["vergleiche", "vergleich", "vs"]
        analysis_intents = ["analysiere", "bewerte", "checke", "prüfe"]

        product_patterns = [
            "samsung", "galaxy", "iphone", "dyson", "ps5", "airpods", "xiaomi",
            "huawei", "oneplus", "ipad", "macbook", "lenovo", "asus", "sony"
        ]

        intent = None
        if any(kw in u_low for kw in shopping_intents) and any(p in u_low for p in product_patterns):
            intent = "shopping"
        elif any(kw in u_low for kw in compare_intents) and any(p in u_low for p in product_patterns):
            intent = "compare"
        elif any(kw in u_low for kw in analysis_intents) and any(p in u_low for p in product_patterns):
            intent = "analysis"

        if intent is None:
            system_prompt = "Du bist 'KI Sekretär', ein autonomer KI-Entwickler-Broker."
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
            if str(chat_id) == ADMIN_USER_ID:
                bot_reply += f"\n\n--- [ADMIN-INFO] ---\n🤖 KI: {used_model} | Integrität: 🛡️ Geschützt"

            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": bot_reply, "parse_mode": "Markdown"})
            return

        clean_keyword = u_low
        filler_words = ["suche", "neueste", "neuer", "neues", "kaufen", "preis", "angebot", "deal", "produkt", "modell", "bitte", "mal"]
        for kw in filler_words:
            clean_keyword = clean_keyword.replace(kw, "")
        for kw in shopping_intents + compare_intents + analysis_intents:
            clean_keyword = clean_keyword.replace(kw, "")
            
        clean_keyword = clean_keyword.strip()
        if not clean_keyword:
            clean_keyword = "airpods"

        save_demand(chat_id, clean_keyword, "Gelsenkirchen", 150.0)

        raw_results = fetch_live_marketplace_data(clean_keyword, "all", "Gelsenkirchen", 20)
        products = extract_products(raw_results)

        for p in products:
            p["price"] = normalize_price(p["price"], p.get("currency", "EUR"))

        if not products:
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Keine Produkte für '{clean_keyword}' gefunden."})
            return

        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": loading_msg_id})
        send_shopping_page(chat_id, products, page=0)

        analysis_text = call_groq_analysis(products, mode=intent)
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": analysis_text}
        )

    except Exception as e:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Fehler: {str(e)}"})
        except: pass

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

def handle_callback_query(callback_data, chat_id, message_id):
    pass

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return "OK", 200

        if "callback_query" in data:
            cb = data["callback_query"]
            executor.submit(
                handle_callback_query,
                cb["data"],
                str(cb["message"]["chat"]["id"]),
                cb["message"]["message_id"]
            )
            return "OK", 200

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
