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

INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 15
DB_PATH = os.getenv("DB_PATH", "bot_memory.db")
pending_code_updates = {}

# --- APIFY ACTORS ---
APIFY_ACTORS = {
    "apify_amazon": "junglee/free-amazon-product-scraper",
    "apify_google": "scraperlink/google-search-results-serp-scraper",
    "apify_ebay": "automation-lab/ebay-scraper",
}


# --- DATABASE INITIALIZATION & HELPERS ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT,
                fact_key TEXT,
                fact_value TEXT,
                PRIMARY KEY (user_id, fact_key)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS marketplace_demand (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                title TEXT,
                location TEXT,
                max_price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database Initialization Error: {e}")

def save_message(user_id, role, content):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)',
            (str(user_id), role, content)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving message: {e}")

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
    multiplier = MARKUP.get(user_level, 2.0)
    return round(base_cost * multiplier, 4)


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
    missing_keywords = [kw for kw in required_keywords if kw not in new_content]
    if missing_keywords:
        return False, f"Fehlende Pflicht-Komponenten: {', '.join(missing_keywords)}"
    return True, "OK"

def update_github_code(file_path: str, new_content: str, commit_message: str, chat_id: str) -> str:
    is_valid, error_reason = validate_code_integrity(new_content)
    if not is_valid:
        return f"❌ **INTEGRITÄTS-ABWEHR AKTIVIERT**\nGrund: `{error_reason}`. Update verweigert."

    pending_code_updates[str(chat_id)] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    
    preview_snippet = new_content[:500] + ("\n... [Code ist länger] ..." if len(new_content) > 500 else "")
    return (
        "🛡️ **SICHERHEITS-KONTROLLE AKTIV**\n\n"
        f"📁 **Ziel-Datei:** `{file_path}`\n"
        f"💬 **Commit-Nachricht:** `{commit_message}`\n\n"
        f"**Code-Vorschau:**\n```python\n{preview_snippet}\n```\n\n"
        f"👉 Antworte jetzt mit **`{REQUIRED_PREFIX} ja`**, um den Code endgültig zu übertragen."
    )

def execute_final_github_update(chat_id: str) -> str:
    update_data = pending_code_updates.get(str(chat_id))
    if not update_data:
        return "❌ Es liegt keine ausstehende Code-Änderung für dich vor."
    
    file_path = update_data["file_path"]
    new_content = update_data["new_content"]
    commit_message = update_data["commit_message"]
    del pending_code_updates[str(chat_id)]

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    
    if not token or not repo:
        return "❌ Fehler: `GITHUB_TOKEN` oder `GITHUB_REPO` sind nicht in den Umgebungsvariablen gesetzt."
        
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
            return f"✅ **Freigabe erfolgreich!** Die Datei `{file_path}` wurde auf GitHub aktualisiert."
        else:
            return f"❌ GitHub API Fehler ({put_res.status_code}): {put_res.text[:300]}"
    except Exception as e:
        return f"❌ Schwerwiegender Fehler beim GitHub-Update: {str(e)}"


# --- APIFY RUN FUNKTION (Mit strengem 10-Sekunden-Timeout) ---
def run_apify(source_key: str, query: str):
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN ist leer – bitte in Render eintragen.")

    actor_id = APIFY_ACTORS.get(source_key)
    if not actor_id:
        raise ValueError(f"Unbekannter Actor Key: {source_key}")
        
    formatted_actor_id = actor_id.replace('/', '~')
    url = f"https://api.apify.com/v2/acts/{formatted_actor_id}/run-sync?token={APIFY_TOKEN}"

    if source_key == "apify_amazon":
        payload = {
            "categoryOrProductUrls": [f"https://www.amazon.de/s?k={query}"],
            "maxItemsPerStartUrl": 10,
            "scrapeProductDetails": False
        }
    elif source_key == "apify_google":
        payload = {
            "queries": [query],
            "maxPagesPerQuery": 1
        }
    else:
        payload = {
            "search": query,
            "maxItems": 10
        }

    # Strenger Timeout von 10 Sekunden, damit der Bot blitzschnell reagiert
    r = requests.post(url, json=payload, timeout=10)
    
    if r.status_code not in [200, 201]:
        raise RuntimeError(f"Apify HTTP {r.status_code}: {r.text[:300]}")
        
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


