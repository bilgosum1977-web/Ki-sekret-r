#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import json
import base64
import concurrent.futures
from flask import Flask, request
import requests
from groq import Groq

# Sicherer Import für DuckDuckGo Search (ddgs)
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

app = Flask(__name__)

# --- CONFIGURATION & KEYS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "8874543115")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_KEY")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

REQUIRED_PREFIX = "+×÷edi99"

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

INITIAL_BALANCE, MAX_HISTORY_LENGTH, DB_PATH = 10000, 15, os.getenv("DB_PATH", "bot_memory.db")
pending_code_updates = {}

# --- APIFY ACTORS ---
APIFY_ACTORS = {
    "apify_amazon": "junglee/amazon-crawler",
    "apify_google_shopping": "apify/google-shopping-scraper",
    "apify_ebay": "maxcopell/ebay-scraper",
}


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

init_db()


# --- USER MODEL & COST LOGIC ---
def get_user_level(user_id: str) -> str:
    return "free"

def calculate_price_with_markup(base_cost: float, user_level: str) -> float:
    MARKUP = {
        "free": 2.0,
        "pro": 1.5,
        "vip": 1.0,
    }
    return round(base_cost * MARKUP.get(user_level, 2.0), 4)


# --- CODE INTEGRITY & GITHUB UPDATES ---
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
        return f"❌ **INTEGRITÄTS-ABWEHR AKTIVIERT**\nGrund: `{error_reason}`."

    pending_code_updates[chat_id] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    preview_snippet = new_content[:500] + ("\n... [Code ist länger] ..." if len(new_content) > 500 else "")
    return (
        "🛡️ **SICHERHEITS-KONTROLLE**\n\n"
        f"📁 **Datei:** `{file_path}`\n\n```python\n" + preview_snippet + "\n```\n\n"
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


# --- APIFY RUN FUNKTION ---
def run_apify(source_key: str, query: str):
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN ist leer – bitte in Render eintragen.")

    actor_id = APIFY_ACTORS[source_key]
    formatted_actor_id = actor_id.replace('/', '~')
    url = f"https://api.apify.com/v2/acts/{formatted_actor_id}/run-sync?token={APIFY_TOKEN}"

    payload = {
        "search": query,
        "keyword": query,
        "queries": [query],
        "maxItems": 20
    }

    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    usage = data.get("usage", {})
    usd = float(usage.get("totalUsd", 0.0))

    return data, usd


# --- FALLBACK SCRAPERS ---
def search_searxng(query: str):
    url = f"{SEARXNG_URL}/search?q={query}&format=json"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:
        return None

def search_ddgs(query: str):
    if not DDGS:
        return None
    try:
        with DDGS(timeout=5) as ddgs:
            results = list(ddgs.text(f"{query} preis kaufen", max_results=5))
            return results
    except Exception:
        return None


# --- DISPATCHER ---
def is_product_query(query: str) -> bool:
    product_keywords = [
        "kaufen", "preis", "kosten", "produkt", "angebot",
        "airpods", "iphone", "samsung", "dyson", "ps5",
        "headset", "kopfhörer", "monitor", "tv", "fernseher",
        "google shopping", "amazon", "ebay", "suche"
    ]
    q = query.lower()
    return any(k in q for k in product_keywords)

def dispatcher(query: str, user_id: str):
    user_level = get_user_level(user_id)

    if is_product_query(query):
        for src in ["apify_amazon", "apify_google_shopping", "apify_ebay"]:
            try:
                apify_data, apify_cost_usd = run_apify(src, query)
                apify_cost_eur = round(apify_cost_usd, 4)
                final_price_for_user = calculate_price_with_markup(apify_cost_eur, user_level)

                items = apify_data.get("items", apify_data)
                return {
                    "status": "success",
                    "layer": "paid",
                    "source": src,
                    "cost_admin": apify_cost_eur,
                    "cost_user": final_price_for_user,
                    "results": items,
                    "message": (
                        f"🚀 **Marktplatz-Daten via {src}**\n\n"
                        f"Admin-Kosten: {apify_cost_eur} $\n"
                        f"Dein Preis: {final_price_for_user} $\n"
                        f"Treffer gefunden: {len(items) if isinstance(items, list) else 'Verfügbar'}."
                    ),
                }
            except Exception as e:
                print(f"CRITICAL APIFY ERROR ({src}): {str(e)}")
                continue

    # Fallback
    searxng_res = search_searxng(query)
    ddgs_res = search_ddgs(query)

    if searxng_res or ddgs_res:
        return {
            "status": "success",
            "layer": "free",
            "source": ["searxng", "ddgs"],
            "cost_admin": 0.0,
            "cost_user": 0.0,
            "results": {"searxng": searxng_res, "ddgs": ddgs_res},
            "message": "Kostenlose Ergebnisse aus SearXNG/DDGS (Apify-Fallback aktiv).",
        }

    return {
        "status": "error",
        "message": "Keine Quelle lieferte Ergebnisse (Apify-Fehler und Fallback blieben leer)."
    }


# --- ASYNC MESSAGE PROCESSOR ---
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
            requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": "Guten Tag! Als Marketplace Broker suche ich gerne nach Produkten für dich."})
            return

        dispatch_res = dispatcher(user_text, chat_id)
        bot_reply = dispatch_res.get("message", "Keine Daten gefunden.")

        requests.post(
            url_edit,
            json={
                "chat_id": chat_id,
                "message_id": loading_msg_id,
                "text": bot_reply,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
        )
    except Exception as e:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Fehler aufgetreten: {str(e)}"}
            )
        except:
            pass


# --- FLASK WEBHOOK ---
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return "OK", 200

        if "message" in data:
            msg = data["message"]
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", msg.get("caption", ""))
            if text:
                res = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": "⏳ Verarbeite Anfrage..."}
                ).json()
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
