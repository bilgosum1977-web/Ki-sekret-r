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


# --- ABSOLUT STABILES APIFY POLLING (REPARIERTE URL-SYNTAX) ---
def run_apify_actor(query: str, actor_id: str = "apify~amazon-crawler"):
    apify_token = os.getenv("APIFY_TOKEN")
    if not apify_token:
        print("❌ Apify-Fehler: APIFY_TOKEN ist nicht gesetzt!", flush=True)
        return None

    # URL-SYNTAX-REPARATUR: Tilde (~) zwingend durch Schrägstrich (/) ersetzen für die REST-API v2
    clean_actor_id = actor_id.replace("~", "/")
    url = f"https://api.apify.com/v2/acts/{clean_actor_id}/runs?waitForFinish=0"

    headers = {
        "Authorization": f"Bearer {apify_token}",
        "Content-Type": "application/json"
    }

    # Standard-Basis-Payload
    payload = {
        "maxItems": 5,
        "proxyConfiguration": {
            "useApifyProxy": True
        }
    }
    
    actor_id_lower = actor_id.lower()
    
    if "ebay" in actor_id_lower:
        payload["searchQueries"] = [query]
        payload["marketplace"] = "DE" 
        
    elif "amazon" in actor_id_lower:
        payload["searchKeywords"] = query
        payload["locationCode"] = "de"
        
    elif "google" in actor_id_lower:
        payload["queries"] = query
    else:
        payload["search"] = query

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        
        run_data = response.json().get("data", {})
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")
        
        if not run_id or not dataset_id:
            print(f"❌ Fehler: Start-Daten unvollständig für {actor_id}", flush=True)
            return None

        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        
        print(f"🚀 Scraper {actor_id} erfolgreich gestartet (Run-ID: {run_id}). Starte Polling...", flush=True)

        for attempt in range(18):
            time.sleep(5)
            
            status_response = requests.get(status_url, headers=headers, timeout=10)
            status_response.raise_for_status()
            
            run_status = status_response.json().get("data", {}).get("status")
            print(f"🤖 [{attempt+1}/18] Actor {actor_id} Status: {run_status}", flush=True)
            
            if run_status == "SUCCEEDED":
                print(f"✅ {actor_id} fertig! Hole Dataset-Items...", flush=True)
                data_response = requests.get(dataset_url, headers=headers, timeout=15)
                data_response.raise_for_status()
                return data_response.json()
                
            elif run_status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                print(f"⚠️ Apify Actor {actor_id} abgebrochen mit Status: {run_status}.", flush=True)
                return None
                
        print(f"⏱️ Apify Timeout: {actor_id} brauchte länger als 90 Sekunden.", flush=True)
        return None

    except Exception as e:
        print(f"❌ Schwerer Fehler beim Apify-Abruf ({actor_id}): {e}", flush=True)
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


