# =====================================================================
# IMPORTS & OPTIONALE BIBLIOTHEKEN
# =====================================================================
import os
import time
import json
import re
import base64
import sqlite3
import requests
import concurrent.futures
from urllib.parse import urlparse
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from groq import Groq


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


# =====================================================================
# CONFIGURATION & KEYS
# =====================================================================
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "8874543115")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_KEY")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

REQUIRED_PREFIX = os.getenv("REQUIRED_PREFIX", "+×÷edi99")
DB_PATH = os.getenv("DB_PATH", "bot_memory.db")
pending_code_updates = {}

groq_client = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)


# =====================================================================
# DATENBANK & HILFSFUNKTIONEN (Chat History & User Facts)
# =====================================================================
def init_core_db():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_facts (
                chat_id TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (chat_id, key)
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Fehler bei Core-DB Init: {e}", flush=True)

init_core_db()

def save_message(chat_id, role, content):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)", (str(chat_id), role, content))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_chat_history(chat_id, limit=10):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (str(chat_id), limit))
        rows = cursor.fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []

def set_user_fact(chat_id, key, value):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_facts (chat_id, key, value) VALUES (?, ?, ?)", (str(chat_id), key, str(value)))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_user_fact(chat_id, key):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_facts WHERE chat_id = ? AND key = ?", (str(chat_id), key))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def fetch_raw_web_data(query):
    results = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "link": r.get("href", "")
                    })
        except Exception as e:
            print(f"[Info] DDG Search fehlgeschlagen: {e}", flush=True)
    
    if not results and SEARXNG_URL:
        try:
            res = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=5)
            if res.status_code == 200:
                data = res.json()
                for r in data.get("results", [])[:5]:
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("content", ""),
                        "link": r.get("url", "")
                    })
        except Exception as e:
            print(f"[Info] SearXNG Search fehlgeschlagen: {e}", flush=True)
    return results


# =====================================================================
# ENGINES KOPPLUNG: BOSS-FILTER + PYTHON SMALLTALK PROTECTION
# =====================================================================
TRUSTED_AUTHORITIES = {
    "apple.com", "microsoft.com", "reuters.com", "bloomberg.com", 
    "heise.de", "golem.de", "t3n.de", "wikipedia.org", "tagesschau.de", "wetter.com", "dwd.de"
}

BANNED_SOURCES = {
    "clickbait-news24.com", "dubious-rumors.net", "seo-spam-farm.org"
}

RUMOR_KEYWORDS = ["gerücht", "soll", "angeblich", "womöglich", "insider behaupten", "wird gemunkelt"]

def check_if_search_needed(query: str) -> bool:
    """ CHEF-FILTER 1: Python blockiert Websuche bei Smalltalk komplett """
    q = query.lower().strip()
    smalltalk_words = ["hallo", "hi", "hey", "moin", "servus", "wie gehts", "wer bist du", "guten tag", "danke"]
    if len(q) < 4 or any(word in q for word in smalltalk_words):
        return False
    return True

