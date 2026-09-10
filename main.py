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
        f"📜 **Vorschau:**\n```python\n{preview_snippet}\n