# --- SMART DISPATCHER ---
def dispatcher(query: str, user_id: str, is_shopping: bool = False):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_searxng = executor.submit(search_searxng, query)
        future_ddgs = executor.submit(search_ddgs, query)
        
        searxng_res = future_searxng.result()
        ddgs_res = future_ddgs.result()

    response_lines = [f"🔍 **Suchergebnisse für:** *{query}*\n"]
    has_web_results = False

    if searxng_res:
        has_web_results = True
        response_lines.append("🌐 **SearXNG Treffer (Kostenlos):**")
        for item in searxng_res[:3]:
            title = item.get("title", "Kein Titel")
            link = item.get("url", "#")
            response_lines.append(f"• [{title}]({link})")
        response_lines.append("")

    if ddgs_res:
        has_web_results = True
        response_lines.append("🦆 **DuckDuckGo Treffer (Kostenlos):**")
        for item in ddgs_res[:3]:
            title = item.get("title", "Kein Titel")
            body = item.get("body", "")
            link = item.get("href", "#")
            response_lines.append(f"• [{title}]({link})\n  _{body[:80]}..._")
        response_lines.append("")

    apify_lines = []
    found_any_apify = False

    if is_shopping:
        actor_id_1 = "apify~amazon-crawler"
        actor_id_2 = "automation-lab~ebay-scraper"

        print(f"🛒 Shopping-Intent erkannt. Starte Apify-Actors für: {query}")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as apify_executor:
            future_apify_1 = apify_executor.submit(run_apify_actor, query, actor_id_1)
            future_apify_2 = apify_executor.submit(run_apify_actor, query, actor_id_2)
            
            data_1 = future_apify_1.result()
            data_2 = future_apify_2.result()

        # === AMAZON AUSWERTUNG ===
        if data_1 and isinstance(data_1, list):
            found_any_apify = True
            apify_lines.append("📦 **Amazon Angebote:**")
            
            for item in data_1[:3]:
                title = item.get("title") or item.get("name") or "Produkt ohne Titel"
                
                price_field = item.get("price")
                price = "Preis auf Anfrage"
                
                if isinstance(price_field, dict):
                    price = price_field.get("display") or price_field.get("value") or price_field.get("raw") or "Preis auf Anfrage"
                elif isinstance(price_field, (str, int, float)):
                    price = str(price_field)
                
                if isinstance(price_field, (int, float)) or (isinstance(price, str) and price.replace('.', '', 1).isdigit()):
                    price = f"{price} €"

                link = item.get("url") or item.get("link") or item.get("href") or "#"
                if link != "#" and link.startswith("/"):
                    link = f"https://amazon.de{link}"
                
                apify_lines.append(f"• {title[:60]}...\n  💰 *{price}* | 🔗 [Zum Shop]({link})")
            apify_lines.append("")

        # === EBAY AUSWERTUNG ===
        if data_2 and isinstance(data_2, list):
            found_any_apify = True
            apify_lines.append("🛒 **Weitere Angebote:**")
            
            for item in data_2[:3]:
                title = item.get("title") or item.get("name") or "Produkt"
                
                price_field = item.get("price")
                price = "Preis auf Anfrage"
                if isinstance(price_field, dict):
                    price = price_field.get("display") or price_field.get("value") or "Preis auf Anfrage"
                elif price_field:
                    price = str(price_field)
                    
                link = item.get("url") or item.get("link") or item.get("href") or "#"
                
                apify_lines.append(f"• {title[:60]}...\n  💰 *{price}* | 🔗 [Zum Shop]({link})")
            apify_lines.append("")
    else:
        print(f"🍃 Normaler Info-Request für '{query}'. Apify übersprungen.")

    if found_any_apify:
        response_lines.extend(apify_lines)
        return "\n".join(response_lines)
    elif has_web_results:
        if is_shopping:
            response_lines.append("⚠️ _Produktdaten von Amazon/eBay sind gerade nicht verfügbar._")
        return "\n".join(response_lines)

    return "❌ Keine Ergebnisse gefunden."


# --- ASYNCHRONER PROZESSOR MIT SICHERHEITSNETZ ---
def process_message_async(chat_id, query, message_id, is_shopping):
    print(f"🔄 Thread gestartet für Chat {chat_id} mit Query: '{query}' (Shopping: {is_shopping})", flush=True)
    try:
        result_text = dispatcher(query, chat_id, is_shopping)
        print(f"📋 Dispatcher-Ergebnis für '{query}': {result_text[:50]}...", flush=True)
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": result_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        print(f"✅ Telegram-Nachricht {message_id} erfolgreich editiert.", flush=True)

    except Exception as thread_error:
        print(f"❌ KRITISCHER FEHLER im Hintergrund-Thread: {thread_error}", flush=True)
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"❌ Interner Fehler bei der Verarbeitung: `{str(thread_error)}`",
                    "parse_mode": "Markdown"
                },
                timeout=10
            )
        except Exception:
            pass


# --- FLASK WEBHOOK MIT INTENT-ERKENNUNG ---
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
            raw_text = msg.get("text", msg.get("caption", ""))
            
            if raw_text:
                clean_query = raw_text.strip()
                is_shopping = False
                
                if clean_query.lower().startswith("suche "):
                    clean_query = clean_query[6:].strip()
                    is_shopping = True

                res = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": f"⏳ Suche nach: *{clean_query}*...", "parse_mode": "Markdown"}
                ).json()
                
                lid = res.get("result", {}).get("message_id")
                if lid:
                    executor.submit(process_message_async, chat_id, clean_query, lid, is_shopping)
    except Exception as e:
        print(f"Webhook error: {e}")
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    port_val = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port_val)