def _fetch_volltext(link, headers, old_snippet):
    """ Hilfsfunktion für paralleles Scraping ohne Zeitverlust """
    try:
        res = requests.get(link, headers=headers, timeout=2.5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
                script.decompose()
            volltext = " ".join(soup.get_text().split())
            if len(volltext) > len(old_snippet):
                return volltext[:2500] + "... [Volltext via Beautiful Soup extrahiert]"
    except Exception:
        pass
    return old_snippet

def master_data_cleaner_and_boss(raw_results, user_query):
    if not check_if_search_needed(user_query) or not raw_results:
        return ""

    seen_links = set()
    seen_titles = set()
    processed_items = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    links_to_scrape = []

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
            links_to_scrape.append((link, len(processed_items)))
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

    if links_to_scrape:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_index = {
                executor.submit(_fetch_volltext, link, headers, processed_items[idx]["snippet"]): idx 
                for link, idx in links_to_scrape
            }
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                processed_items[idx]["snippet"] = future.result()

    processed_items.sort(key=lambda x: x["priority"], reverse=True)
    if not processed_items:
        return ""

    boss_packet = (
        f"GEPRÜFTES DOSSIER VOM BOSS-FILTER:\n"
        f"Nutze AUSSCHLIESSLICH diese vorvalidierten Daten zur Beantwortung der Anfrage ('{user_query}'). "
        f"Übernehme die Status-Markierungen sowie die Quellen (Domains/Links) exakt in deine Antwort:\n\n"
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
# PRODUKTE DATENBANK & ZUBEHÖR-FILTER
# =====================================================================
ACCESSOIRE_KEYWORDS = ["hülle", "case", "schutzfolie", "panzerglas", "kabel", "adapter", "halterung", "charger", "tasche"]

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
                image_url TEXT,
                lieferzeit TEXT,
                is_accessoire INTEGER DEFAULT 0,
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
            AND is_accessoire = 0
            AND datum > datetime('now', '-24 hours')
        ''', (kategorie,))
        anzahl = cursor.fetchone()[0]
        conn.close()
        return anzahl >= 3
    except Exception as e:
        print(f"❌ Fehler: {e}", flush=True)
        return False

def hole_aus_db(kategorie, limit=10, accessories_only=False):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        acc_flag = 1 if accessories_only else 0
        cursor.execute('''
            SELECT name, preis, url, shop, image_url, lieferzeit
            FROM produkte
            WHERE LOWER(kategorie) = LOWER(?)
            AND is_accessoire = ?
            AND datum > datetime('now', '-24 hours')
            ORDER BY datum DESC
            LIMIT ?
        ''', (kategorie, acc_flag, limit))
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
        for item in data[:15]:
            name = item.get("title") or item.get("name") or "Produkt"
            lower_name = name.lower()
            is_acc = 1 if any(kw in lower_name for kw in ACCESSOIRE_KEYWORDS) else 0

            raw_preis = item.get("priceString") or item.get("price") or "Auf Anfrage"
            if isinstance(raw_preis, dict):
                preis = raw_preis.get("display") or raw_preis.get("value") or raw_preis.get("raw") or "Auf Anfrage"
                preis = str(preis)
            else:
                preis = str(raw_preis)

            url = item.get("url") or item.get("link") or "#"
            image_url = item.get("image") or item.get("thumbnail") or item.get("imageUrl") or ""
            lieferzeit = item.get("delivery") or item.get("shippingText") or "Sofort lieferbar"

            cursor.execute('''
                INSERT INTO produkte (kategorie, name, preis, url, shop, image_url, lieferzeit, is_accessoire)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (kategorie, name, preis, url, shop, image_url, lieferzeit, is_acc))
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
                future_amazon = sub_executor.submit(run_apify_actor, kat, "junglee~amazon-crawler", 10)
                future_ebay = sub_executor.submit(run_apify_actor, kat, "automation-lab~ebay-scraper", 10)
                
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


# =====================================================================
# MULTIMODAL & VISION PIPELINE (QWEN FALLBACK-KETTE)
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

def analyze_image_and_create_dossier(image_path: str) -> tuple[bool, str, str]:
    """ Analysiert Bilder und gibt (is_prod_detected, detected_name, dossier) zurück """
    try:
        if not os.path.exists(image_path):
            return False, "", "BILD-DOSSIER: Bilddatei nicht gefunden."

        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        if not groq_client:
            return False, "", "BILD-DOSSIER: API-Key fehlt."

        vision_models = ["qwen/qwen3.6-27b", "qwen/qwen3.8-27b"]
        completion = None
        last_error = None

        vision_prompt = (
            "Analysiere dieses Bild präzise auf Deutsch. Antworte mit einem strukturierten Dossier:\n"
            "1. Kerninhalt: Was ist auf dem Bild zu sehen (Gegenstände, Personen, Text/Logos)?\n"
            "2. Produkterkennung: Falls ein konkretes Produkt oder ein kaufbarer Artikel zu sehen ist, nenne den exakten Produktnamen ganz kurz und prägnant (z. B. 'Alufelgen Radkappen'). Wenn kein Produkt zu sehen ist, antworte hier mit 'KEIN_PRODUKT'.\n"
            "3. Echtheits- & KI-Check: Gibt es visuelle Artefakte, Manipulationsspuren oder KI-Merkmale?"
        )

        for model_name in vision_models:
            try:
                completion = groq_client.chat.completions.create(
                    model=model_name,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }],
                    temperature=0.1,
                    max_tokens=600
                )
                break
            except Exception as model_err:
                last_error = model_err
                continue

        if completion is None:
            if last_error:
                raise last_error
            return False, "", "BILD-DOSSIER: Fehler bei der KI-Bildanalyse."

        ai_description = completion.choices[0].message.content
        dossier = f"BILD-DOSSIER VOM VISION-FILTER:\n{ai_description}\n"

        is_prod = False
        detected_name = ""
        if "KEIN_PRODUKT" not in ai_description.upper():
            is_prod = True
            lines = ai_description.split("\n")
            for line in lines:
                if "produkt" in line.lower() or "name" in line.lower():
                    detected_name = line.split(":")[-1].strip(" '\"*")
                    break
            if not detected_name:
                detected_name = "Produkt vom Bild"

        if OPENCV_AVAILABLE:
            try:
                img = cv2.imread(image_path)
                if img is not None:
                    h, w, _ = img.shape
                    dossier += f"• Technische Auflösung: {w}x{h} Pixel.\n"
            except Exception:
                pass

        return is_prod, detected_name, dossier
    except Exception as e:
        print(f"❌ Fehler bei Vision API: {e}", flush=True)
        return False, "", "BILD-DOSSIER: Fehler bei der Verarbeitung."

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
                    ret, _ = cap.read()
                    if not ret:
                        break
                    count += 1
                cap.release()
                dossier += f"• Frame-Extraktion: {count} Kern-Frames analysiert.\n"
            else:
                dossier += "• Videodatei konnte nicht geöffnet werden.\n"
        except Exception as e:
            dossier += f"• Fehler: {e}\n"
    else:
        dossier += "• OpenCV nicht verfügbar.\n"
    return dossier


