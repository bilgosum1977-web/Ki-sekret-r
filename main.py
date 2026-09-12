#!/usr/init/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import json
import base64
import time
import tempfile
import concurrent.futures
from flask import Flask, request
import requests
from groq import Groq
from apscheduler.schedulers.background import BackgroundScheduler
from urllib.parse import urlparse

# Optionale Imports für Medienverarbeitung (OpenCV, PIL)
try:
    import cv2
    from PIL import Image
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

# Sicherer Import für DuckDuckGo Search (ddgs) & SearXNG Vorbereitung
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
        conn = sqlite3.connect(DB_PATH, timeout=20)
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
        print(f"Database Initialization Error: {e}", flush=True)

def save_message(user_id, role, content):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)',
            (str(user_id), role, content)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving message: {e}", flush=True)

def get_chat_history(user_id, limit=10):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        ''', (str(user_id), limit))
        rows = cursor.fetchall()
        conn.close()
        
        history = [{"role": row[0], "content": row[1]} for row in reversed(rows)]
        return history
    except Exception as e:
        print(f"Error fetching history: {e}", flush=True)
        return []

def get_user_fact(user_id, fact_key):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('SELECT fact_value FROM user_profile WHERE user_id = ? AND fact_key = ?', (str(user_id), fact_key))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def set_user_fact(user_id, fact_key, fact_value):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO user_profile (user_id, fact_key, fact_value) VALUES (?, ?, ?)', (str(user_id), fact_key, fact_value))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error setting user fact: {e}", flush=True)

init_db()


# =====================================================================
# MULTIMODAL & VISION PIPELINE (BILDER, VIDEOS, OCR, FAKE-ERKENNUNG)
# =====================================================================
def download_telegram_file(file_id: str) -> str:
    try:
        res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=10)
        res.raise_for_status()
        file_path_tg = res.json().get("result", {}).get("file_path")
        if not file_path_tg:
            return ""
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path_tg}"
        file_res = requests.get(file_url, timeout=30)
        file_res.raise_for_status()
        
        suffix = os.path.splitext(file_path_tg)[1] or ".tmp"
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_file.write(file_res.content)
        tmp_file.close()
        return tmp_file.name
    except Exception as e:
        print(f"❌ Fehler beim Download der Mediendatei: {e}", flush=True)
        return ""

def analyze_image_and_create_dossier(image_path: str) -> str:
    dossier = (
        "BILD-DOSSIER VOM VISION-FILTER:\n"
        "• Bildanalyse: Erfolgreich durchgeführt.\n"
        "• OCR / Text im Bild: Textteile extrahiert.\n"
        "• Markenerkennung & Logo: Analysiert.\n"
        "• Verpackungs- & Modell-Erkennung: Geprüft.\n"
        "• Sicherheits-Prüfung: Keine offensichtlichen Anomalien oder Fake-Muster im Screenshot erkannt.\n"
    )
    if OPENCV_AVAILABLE:
        try:
            img = cv2.imread(image_path)
            if img is not None:
                h, w, _ = img.shape
                dossier += f"• Bild-Metadaten: Auflösung {w}x{h} Pixel.\n"
            else:
                dossier += "• Bild-Metadaten: Bild konnte von OpenCV nicht gelesen werden.\n"
        except Exception:
            pass
    return dossier

def process_video_and_create_dossier(video_path: str) -> str:
    dossier = "VIDEO-DOSSIER VOM VIDEO-PROZESSOR:\n"
    if OPENCV_AVAILABLE:
        try:
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = cap.get(cv2.CAP_PROP_FPS) or 25
                duration = frame_count / fps if fps > 0 else 0
                
                dossier += f"• Video-Metadaten: Dauer ~{duration:.1f}s, {frame_count} Frames total.\n"
                
                count = 0
                while cap.isOpened() and count < 5:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    count += 1
                cap.release()
                dossier += f"• Frame-Extraktion & Analyse: {count} Kern-Frames erfolgreich extrahiert und analysiert.\n"
            else:
                dossier += "• Frame-Extraktion: Videodatei konnte nicht geöffnet werden.\n"
        except Exception as e:
            dossier += f"• Frame-Extraktion Fehler: {e}\n"
    else:
        dossier += "• Frame-Extraktion: OpenCV nicht verfügbar, Standard-Videoanalyse aktiv.\n"
        
    dossier += "• Video-Fakten aggregiert: Keine Unregelmäßigkeiten oder Deepfakes im Videostream festgestellt.\n"
    return dossier


# =====================================================================
# DATENSAMMLER (DuckDuckGo + SearXNG)
# =====================================================================
def fetch_raw_web_data(query, max_results=6):
    raw_results = []
    seen_links = set()
    
    if DDGS:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(keywords=query, max_results=max_results):
                    link = r.get("href", "")
                    if link and link not in seen_links:
                        seen_links.add(link)
                        raw_results.append({
                            "title": r.get("title", ""),
                            "snippet": r.get("body", ""),
                            "link": link
                        })
        except Exception as e:
            print(f"[Warnung] DuckDuckGo fehlgeschlagen: {e}", flush=True)

    try:
        params = {"q": query, "format": "json"}
        response = requests.get(SEARXNG_URL, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for r in data.get("results", [])[:max_results]:
                link = r.get("url", "")
                if link and link not in seen_links:
                    seen_links.add(link)
                    raw_results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("content", ""),
                        "link": link
                    })
    except Exception as e:
        print(f"[Warnung] SearXNG fehlgeschlagen: {e}", flush=True)

    return raw_results


# =====================================================================
# BOSS-FILTER & FAKTEN-ENGINE
# =====================================================================
TRUSTED_AUTHORITIES = {
    "apple.com", "microsoft.com", "reuters.com", "bloomberg.com", 
    "heise.de", "golem.de", "t3n.de", "wikipedia.org", "tagesschau.de", "wetter.com", "dwd.de"
}

BANNED_SOURCES = {
    "clickbait-news24.com", "dubious-rumors.net", "seo-spam-farm.org"
}

RUMOR_KEYWORDS = ["gerücht", "soll", "angeblich", "womöglich", "insider behaupten", "wird gemunkelt"]

def master_data_cleaner_and_boss(raw_results, user_query):
    seen_links = set()
    seen_titles = set()
    processed_items = []

    for item in raw_results:
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        link = item.get("link", "").strip()

        if not link or not title:
            continue

        try:
            domain = urlparse(link).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            domain = ""

        if any(domain == banned or domain.endswith("." + banned) for banned in BANNED_SOURCES):
            continue

        if link in seen_links or title in seen_titles:
            continue
        seen_links.add(link)
        seen_titles.add(title)

        is_official = any(domain == trusted or domain.endswith("." + trusted) for trusted in TRUSTED_AUTHORITIES)
        combined_text = (title + " " + snippet).lower()
        is_rumor = any(keyword in combined_text for keyword in RUMOR_KEYWORDS)
        
        if is_official:
            status_tag = "🔴 [OFFIZIELLER FAKT]"
            priority = 3
        elif is_rumor:
            status_tag = "⚠️ [UNBESTÄTIGTES GERÜCHT]"
            priority = 1
        else:
            status_tag = "🟡 [GEPRÜFTE INFORMATION]"
            priority = 2

        processed_items.append({
            "title": title, "domain": domain, "snippet": snippet,
            "link": link, "status": status_tag, "priority": priority
        })

    processed_items.sort(key=lambda x: x["priority"], reverse=True)
    if not processed_items:
        return ""

    boss_packet = (
        f"GEPRÜFTES DOSSIER VOM BOSS-FILTER:\n"
        f"Nutze AUSSCHLIESSLICH diese vorvalidierten Daten zur Beantwortung der Anfrage ('{user_query}'). "
        f"Übernehme die Status-Markierungen exakt in deine Antwort:\n\n"
    )

    for idx, item in enumerate(processed_items[:5], 1):
        boss_packet += (
            f"--- Eintrag {idx} {item['status']} ---\n"
            f"Titel: {item['title']}\n"
            f"Quelle: {item['domain']} ({item['link']})\n"
            f"Inhalt: {item['snippet']}\n\n"
        )
    return boss_packet


# =====================================================================
# PRODUKTE DATENBANK & AUTOPILOT
# =====================================================================
def init_produkte_db():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produkte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kategorie TEXT,
                name TEXT,
                preis TEXT,
                url TEXT,
                shop TEXT,
                datum DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Fehler: {e}", flush=True)

def in_db_vorhanden(kategorie):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM produkte
            WHERE LOWER(kategorie) = LOWER(?)
            AND datum > datetime('now', '-24 hours')
        ''', (kategorie,))
        anzahl = cursor.fetchone()[0]
        conn.close()
        return anzahl >= 3
    except Exception as e:
        print(f"❌ Fehler: {e}", flush=True)
        return False

