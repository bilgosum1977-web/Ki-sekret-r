#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import json
import base64
import time
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


# --- ASYNCHRONER APIFY ACTOR MIT POLLING ---
def run_apify_actor(query: str, actor_id: str = "junglee~free-amazon-product-scraper"):
    if not APIFY_TOKEN:
        return None

    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={APIFY_TOKEN}&waitForFinish=0"

    payload = {"maxItems": 5}
    if "ebay" in actor_id.lower():
        payload["searchKeyword"] = query
    elif "google" in actor_id.lower():
        payload["queries"] = query
    else:
        payload["search"] = query

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        
        run_data = response.json().get("data", {})
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")
        
        if not run_id or not dataset_id:
            return None

        # Status-Polling (Max. 60 Sekunden warten, alle 5 Sekunden prüfen)
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        
        for _ in range(12):
            time.sleep(5)
            status_response = requests.get(status_url, timeout=10)
            status_response.raise_for_status()
            
            run_status = status_response.json().get("data", {}).get("status")
            
            if run_status == "SUCCEEDED":
                dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
                data_response = requests.get(dataset_url, timeout=15)
                data_response.raise_for_status()
                return data_response.json()
                
            elif run_status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                return None
                
        return None
    except Exception as e:
        print(f"Apify Polling Fehler: {e}")
        return None


# --- KOSTENLOSE SUCHMASCHINEN (SearXNG & DuckDuckGo) ---
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
            return list(ddgs.text(f"{query} preis kaufen", max_results=5))
    except Exception:
        return None


# --- SMART DISPATCHER (Zuerst kostenlos, dann Fallback via Apify) ---
def dispatcher(query: str, user_id: str):
    # 1. Bevorzuge IMMER zuerst kostenlose Quellen (Parallel)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_searxng = executor.submit(search_searxng, query)
        future_ddgs = executor.submit(search_ddgs, query)
        
        searxng_res = future_searxng.result()
        ddgs_res = future_ddgs.result()

    response_lines = [f"🔍 **Suchergebnisse für:** *{query}*\n"]
    has_results = False

    if searxng_res:
        has_results = True
        response_lines.append("🌐 **SearXNG Treffer (Kostenlos):**")
        for item in searxng_res[:3]:
            title = item.get("title", "Kein Titel")
            link = item.get("url", "#")
            response_lines.append(f"• [{title}]({link})")
        response_lines.append("")

    if ddgs_res:
        has_results = True
        response_lines.append("🦆 **DuckDuckGo Treffer (Kostenlos):**")
        for item in ddgs_res[:3]:
            title = item.get("title", "Kein Titel")
            body = item.get("body", "")
            link = item.get("href", "#")
            response_lines.append(f"• [{title}]({link})\n  _{body[:80]}..._")
        response_lines.append("")

    if has_results:
        return "\n".join(response_lines)

    # 2. Fallback: Wenn Web-Suche leer bleibt, Apify Amazon Scraper nutzen
    try:
        apify_data = run_apify_actor(query)
        if apify_data and isinstance(apify_data, list):
            apify_lines = [f"🛒 **Amazon-Ergebnisse (Fallback):** *{query}*\n"]
            for item in apify_data[:5]:
                title = item.get("title", "Produkt")
                price = item.get("price", {}).get("display", item.get("price", "Preis auf Anfrage"))
                link = item.get("url", "#")
                apify_lines.append(f"• **{title}**\n  💰 Preis: {price}\n  🔗 [Zum Angebot]({link})\n")
            return "\n".join(apify_lines)
    except Exception as e:
        print(f"Apify Fallback Fehler: {e}")

    return "❌ Keine Ergebnisse gefunden."


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
                    "text": "Guten Tag! Ich durchsuche das Web und Amazon nach Produkten für dich."
                }
            )
            return

        bot_reply = dispatcher(user_text, chat_id)

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
                    json={"chat_id": chat_id, "text": "⏳ Suche läuft..."}
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