# =====================================================================
# CODE INTEGRITY & GITHUB UPDATES
# =====================================================================
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
            
        encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
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
# APIFY ACTOR STARTEN & POLLING
# =====================================================================
def run_apify_actor(query: str, actor_id: str = "junglee~amazon-crawler", max_items: int = 10):
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


# =====================================================================
# GROQ KI CHAT-FUNKTION (Mit Python-Schutzschild & JSON-Parsing)
# =====================================================================
def ask_groq(chat_id: str, query: str, web_context: str = "") -> dict:
    if not groq_client:
        return {"antwort_text": "❌ Groq-Fehler: GROQ_API_KEY ist nicht gesetzt.", "buttons": []}
    try:
        history = get_chat_history(chat_id, limit=10)
        system_prompt = (
            "Du bist 'Code X', ein transparenter, neutraler und hilfsbereiter KI-Assistent in einem Telegram-Bot. "
            "Wenn dir ein Dossier mit Web-Daten oder Medien-Daten übergeben wird, musst du die Status-Markierungen (🔴, 🟡, ⚠️) "
            "und die **Quellen (Domains/Links)** zwingend in deine Antwort einbauen. "
            "Erfinde keine Fakten. "
            "Antworte AUSSCHLIESSLICH als reines JSON-Objekt im folgenden Format, ohne Markdown-Code-Blöcke:\n"
            "{\n"
            "  \"antwort_text\": \"Dein formatierter Text inklusive Quellen und Status\",\n"
            "  \"buttons\": []\n"
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
        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if match:
            clean_json = match.group(0)
        else:
            if "```" in clean_json:
                parts = clean_json.split("```")
                for p in parts:
                    p_s = p.strip()
                    if p_s.startswith("json"):
                        p_s = p_s[4:].strip()
                    if p_s.startswith("{") and p_s.endswith("}"):
                        clean_json = p_s
                        break

        try:
            response_data = json.loads(clean_json)
            return {
                "antwort_text": response_data.get("antwort_text", raw_content),
                "buttons": response_data.get("buttons", [])
            }
        except json.JSONDecodeError:
            return {
                "antwort_text": raw_content,
                "buttons": []
            }

    except Exception as e:
        print(f"❌ Groq Parsing Error: {e}", flush=True)
        return {"antwort_text": "⚠️ Verarbeitungsfehler bei der KI-Antwort.", "buttons": []}


# =====================================================================
# TELEGRAM SENDEN
# =====================================================================
def send_telegram_photo_or_message(chat_id, text, image_url=None, message_id=None, buttons=None):
    if not TELEGRAM_BOT_TOKEN:
        return False

    reply_markup = None
    if buttons and isinstance(buttons, list) and len(buttons) > 0:
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
    else:
        reply_markup = {"inline_keyboard": []}

    if image_url and image_url.startswith("http") and not message_id:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {"chat_id": chat_id, "photo": image_url, "caption": text, "parse_mode": "Markdown"}
        if reply_markup and reply_markup["inline_keyboard"]:
            payload["reply_markup"] = reply_markup
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                return True
        except Exception:
            pass

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
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
            payload["text"] = text.replace("**", "").replace("*", "").replace("[", "").replace("]", "")
            payload.pop("parse_mode", None)
            response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Telegram-Sende-Fehler: {e}", flush=True)
        return False


# =====================================================================
# ASYNCHRONER PROZESSOR (KONTROLLZENTRUM)
# =====================================================================
import tempfile

def process_message_async(chat_id, query, message_id, is_shopping, media_type=None, file_id=None, is_callback=False):
    try:
        user_input_text = query.strip() if query else ""
        save_message(chat_id, "user", user_input_text if user_input_text else f"[{media_type}]")
        
        buttons = []
        nachricht = ""
        image_to_send = None
        medien_dossier = ""

        # =====================================================================
        # MASTER-WEICHE 1: MEDIEN- & PRODUKT-ERKENNUNG
        # =====================================================================
        if media_type and file_id:
            local_file = download_telegram_file(file_id)
            if local_file:
                if media_type == "image":
                    is_prod_detected, detected_name, medien_dossier = analyze_image_and_create_dossier(local_file)
                    if is_prod_detected and detected_name:
                        is_shopping = True
                        user_input_text = detected_name
                elif media_type == "video":
                    medien_dossier = process_video_and_create_dossier(local_file)
                    if "produkt" in medien_dossier.lower():
                        is_shopping = True
                else:
                    medien_dossier = f"📦 [SYSTEM HINWEIS]: Datei vom Typ '{media_type}' empfangen."

        # =====================================================================
        # MASTER-WEICHE 2: TEXT-KLASSIFIZIERUNG
        # =====================================================================
        if user_input_text and not is_shopping and not is_callback:
            lower_text = user_input_text.lower()
            shopping_keywords = ["kaufen", "preis", "shop", "angebot", "bestellen", "radkappen", "zoll", "felgen"]
            if any(kw in lower_text for kw in shopping_keywords):
                is_shopping = True

        # =====================================================================
        # ABARBEITUNG BOT-STATES & CALLBACKS
        # =====================================================================
        current_state = get_user_fact(chat_id, "bot_state")
        
        if current_state == "waiting_for_location" and not is_callback:
            loc = user_input_text.capitalize()
            set_user_fact(chat_id, "bot_state", None)  
            set_user_fact(chat_id, "location", loc)    
            send_telegram_photo_or_message(chat_id, f"✅ Dein Standort wurde auf **{loc}** gespeichert!", message_id=message_id)
            
            gemerkte_suche = get_user_fact(chat_id, "last_user_query") or "radkappen"
            such_string_mit_ort = f"{gemerkte_suche} {loc}"
            
            produkte = []
            if in_db_vorhanden(gemerkte_suche):
                produkte = hole_aus_db(gemerkte_suche, limit=3, accessories_only=False)
            else:
                try:
                    raw_data = run_apify_actor(such_string_mit_ort, "junglee~amazon-crawler", 5)
                    if raw_data:
                        speichere_produkte(gemerkte_suche, raw_data, "Amazon")
                    produkte = hole_aus_db(gemerkte_suche, limit=3, accessories_only=False)
                except Exception:
                    pass

            if produkte:
                top_prod = produkte[0]
                name, preis, url, shop, image_url, lieferzeit = top_prod
                image_to_send = image_url
                nachricht = (
                    f"🎯 **Top-Ergebnis für '{gemerkte_suche}' in {loc}:**\n\n"
                    f"📦 **{name}**\n"
                    f"💰 **Preis:** {preis} | 📦 **Lieferung:** {lieferzeit}\n"
                    f"🛒 **Anbieter:** {shop}\n"
                )
                buttons = [
                    {"text": "🛒 Zum Shop", "callback": f"open_link:{url}"},
                    {"text": "🔄 Alternativen", "callback": "show_alternatives"},
                    {"text": "🔌 Zubehör", "callback": f"accessories_{gemerkte_suche}"}
                ]
            else:
                clean_context = ""
                if check_if_search_needed(such_string_mit_ort):
                    raw_web_data = fetch_raw_web_data(such_string_mit_ort)
                    clean_context = master_data_cleaner_and_boss(raw_web_data, such_string_mit_ort)
                groq_result = ask_groq(chat_id, f"Finde Angebote zu {such_string_mit_ort}", web_context=clean_context)
                nachricht = f"🌐 **Ergebnisse für '{gemerkte_suche}' in {loc}:**\n\n" + groq_result.get("antwort_text", "")
                buttons = [{"text": "🏠 Hauptmenü", "callback": "restart"}]

            save_message(chat_id, "assistant", nachricht)
            send_telegram_photo_or_message(chat_id, nachricht, image_url=image_to_send, buttons=buttons)
            return

        if is_callback:
            if query.startswith("limit_"):
                parts = query.split("_")
                limit_num = int(parts[1]) if len(parts) > 1 else 3
                product_name = get_user_fact(chat_id, "last_user_query") or "radkappen"
                
                set_user_fact(chat_id, "search_limit", str(limit_num))
                produkte = hole_aus_db(product_name, limit=limit_num, accessories_only=False)

                if not produkte:
                    nachricht = f"⚠️ Keine Angebote für '{product_name}' gefunden."
                    buttons = [{"text": "💎 Live-Suche", "callback": "live_search_pro"}, {"text": "🏠 Hauptmenü", "callback": "restart"}]
                else:
                    top_prod = produkte[0]
                    name, preis, url, shop, image_url, lieferzeit = top_prod
                    image_to_send = image_url
                    location = get_user_fact(chat_id, "location") or "Gelsenkirchen"
                    
                    nachricht = (
                        f"📱 **{name}**\n\n"
                        f"💰 **Preis:** {preis} | 📦 **Lieferung:** {lieferzeit} nach {location}\n"
                        f"🛒 **Anbieter:** {shop}\n"
                    )
                    buttons = [
                        {"text": "🛒 Zum Shop", "callback": f"open_link:{url}"},
                        {"text": "🔄 Alternativen", "callback": "show_alternatives"},
                        {"text": "🔌 Zubehör", "callback": f"accessories_{product_name}"}
                    ]

                save_message(chat_id, "assistant", nachricht)
                send_telegram_photo_or_message(chat_id, nachricht, image_url=image_to_send, message_id=message_id, buttons=buttons)
                return

            elif query == "show_alternatives":
                product_name = get_user_fact(chat_id, "last_user_query") or "radkappen"
                produkte = hole_aus_db(product_name, limit=5, accessories_only=False)
                
                if len(produkte) < 2:
                    nachricht = "ℹ️ Keine weiteren Alternativen im Cache."
                    buttons = [{"text": "💎 Live-Suche", "callback": "live_search_pro"}]
                else:
                    alt_lines = [f"🔄 **Alternativen für '{product_name}':**\n"]
                    for p in produkte[1:]:
                        name, preis, url, shop, _, _ = p
                        alt_lines.append(f"• [{shop}] {name[:40]}...\n  💰 *{preis}* | 🔗 [Zum Angebot]({url})")
                    nachricht = "\n".join(alt_lines)
                    buttons = [{"text": "🏠 Hauptmenü", "callback": "restart"}]

                save_message(chat_id, "assistant", nachricht)
                send_telegram_photo_or_message(chat_id, nachricht, image_url=image_to_send, message_id=message_id, buttons=buttons)
                return

            elif query.startswith("accessories_"):
                product_name = query.replace("accessories_", "").strip()
                z_produkte = hole_aus_db(product_name, limit=5, accessories_only=True)

                if not z_produkte:
                    nachricht = f"⚠️ Kein passendes Zubehör für '{product_name}' gefunden."
                    buttons = [{"text": "🏠 Hauptmenü", "callback": "restart"}]
                else:
                    top_z = z_produkte[0]
                    z_name, z_preis, z_url, z_shop, z_image, _ = top_z
                    image_to_send = z_image

                    nachricht = (
                        f"🔌 **Zubehör für {product_name}:**\n\n"
                        f"📦 **{z_name}**\n"
                        f"💰 **Preis:** {z_preis} | 🛒 **Anbieter:** {z_shop}\n"
                    )
                    buttons = [
                        {"text": "🛒 Zum Shop", "callback": f"open_link:{z_url}"},
                        {"text": "🏠 Hauptmenü", "callback": "restart"}
                    ]

                save_message(chat_id, "assistant", nachricht)
                send_telegram_photo_or_message(chat_id, nachricht, image_url=image_to_send, message_id=message_id, buttons=buttons)
                return

            elif query.startswith("open_link:"):
                target_url = query.replace("open_link:", "").strip()
                nachricht = f"🔗 **Direkter Link zum Angebot:**\n{target_url}"
                buttons = [{"text": "🏠 Hauptmenü", "callback": "restart"}]
                send_telegram_photo_or_message(chat_id, nachricht, message_id=message_id, buttons=buttons)
                return

            elif query == "live_search_pro":
                last_query = get_user_fact(chat_id, "last_user_query") or "radkappen"
                raw_data = run_apify_actor(last_query, "junglee~amazon-crawler", 5)
                if raw_data:
                    speichere_produkte(last_query, raw_data, "Amazon")
                
                produkte = hole_aus_db(last_query, limit=3, accessories_only=False)
                if produkte:
                    top_prod = produkte[0]
                    name, preis, url, shop, image_url, _ = top_prod
                    nachricht = f"✅ **Live-Ergebnis:**\n📦 {name}\n💰 {preis}\n🛒 {shop}"
                    buttons = [{"text": "🛒 Zum Shop", "callback": f"open_link:{url}"}, {"text": "🏠 Hauptmenü", "callback": "restart"}]
                    send_telegram_photo_or_message(chat_id, nachricht, image_url=image_url, message_id=message_id, buttons=buttons)
                else:
                    send_telegram_photo_or_message(chat_id, "Keine Live-Treffer gefunden.", message_id=message_id, buttons=[{"text": "🏠 Hauptmenü", "callback": "restart"}])
                return

            elif query == "restart":
                set_user_fact(chat_id, "bot_state", None)
                nachricht = "🤖 **Hauptmenü:** Hallo! Was möchtest du suchen?"
                buttons = [{"text": "📍 Standort setzen", "callback": "ask_location"}]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_photo_or_message(chat_id, nachricht, message_id=message_id, buttons=buttons)
                return

            elif query == "ask_location":
                set_user_fact(chat_id, "bot_state", "waiting_for_location")
                nachricht = "📍 Bitte antworte jetzt mit deiner Stadt (z. B. *Gelsenkirchen*):"
                buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
                save_message(chat_id, "assistant", nachricht)
                send_telegram_photo_or_message(chat_id, nachricht, message_id=message_id, buttons=buttons)
                return

        elif is_shopping:
            clean_search = user_input_text
            for prefix in ["finde mir", "suche nach", "suchen nach", "suche", "such", "finde", "mir"]:
                if clean_search.lower().startswith(prefix):
                    clean_search = clean_search[len(prefix):].strip()
            
            set_user_fact(chat_id, "last_user_query", clean_search)
            location = get_user_fact(chat_id, "location")
            
            if not location:
                set_user_fact(chat_id, "bot_state", "waiting_for_location")
                nachricht = f"🛍️ Du suchst nach **'{clean_search}'**.\n\nIn welcher Stadt befindest du dich für lokale Angebote?"
                buttons = [{"text": "❌ Abbrechen", "callback": "restart"}]
            else:
                nachricht = f"🎯 Suchanfrage für **'{clean_search}'** in {location} empfangen.\n\nWie viele Ergebnisse möchtest du sehen?"
                buttons = [{"text": "Top 3", "callback": "limit_3"}, {"text": "10 Ergebnisse", "callback": "limit_10"}]

            save_message(chat_id, "assistant", nachricht)
            send_telegram_photo_or_message(chat_id, nachricht, buttons=buttons)
            return

        else:
            if str(chat_id) == ADMIN_USER_ID and user_input_text == "ja":
                nachricht = execute_final_github_update(chat_id)
                buttons = []
            else:
                clean_context = ""
                if check_if_search_needed(user_input_text):
                    raw_web_data = fetch_raw_web_data(user_input_text)
                    clean_context = master_data_cleaner_and_boss(raw_web_data, user_input_text)
                
                vollstaendiger_kontext = ""
                if medien_dossier:
                    vollstaendiger_kontext += medien_dossier + "\n\n"
                if clean_context:
                    vollstaendiger_kontext += clean_context
                
                ki_frage = user_input_text if user_input_text else "Analysiere das übermittelte Medium präzise anhand der Dossier-Daten."
                groq_result = ask_groq(chat_id, ki_frage, web_context=vollstaendiger_kontext)
                nachricht = groq_result.get("antwort_text", "⚠️ Keine Antwort erhalten.")
                buttons = groq_result.get("buttons", [])

        save_message(chat_id, "assistant", nachricht)
        send_telegram_photo_or_message(chat_id, nachricht, image_url=image_to_send, message_id=message_id, buttons=buttons)

    except Exception as thread_error:
        print(f"❌ KRITISCHER FEHLER im Hauptprozessor: {thread_error}", flush=True)
        send_telegram_photo_or_message(chat_id, f"❌ System-Notbremse gegriffen: `{str(thread_error)}`", message_id=message_id)


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
            msg_id = cq["message"]["message_id"]
            
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cq_id})
            executor.submit(process_message_async, chat_id, callback_data, msg_id, False, None, None, is_callback=True)
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
                        executor.submit(process_message_async, chat_id, "ja", lid, False, None, None, False)
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

                current_state = get_user_fact(chat_id, "bot_state")
                if current_state == "waiting_for_location":
                    res = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": "⏳ Standort wird verarbeitet...", "parse_mode": "Markdown"}
                    ).json()
                    lid = res.get("result", {}).get("message_id")
                    executor.submit(process_message_async, chat_id, clean_query, lid, False, None, None, False)
                    return "OK", 200

                is_shopping = False
                lower_text = clean_query.lower()
                for prefix in ["suche nach ", "suchen nach ", "suche ", "such ", "suchen ", "finde ", "finde"]:
                    if lower_text.startswith(prefix):
                        clean_query = clean_query[len(prefix):].strip()
                        is_shopping = True
                        break
                
                if "zoll" in lower_text or "radkappen" in lower_text:
                    is_shopping = True

                if TELEGRAM_BOT_TOKEN:
                    res = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": "⏳ Verarbeite Anfrage...", "parse_mode": "Markdown"}
                    ).json()
                    lid = res.get("result", {}).get("message_id")
                    if lid:
                        executor.submit(process_message_async, chat_id, clean_query, lid, is_shopping, media_type, file_id, False)
                
    except Exception as e:
        print(f"Webhook error: {e}", flush=True)
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