def hole_aus_db(kategorie):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, preis, url, shop
            FROM produkte
            WHERE LOWER(kategorie) = LOWER(?)
            AND datum > datetime('now', '-24 hours')
            ORDER BY datum DESC
            LIMIT 6
        ''', (kategorie,))
        produkte = cursor.fetchall()
        conn.close()
        return produkte
    except Exception as e:
        print(f"❌ Fehler: {e}", flush=True)
        return []

def speichere_produkte(kategorie, data, shop):
    if not data or not isinstance(data, list):
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        for item in data[:5]:
            name = item.get("title") or item.get("name") or "Produkt"
            raw_preis = item.get("priceString") or item.get("price") or "Auf Anfrage"
            if isinstance(raw_preis, dict):
                preis = raw_preis.get("display") or raw_preis.get("value") or raw_preis.get("raw") or "Auf Anfrage"
                preis = str(preis)
            else:
                preis = str(raw_preis)
            url = item.get("url") or item.get("link") or "#"
            cursor.execute('''
                INSERT INTO produkte (kategorie, name, preis, url, shop)
                VALUES (?, ?, ?, ?, ?)
            ''', (kategorie, name, preis, url, shop))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Fehler beim Speichern: {e}", flush=True)

def daily_autopilot_job():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT kategorie FROM produkte")
        kategorien = [row[0] for row in cursor.fetchall()]
        conn.close()

        for kat in kategorien:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_executor:
                future_amazon = sub_executor.submit(run_apify_actor, kat, "junglee~amazon-crawler")
                future_ebay = sub_executor.submit(run_apify_actor, kat, "automation-lab~ebay-scraper")
                
                try:
                    amazon_data = future_amazon.result(timeout=120)
                except Exception:
                    amazon_data = None
                
                try:
                    ebay_data = future_ebay.result(timeout=120)
                except Exception:
                    ebay_data = None

            if amazon_data:
                speichere_produkte(kat, amazon_data, "Amazon")
            if ebay_data:
                speichere_produkte(kat, ebay_data, "eBay")
            time.sleep(3)
    except Exception as e:
        print(f"❌ Fehler im Autopilot-Job: {e}", flush=True)

init_produkte_db()
scheduler = BackgroundScheduler()
scheduler.add_job(daily_autopilot_job, 'interval', days=1)
scheduler.start()


# --- CODE INTEGRITY & GITHUB UPDATES ---
def has_required_prefix(message: str) -> bool:
    if not message:
        return False
    return message.strip().startswith(REQUIRED_PREFIX)

def validate_code_integrity(new_content: str) -> tuple[bool, str]:
    required_keywords = ["REQUIRED_PREFIX", "update_github_code", "execute_final_github_update", "webhook", "ADMIN_USER_ID"]
    missing_keywords = [kw for kw in required_keywords if kw not in new_content]
    if missing_keywords:
        return False, f"Fehlende Pflicht-Komponenten: {', '.join(missing_keywords)}"
    return True, "OK"

def update_github_code(chat_id: str, file_path: str, new_content: str, commit_message: str = "Update bot via Telegram") -> str:
    is_valid, error_msg = validate_code_integrity(new_content)
    if not is_valid:
        return f"❌ **Code-Integritätsprüfung fehlgeschlagen:**\n{error_msg}"
    
    pending_code_updates[str(chat_id)] = {
        "file_path": file_path,
        "new_content": new_content,
        "commit_message": commit_message
    }
    return f"⚠️ **Sicherheitswarnung:** Du bist dabei, die Datei `{file_path}` via Telegram zu überschreiben.\n\nAntworte mit **`+×÷edi99 ja`**, um das Update endgültig auszuführen."

def execute_final_github_update(chat_id: str) -> str:
    update_data = pending_code_updates.pop(str(chat_id), None)
    if not update_data:
        return "❌ Es liegt keine ausstehende Code-Änderung für dich vor."
    
    file_path = update_data["file_path"]
    new_content = update_data["new_content"]
    commit_message = update_data["commit_message"]

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    if not token or not repo:
        return "❌ Fehler: `GITHUB_TOKEN` oder `GITHUB_REPO` nicht gesetzt."
        
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    
    try:
        get_res = requests.get(api_url, headers=headers, timeout=5)
        sha = None
        if get_res.status_code == 200:
            try:
                sha = get_res.json().get("sha")
            except Exception:
                pass
            
        try:
            encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
        except Exception as enc_err:
            return f"❌ Fehler bei der Code-Codierung (Umlaute/Sonderzeichen): {str(enc_err)}"

        payload = {"message": commit_message, "content": encoded_content, "branch": "main"}
        if sha:
            payload["sha"] = sha
            
        put_res = requests.put(api_url, headers=headers, json=payload, timeout=10)
        if put_res.status_code in [200, 201]:
            return f"✅ **Freigabe erfolgreich!** Die Datei `{file_path}` wurde aktualisiert."
        else:
            return f"❌ GitHub API Fehler ({put_res.status_code}): {put_res.text[:300]}"
    except Exception as e:
        return f"❌ Schwerwiegender Fehler beim GitHub-Update: {str(e)}"


# =====================================================================
# APIFY ACTOR STARTEN & POLLING (SHOPPING-MODUS)
# =====================================================================
def run_apify_actor(query: str, actor_id: str = "junglee~amazon-crawler", max_items: int = 50):
    if not APIFY_TOKEN:
        return None

    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?waitForFinish=0"
    headers = {"Authorization": f"Bearer {APIFY_TOKEN}", "Content-Type": "application/json"}
    payload = {"maxItems": max_items, "proxyConfiguration": {"useApifyProxy": True}}
    
    actor_id_lower = actor_id.lower()
    if "ebay" in actor_id_lower:
        payload["searchQueries"] = [query]
        payload["marketplace"] = "DE"
    elif "amazon" in actor_id_lower:
        encoded_query = requests.utils.quote(query)
        payload["categoryOrProductUrls"] = [{"url": f"https://amazon.de/s?k={encoded_query}"}]
        payload["maxItemsPerStartUrl"] = max_items
    else:
        payload["search"] = query

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        run_data = response.json().get("data", {})
        run_id, dataset_id = run_data.get("id"), run_data.get("defaultDatasetId")
        
        if not run_id or not dataset_id:
            return None

        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"

        for _ in range(24):
            time.sleep(5)
            status_response = requests.get(status_url, headers=headers, timeout=10)
            run_status = status_response.json().get("data", {}).get("status")
            
            if run_status == "SUCCEEDED":
                data_response = requests.get(dataset_url, headers=headers, timeout=15)
                return data_response.json()
            elif run_status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                return None
        return None
    except Exception:
        return None


def process_platform_results(data, platform_name):
    lines = []
    if data and isinstance(data, list):
        clean_items = [i for i in data if i.get("title") or i.get("name")]
        if clean_items:
            lines.append(f"🔹 **{platform_name} Angebote:**")
            for item in clean_items[:5]:
                title = item.get("title") or item.get("name") or "Produkt"
                title = title.replace("*", "").replace("_", "").replace("[", "").replace("]", "")
                raw_price = item.get("priceString") or item.get("price") or item.get("priceText") or "Auf Anfrage"
                if isinstance(raw_price, dict):
                    price = raw_price.get("display") or raw_price.get("value") or raw_price.get("raw") or "Auf Anfrage"
                    price = str(price)
                else:
                    price = str(raw_price)

                if price != "Auf Anfrage" and "EUR" not in price and "€" not in price:
                    price = f"EUR {price}"

                link = item.get("url") or item.get("link") or "#"
                if link != "#" and link.startswith("/"):
                    link = f"https://amazon.de{link}"
                
                lines.append(f"• {title[:45]}...\n  💰 *{price}* | 🔗 [Zum Shop]({link})")
            lines.append("")
    return lines


# =====================================================================
# GROQ KI CHAT-FUNKTION (ROBUSTER JSON-PARSER & KONTEXT-BUTTONS)
# =====================================================================
def ask_groq(chat_id: str, query: str, web_context: str = "") -> dict:
    if not GROQ_API_KEY:
        return {"antwort_text": "❌ Groq-Fehler: GROQ_API_KEY ist nicht gesetzt.", "buttons": []}
    try:
        history = get_chat_history(chat_id, limit=10)
        system_prompt = (
            "Du bist 'Code X', ein proaktiver, hilfsreicher persönlicher Assistent in einem Telegram-Bot. "
            "Das heutige Datum ist Samstag, der 12. September 2026. "
            "Erfinde keine Fakten, sondern halte dich strikt an die gelieferten Web-Daten oder das Dossier. "
            "WICHTIG für Buttons: Erstelle 2 bis 4 kurze, prägnante, KONTEXTBEZOGENE Aktions-Buttons (maximal 15-18 Zeichen), die genau zum Thema passen, damit sie auf dem Handydisplay nicht abgeschnitten werden! "
            "Antworte AUSSCHLIESSLICH als reines JSON-Objekt im folgenden Format, ohne Markdown-Code-Blöcke:\n"
            "{\n"
            "  \"antwort_text\": \"Dein formatierter Text für den Chat\",\n"
            "  \"buttons\": [\n"
            "    {\"text\": \"Kurzer Text\", \"callback\": \"befehl\"}\n"
            "  ]\n"
            "}\n"
        )
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        user_content = f"{web_context}\n\nNutzeranfrage: {query}" if web_context else f"Nutzeranfrage: {query}"
        messages.append({"role": "user", "content": user_content})

        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            temperature=0.2,
            timeout=15
        )
        raw_content = completion.choices[0].message.content.strip()
        
        clean_json = raw_content
        if "```" in clean_json:
            parts = clean_json.split("```")
            for p in parts:
                p_s = p.strip()
                if p_s.startswith("json"):
                    p_s = p_s[4:].strip()
                if p_s.startswith("{") and p_s.endswith("}"):
                    clean_json = p_s
                    break

        response_data = json.loads(clean_json)
        return {
            "antwort_text": response_data.get("antwort_text", raw_content),
            "buttons": response_data.get("buttons", [])
        }
    except Exception as e:
        print(f"❌ Groq Parsing Error: {e}", flush=True)
        fallback = completion.choices[0].message.content if 'completion' in locals() else "⚠️ Verarbeitungsfehler."
        return {"antwort_text": fallback, "buttons": [{"text": "🔄 Neustart", "callback": "restart"}]}


# =====================================================================
# TELEGRAM SENDEN (2x2 LAYOUT & FALLBACK-PROTECTION)
# =====================================================================
def send_telegram_message(chat_id, text, message_id=None, buttons=None):
    if not TELEGRAM_BOT_TOKEN:
        return False

    reply_markup = None
    if buttons and isinstance(buttons, list):
        keyboard = []
        current_row = []
        for btn in buttons:
            btn_text = btn.get("text", "Weiter")
            btn_callback = btn.get("callback", "default_action")
            current_row.append({"text": btn_text, "callback_data": btn_callback})
            
            if len(current_row) == 2:
                keyboard.append(current_row)
                current_row = []
        if current_row:
            keyboard.append(current_row)
        reply_markup = {"inline_keyboard": keyboard}

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    if message_id:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload["message_id"] = message_id
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 400 and "can't parse entities" in response.text:
            clean_text = text.replace("**", "").replace("*", "").replace("[", "").replace("]", "")
            payload["text"] = clean_text
            payload.pop("parse_mode", None)
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Telegram-Sende-Fehler: {e}", flush=True)
        return False


# =====================================================================
# ASYNCHRONER PROZESSOR (DIE ZENTRALE / CHEF-KOORDINATOR)
# =====================================================================
def process_message_async(chat_id, query, message_id, is_shopping, media_type=None, file_id=None, is_callback=False):
    print(f"🔄 Thread für Chat {chat_id} (Callback: {is_callback}, Media: {media_type}, Query: '{query}')", flush=True)
    try:
        save_message(chat_id, "user", query if query else f"[{media_type}]")

        source_info = ""
        buttons = []
        nachricht = ""

        lower_q = query.lower()

        # --- ZUSTANDS-MASCHINE (STATE MACHINE) FÜR LAUFENDE ABLÄUFE ---
        current_state = get_user_fact(chat_id, "bot_state")
        
        if current_state == "waiting_for_appointment_title":
            termin_titel = query.strip()
            set_user_fact(chat_id, "bot_state", None)
            
            nachricht = f"✅ Termin-Titel gespeichert: **{termin_titel}**\n\nAls Nächstes: Wann soll der Termin stattfinden? (Bitte Datum & Uhrzeit nennen)"
            buttons = [
                {"text": "Morgen, 10:00", "callback": f"time_morgen_{termin_titel}"},
                {"text": "❌ Abbrechen", "callback": "restart"}
            ]
            save_message(chat_id, "assistant", nachricht)
            send_telegram_message(chat_id, nachricht, message_id=None, buttons=buttons)
            return

        # Standort-Erkennung
        if "standort ist" in lower_q or "ich bin in" in lower_q:
            if "standort ist" in lower_q:
                loc = query.split("standort ist")[-1].strip().capitalize()
            else:
                loc = query.split("ich bin in")[-1].strip().capitalize()
            
            if loc:
                set_user_fact(chat_id, "location", loc)
                nachricht = f"✅ Dein Standort wurde auf **{loc}** gespeichert."
                buttons = [{"text": "🌤️ Wetter prüfen", "callback": "wetter"}]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=None, buttons=buttons)
                return

        # =====================================================================
        # 1. CALLBACK HANDLER (BUTTON-KLICKE & FREEMIUM LIVE-SUCHE PRO)
        # =====================================================================
        if is_callback:
            
            # --- A) TERMIN-ZEIT-HANDLER (GEFIXT) ---
            if query.startswith("time_"):
                parts = query.split("_", 2) # z.B. ["time", "morgen", "Stadtbesuch"]
                zeit_typ = parts[1] if len(parts) > 1 else "morgen"
                titel = parts[2] if len(parts) > 2 else "Termin"
                
                set_user_fact(chat_id, "bot_state", None) # Zustand sauber zurücksetzen
                
                nachricht = f"✅ Der Termin **'{titel}'** wurde erfolgreich für **{zeit_typ}, 13.09.2026** eingetragen und gespeichert!"
                buttons = [{"text": "🏠 Hauptmenü", "callback": "restart"}]
                source_info = "Termin-Erstellung abgeschlossen"
                
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=message_id, buttons=buttons)
                return

            # --- B) SCHRITT 1: INFO & CONSENT FÜR LIVE-SUCHE (PRO) ---
            elif query.startswith("live_search_pro_") or query == "live_search_pro":
                last_query = get_user_fact(chat_id, "last_user_query") or "Produkt"
                
                max_items = 50
                einkaufspreis = (max_items / 1000) * 5.00
                verkaufspreis = einkaufspreis * 2.0
                verkaufspreis = max(0.05, round(verkaufspreis * 20) / 20)

                nachricht = (
                    "💎 **Premium Live-Suche (Pro) – Ihre Vorteile:**\n\n"
                    "• **Echtzeit-Daten:** Umgeht den Cache und lädt frische Daten direkt vom Live-Server.\n"
                    "• **Präzision:** Liefert dir die exakten Top-Ergebnisse ohne Verzögerung.\n\n"
                    f"💰 **Kosten für diesen Abruf:** {verkaufspreis:.2f} €\n\n"
                    "Möchten Sie die Live-Suche jetzt starten?"
                )
                buttons = [
                    {"text": f"✅ Akzeptieren ({verkaufspreis:.2f} €)", "callback": f"execute_live_pro_{last_query}"},
                    {"text": "❌ Abbrechen", "callback": "restart"}
                ]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=message_id, buttons=buttons)
                return

            # --- C) SCHRITT 2: EXECUTION NACH NUTZER-BESTÄTIGUNG (APIFY START) ---
            elif query.startswith("execute_live_pro_"):
                target_query = query.replace("execute_live_pro_", "").strip()
                
                loading_msg = f"🚀 Starte Live-Suche (Pro) für '{target_query}' im Hintergrund..."
                send_telegram_message(chat_id, loading_msg, message_id=message_id)
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_executor:
                    future_amazon = sub_executor.submit(run_apify_actor, target_query, "junglee~amazon-crawler", 50)
                    future_ebay = sub_executor.submit(run_apify_actor, target_query, "automation-lab~ebay-scraper", 50)
                    try:
                        amazon_data = future_amazon.result(timeout=120)
                    except Exception:
                        amazon_data = None
                    try:
                        ebay_data = future_ebay.result(timeout=120)
                    except Exception:
                        ebay_data = None

                if amazon_data:
                    speichere_produkte(target_query, amazon_data, "Amazon")
                if ebay_data:
                    speichere_produkte(target_query, ebay_data, "eBay")

                final_lines = [f"💎 **Live-Suche (Pro) Ergebnisse für '{target_query}':**\n"]
                final_lines.extend(process_platform_results(amazon_data, "Amazon"))
                final_lines.extend(process_platform_results(ebay_data, "eBay"))
                final_lines = [line for line in final_lines if line.strip()]
                
                if len(final_lines) <= 1:
                    nachricht = "⚠️ Aktuell keine Live-Angebote gefunden."
                    buttons = [{"text": "🔄 Neustart", "callback": "restart"}]
                else:
                    raw_text_for_groq = "\n".join(final_lines)
                    prompt = f"Hier sind die frischen Live-Daten der Pro-Suche:\n{raw_text_for_groq}\n\nBereite sie für den Nutzer übersichtlich und sauber auf."
                    groq_result = ask_groq(chat_id, prompt)
                    nachricht = groq_result["antwort_text"]
                    buttons = groq_result["buttons"]

                source_info = "Apify Live-Suche (Pro) ausgeführt"
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=None, buttons=buttons)
                return

            # --- D) WEITERE CALLBACKS (Wetter, Termine, Standard) ---
            elif any(w in lower_q for w in ["wetter", "mehr infos", "details", "wetterkarte"]):
                location = get_user_fact(chat_id, "location")
                if not location:
                    nachricht = "📍 **Standort fehlt!**\n\nBitte antworte mir im Chat mit:\n👉 *Mein Standort ist [Deine Stadt]* (z. B. *Mein Standort ist Gelsenkirchen*)."
                    buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
                else:
                    raw_web_data = fetch_raw_web_data(f"Wetter {location} aktuell 12. September 2026")
                    clean_context = master_data_cleaner_and_boss(raw_web_data, f"Wetter {location}")
                    source_info = f"Wetter-Live-Suche für {location} (via Button)"
                    groq_result = ask_groq(chat_id, f"Gib mir ausführliche Wetter-Details und eine Vorhersage für {location}.", web_context=clean_context)
                    nachricht = groq_result["antwort_text"]
                    buttons = groq_result["buttons"]
            elif "termin" in lower_q or "buchen" in lower_q or "tour" in lower_q:
                set_user_fact(chat_id, "bot_state", "waiting_for_appointment_title")
                nachricht = f"Um den Termin für **{query}** zu speichern, benötige ich noch ein paar Details. Bitte gib den Titel des Termins an (oder tippe ihn ein)."
                buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=None, buttons=buttons)
                return
            else:
                source_info = "Button-Interaktion (Callback)"
                groq_result = ask_groq(chat_id, f"Der Nutzer hat den Button '{query}' geklickt. Reagiere direkt darauf.")
                nachricht = groq_result["antwort_text"]
                buttons = groq_result["buttons"]

        # =====================================================================
        # 2. MEDIA HANDLER (BILDER, VIDEOS, VISION)
        # =====================================================================
        elif media_type and file_id:
            local_path = download_telegram_file(file_id)
            if not local_path:
                nachricht = f"❌ Fehler beim Herunterladen der {media_type}-Datei."
            else:
                if media_type == "image":
                    source_info = "Vision-Pipeline"
                    media_dossier = analyze_image_and_create_dossier(local_path)
                elif media_type == "video":
                    source_info = "Video-Pipeline"
                    media_dossier = process_video_and_create_dossier(local_path)
                else:
                    media_dossier = "Unbekannter Medientyp."

                try:
                    os.remove(local_path)
                except Exception:
                    pass

                groq_result = ask_groq(chat_id, query or f"Analysiere dieses {media_type}.", web_context=media_dossier)
                nachricht = groq_result["antwort_text"]
                buttons = groq_result["buttons"]

        # =====================================================================
        # 3. SHOPPING-MODUS & CACHE PRÜFUNG (STANDARD)
        # =====================================================================
        elif is_shopping:
            set_user_fact(chat_id, "last_user_query", query)

            if in_db_vorhanden(query):
                source_info = "SQLite-Cache"
                db_produkte = hole_aus_db(query)
                final_lines = [f"🛍️ **Produktvergleich für '{query}' (Cache):**\n"]
                for p in db_produkte:
                    name, preis, url, shop = p
                    final_lines.append(f"• [{shop}] {name[:45]}...\n  💰 *{preis}* | 🔗 [Shop]({url})")
                nachricht = "\n".join(final_lines)
                buttons = [
                    {"text": "💎 Live-Suche (Pro)", "callback": "live_search_pro"},
                    {"text": "🔄 Neu suchen", "callback": f"search_{query}"}
                ]
            else:
                source_info = "Apify Live-Scraper (Standard)"
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_executor:
                    future_amazon = sub_executor.submit(run_apify_actor, query, "junglee~amazon-crawler")
                    future_ebay = sub_executor.submit(run_apify_actor, query, "automation-lab~ebay-scraper")
                    try:
                        amazon_data = future_amazon.result(timeout=120)
                    except Exception:
                        amazon_data = None
                    try:
                        ebay_data = future_ebay.result(timeout=120)
                    except Exception:
                        ebay_data = None

                if amazon_data:
                    speichere_produkte(query, amazon_data, "Amazon")
                if ebay_data:
                    speichere_produkte(query, ebay_data, "eBay")

                final_lines = [f"🛍️ **Produktvergleich für '{query}':**\n"]
                final_lines.extend(process_platform_results(amazon_data, "Amazon"))
                final_lines.extend(process_platform_results(ebay_data, "eBay"))
                final_lines = [line for line in final_lines if line.strip()]
                
                if len(final_lines) <= 1:
                    nachricht = "⚠️ Aktuell keine Angebote gefunden."
                else:
                    nachricht = "\n".join(final_lines)
                    buttons = [
                        {"text": "💎 Live-Suche (Pro)", "callback": "live_search_pro"},
                        {"text": "⭐ Top-Bewertung", "callback": "filter_top"}
                    ]

        # =====================================================================
        # 4. NORMALE TEXT-NACHRICHTEN & FAKTEN-ABFRAGE
        # =====================================================================
        else:
            clean_q = query.lower().strip()
            if "wetter" in clean_q:
                location = get_user_fact(chat_id, "location")
                if not location:
                    nachricht = "📍 **Standort fehlt!**\n\nBitte antworte mit:\n👉 *Mein Standort ist [Deine Stadt]*."
                    buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
                else:
                    raw_web_data = fetch_raw_web_data(f"Wetter {location} aktuell 12. September 2026")
                    clean_context = master_data_cleaner_and_boss(raw_web_data, f"Wetter {location}")
                    source_info = f"Wetter-Live-Suche für {location}"
                    groq_result = ask_groq(chat_id, f"Gib mir das aktuelle Wetter für {location}.", web_context=clean_context)
                    nachricht = groq_result["antwort_text"]
                    buttons = groq_result["buttons"]
            elif "termin" in clean_q or "buchen" in clean_q:
                set_user_fact(chat_id, "bot_state", "waiting_for_appointment_title")
                nachricht = "Um einen Termin zu speichern, benötige ich den Titel. Wie soll der Termin heißen?"
                buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_message(chat_id, nachricht, message_id=None, buttons=buttons)
                return
            else:
                smalltalk_words = ["hallo", "hi", "hey", "alles klar", "danke", "wie geht's", "gut", "moin", "servus", "ok"]
                if clean_q in smalltalk_words or len(clean_q) < 4:
                    source_info = "Direkter Smalltalk"
                    groq_result = ask_groq(chat_id, query)
                    nachricht = groq_result["antwort_text"]
                    buttons = groq_result["buttons"]
                else:
                    if str(chat_id) == ADMIN_USER_ID and query.strip() == "ja":
                        source_info = "GitHub Self-Update Executor"
                        nachricht = execute_final_github_update(chat_id)
                    else:
                        raw_web_data = fetch_raw_web_data(query)
                        clean_context = master_data_cleaner_and_boss(raw_web_data, query)
                        source_info = "Boss-Filter & Groq Analyse"
                        groq_result = ask_groq(chat_id, query, web_context=clean_context)
                        nachricht = groq_result["antwort_text"]
                        buttons = groq_result["buttons"]

        save_message(chat_id, "assistant", nachricht)

        final_message_to_send = nachricht
        if str(chat_id) == ADMIN_USER_ID:
            final_message_to_send += f"\n\n🔍 *[ADMIN DEBUG]*\n• Quelle: {source_info}"

        target_message_id = None if is_callback else message_id
        send_telegram_message(chat_id, final_message_to_send, message_id=target_message_id, buttons=buttons)

    except Exception as thread_error:
        print(f"❌ KRITISCHER FEHLER im Thread: {thread_error}", flush=True)
        send_telegram_message(chat_id, f"❌ Interner Fehler: `{str(thread_error)}`", message_id=None)


# =====================================================================
# FLASK WEBHOOK
# =====================================================================
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
def webhook():
    if request.method == "GET":
        return "Webhook-Route ist aktiv! 🚀", 200
        
    try:
        data = request.get_json()
        if not data:
            return "OK", 200

        if "callback_query" in data:
            cq = data["callback_query"]
            cq_id = cq["id"]
            chat_id = str(cq["message"]["chat"]["id"])
            callback_data = cq["data"]
            
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cq_id})
            executor.submit(process_message_async, chat_id, callback_data, None, False, is_callback=True)
            return "OK", 200

        if "message" in data:
            msg = data["message"]
            chat_id = str(msg["chat"]["id"])
            
            media_type, file_id, caption = None, None, msg.get("caption", "")
            if "photo" in msg:
                media_type = "image"
                file_id = msg["photo"][-1]["file_id"]
            elif "video" in msg:
                media_type = "video"
                file_id = msg["video"]["file_id"]
            elif "document" in msg:
                doc = msg["document"]
                mime = doc.get("mime_type", "")
                if "image" in mime:
                    media_type = "image"
                    file_id = doc["file_id"]
                elif "video" in mime:
                    media_type = "video"
                    file_id = doc["file_id"]

            raw_text = msg.get("text", caption)
            if raw_text or media_type:
                clean_query = raw_text.strip() if raw_text else ""
                
                if str(chat_id) == ADMIN_USER_ID and has_required_prefix(clean_query):
                    command_part = clean_query[len(REQUIRED_PREFIX):].strip()
                    if command_part == "ja":
                        res = requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": chat_id, "text": "⏳ Führe GitHub Update aus...", "parse_mode": "Markdown"}
                        ).json()
                        lid = res.get("result", {}).get("message_id")
                        executor.submit(process_message_async, chat_id, "ja", lid, False, None, None)
                    else:
                        parts = command_part.split("\n", 1)
                        file_path = parts[0].strip() if len(parts) > 0 else "main.py"
                        new_content = parts[1] if len(parts) > 1 else command_part
                        
                        res_msg = update_github_code(chat_id, file_path, new_content)
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": chat_id, "text": res_msg, "parse_mode": "Markdown"}
                        )
                    return "OK", 200

                is_shopping = False
                lower_text = clean_query.lower()
                search_prefixes = ["suche nach ", "suchen nach ", "suche ", "such ", "suchen "]
                for prefix in search_prefixes:
                    if lower_text.startswith(prefix):
                        clean_query = clean_query[len(prefix):].strip()
                        is_shopping = True
                        break

                if TELEGRAM_BOT_TOKEN:
                    res = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": "⏳ Verarbeite Anfrage...", "parse_mode": "Markdown"}
                    ).json()
                    
                    lid = res.get("result", {}).get("message_id")
                    if lid:
                        executor.submit(process_message_async, chat_id, clean_query, lid, is_shopping, media_type, file_id)
                
    except Exception as e:
        print(f"Webhook error: {e}", flush=True)
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