# --- DISPATCHER & INTENT RECOGNITION ---
def is_product_query(query: str) -> bool:
    product_keywords = [
        "kaufen", "preis", "kosten", "produkt", "angebot",
        "airpods", "iphone", "samsung", "dyson", "ps5",
        "headset", "kopfhörer", "monitor", "tv", "fernseher",
        "google", "amazon", "ebay", "suche", "bestellen"
    ]
    q = query.lower()
    return any(k in q for k in product_keywords)

def dispatcher(query: str, user_id: str):
    user_level = get_user_level(user_id)
    apify_errors = []

    if is_product_query(query):
        # Wir versuchen nur kurz den eBay oder Google Scraper, um Endlos-Schleifen zu vermeiden
        for src in ["apify_ebay", "apify_google"]:
            try:
                apify_data, apify_cost_usd = run_apify(src, query)
                apify_cost_eur = round(apify_cost_usd, 4)
                final_price_for_user = calculate_price_with_markup(apify_cost_eur, user_level)

                items = (
                    apify_data.get("items")
                    or apify_data.get("results")
                    or apify_data.get("data")
                    or apify_data.get("products")
                    or (apify_data if isinstance(apify_data, list) else None)
                )

                if apify_data is not None:
                    item_count = len(items) if isinstance(items, list) else "Verfügbar"
                    return {
                        "status": "success",
                        "layer": "paid",
                        "source": src,
                        "cost_admin": apify_cost_eur,
                        "cost_user": final_price_for_user,
                        "results": items if items else apify_data,
                        "message": (
                            f"🚀 **Marktplatz-Daten via {src}**\n\n"
                            f"Admin-Kosten: {apify_cost_eur} $\n"
                            f"Dein Preis: {final_price_for_user} $\n"
                            f"Treffer gefunden: {item_count}."
                        ),
                    }
            except Exception as e:
                err_str = str(e)
                print(f"CRITICAL APIFY ERROR ({src}): {err_str}")
                apify_errors.append(f"{src}: {err_str}")

    # Fallback greift jetzt sofort nach max. 10 Sekunden Timeout
    searxng_res = search_searxng(query)
    ddgs_res = search_ddgs(query)
    error_details = "\n".join(apify_errors) if apify_errors else "Timeout / Unbekannter Fehler"

    if searxng_res or ddgs_res:
        return {
            "status": "success",
            "layer": "free",
            "source": ["searxng", "ddgs"],
            "cost_admin": 0.0,
            "cost_user": 0.0,
            "results": {"searxng": searxng_res, "ddgs": ddgs_res},
            "message": f"⚠️ **Apify Timeout/Fehler, Fallback aktiv!**\n\nDetails:\n{error_details}",
        }

    return {
        "status": "error",
        "message": f"❌ **Alle Quellen fehlgeschlagen.**\n\nApify Fehler:\n{error_details}"
    }


# --- ASYNC MESSAGE PROCESSOR ---
def process_message_async(chat_id: str, user_text: str, loading_msg_id: int):
    try:
        u_low = user_text.lower()
        url_edit = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        save_message(chat_id, "user", user_text)

        if str(chat_id) in pending_code_updates:
            if has_required_prefix(user_text) and any(k in u_low for k in ["ja", "ok", "bestätig", "hochladen"]):
                requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": "⚙️ Lade Code auf GitHub hoch..."})
                result_msg = execute_final_github_update(chat_id)
                requests.post(url_edit, json={"chat_id": chat_id, "message_id": loading_msg_id, "text": result_msg})
                return
            else:
                del pending_code_updates[str(chat_id)]

        greetings = ["hallo", "hi", "guten morgen", "guten tag", "moin", "servus", "hey"]
        if any(g in u_low for g in greetings):
            requests.post(
                url_edit,
                json={
                    "chat_id": chat_id,
                    "message_id": loading_msg_id,
                    "text": "Guten Tag! Als Marketplace Broker suche ich gerne nach Produkten für dich."
                }
            )
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
        print(f"Error in process_message_async: {e}")
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                json={"chat_id": chat_id, "message_id": loading_msg_id, "text": f"Ein Fehler ist aufgetreten: {str(e)}"}
            )
        except Exception:
            pass


# --- FLASK WEBHOOK & SERVER ---
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
    except Exception as e:
        print(f"Webhook error: {e}")
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    port_val = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port_val)
