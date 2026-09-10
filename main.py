#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import sqlite3
import json
import base64
import time
import threading
import concurrent.futures
import smtplib
import re
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

# --- APIFY ACTORS (ROBUSTE PFADE) ---
APIFY_ACTORS = {
    "apify_google_shopping": "apify/google-shopping-scraper",
    "apify_amazon": "apify/amazon-extractor",
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


# --- USER MODEL & COST LOGIC ---
def get_user_level(user_id: str) -> str:
    return "free"

COST_REGISTRY = {
    "searxng": {"type": "free"},
    "ddgs": {"type": "free"},
    "groq_analysis": {"type": "free"},
    "apify_amazon": {"type": "paid"},
    "apify_google_shopping": {"type": "paid"},
    "apify_ebay": {"type": "paid"},
}

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
        return (
            "❌ **INTEGRITÄTS-ABWEHR AKTIVIERT**\n\n"
            "Der von der KI vorgeschlagene Code verstößt gegen die Grundsicherheitsregeln!\n"
            f"Grund: `{error_reason}`.\n"
        )

    pending_code_updates[chat_id] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    preview_snippet = new_content[:500] + ("\n... [Code ist länger] ..." if len(new_content) > 500 else "")
    return (
        "🛡️ **SICHERHEITS-KONTROLLE (VORSCHAU GEPRÜFT)**\n\n"
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


# --- ROBUSTE APIFY & SCRAPING ENGINE ---
def run_apify(source_key: str, query: str):
    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN ist leer oder nicht gesetzt!")
        return None, 0.0

    actor_id = APIFY_ACTORS.get(source_key, "apify/google-shopping-scraper")
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync?token={APIFY_TOKEN}"
    
    # Universelles Payload für verschiedene Scraper-Typen
    payload = {
        "queries": [query],
        "searchString": query,
        "maxItems": 5
    }

    try:
        print(f"🔄 Starte Apify Actor {actor_id} für Abfrage: '{query}'")
        r = requests.post(url, json=payload, timeout=45)
        
        if r.status_code != 200:
            print(f"❌ Apify HTTP-Fehler {r.status_code}: {r.text[:300]}")
            return None, 0.0
            
        data = r.json()
        usage = data.get("data", {}).get("usage", {}) or data.get("usage", {})
        usd = float(usage.get("totalUsd", 0.0))
        items = data.get("data", {}).get("items") or data.get("items") or data
        
        if not items or not isinstance(items, list):
            print(f"⚠️ Apify hat keine gültigen Items zurückgeliefert: {str(data)[:200]}")
            return None, usd
            
        print(f"✅ Erfolgreich {len(items)} Items von Apify ({source_key}) geladen.")
        return items, usd
    except Exception as e:
        print(f"⚠️ Ausnahmefehler bei Apify-Quelle {source_key}: {e}")
        return None, 0.0

def search_searxng(query: str):
    url = f"http://localhost:4000/search?q={query}&format=json"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def search_ddgs(query: str):
    try:
        with DDGS(timeout=5) as ddgs:
            results = list(ddgs.text(f"{query} preis kaufen", max_results=5))
            return results
    except Exception:
        return None


# --- PRODUCT QUERY DETECTION & FORMATTING ---
def is_product_query(query: str) -> bool:
    product_keywords = [
        "kaufen", "preis", "kosten", "produkt", "angebot",
        "airpods", "iphone", "samsung", "dyson", "ps5",
        "headset", "kopfhörer", "monitor", "tv", "fernseher",
        "google shopping", "amazon", "ebay", "suche", "suche nach"
    ]
    q = query.lower()
    return any(k in q for k in product_keywords)

def format_products_for_telegram(items):
    if not items or not isinstance(items, list):
        return "Keine Live-Produktdaten gefunden."

    formatted_lines = []
    for i, item in enumerate(items[:5], 1):
        title = item.get("title", "Unbekanntes Produkt")
        price = item.get("price", "Preis auf Anfrage")
        merchant = item.get("merchant") or item.get("source", "Online-Shop")
        link = item.get("link", "") or item.get("href", "")

        if link and link != "#":
            merchant_display = f"[{merchant}]({link})"
        else:
            merchant_display = merchant

        formatted_lines.append(
            f"{i}. **{title}**\n"
            f"   - Preis: **{price}**\n"
            f"   - Anbieter: {merchant_display}\n"
        )

    return "\n".join(formatted_lines)


# --- DISPATCHER ---
def dispatcher(query: str, user_id: str):
    user_level = get_user_level(user_id)

    # 1) PRODUKTANFRAGE? → APIFY ZUERST
    if is_product_query(query) and APIFY_TOKEN:
        for src in ["apify_google_shopping", "apify_amazon"]:
            apify_items, apify_cost_usd = run_apify(src, query)

            if apify_items is not None:
                apify_cost_eur = round(apify_cost_usd, 4)
                final_price_for_user = calculate_price_with_markup(apify_cost_eur, user_level)

                if user_level == "free":
                    product_preview = format_products_for_telegram(apify_items[:3] if isinstance(apify_items, list) else [])
                    return {
                        "status": "paid_required",
                        "response_text": (
                            f"🔍 **Apify Live-Suche ({src})**\n\n"
                            f"{product_preview}\n\n"
                            f"Admin-Kosten: {apify_cost_eur} € | Dein Preis: {final_price_for_user} €"
                        )
                    }

                product_text = format_products_for_telegram(apify_items)
                final_ai_response = call_groq_analysis(product_text, mode="shopping")
                return {
                    "status": "success",
                    "response_text": final_ai_response
                }

    # 2) FREE-LAYER FALLBACK
    searxng_res = search_searxng(query)
    ddgs_res = search_ddgs(query)

    if searxng_res or ddgs_res:
        return {
            "status": "success",
            "response_text": f"Ergebnisse für '{query}' aus dem Free-Layer abgerufen."
        }

    return {
        "status": "error",
        "response_text": "Es konnten keine passenden Ergebnisse gefunden werden."
    }


# --- GROQ AI INTEGRATION ---
def call_groq_analysis(products_text, mode: str):
    messages = [
        {
            "role": "system",
            "content": (
                "Du lebst IMMER im aktuellen Datum (2026). "
                "Du bist ein professioneller Marktplatz-Broker für einen Telegram-Bot. "
                "Fasse die Angebote übersichtlich zusammen, nenne das günstigste Angebot "
                "und behalte die Markdown-Links ([Anbieter](URL)) unbedingt bei!"
            )
        },
        {
            "role": "user",
            "content": f"Modus: {mode}\nAnalysiere diese Live-Produktdaten:\n{products_text}"
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
        return f"Hier sind die gefundenen Angebote:\n\n{products_text}"

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

        if is_product_query(user_text):
            dispatch_res = dispatcher(user_text, chat_id)
            bot_reply = dispatch_res.get("response_text", "Keine Daten gefunden.")
        else:
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
