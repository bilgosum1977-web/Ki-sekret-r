import os
import io
import sqlite3
import json
import concurrent.futures
from flask import Flask, request
from groq import Groq
import requests
from PIL import Image
from duckduckgo_search import DDGS

app = Flask(__name__)

# --- KONFIGURATION & API-SCHLÜSSEL ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "8874543115")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    print("[ADMIN LOG] ❌ KRITISCH: Weder TELEGRAM_TOKEN noch TELEGRAM_BOT_TOKEN gefunden!", flush=True)
else:
    print(f"[ADMIN LOG] ✅ Telegram Token erfolgreich geladen.", flush=True)

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
else:
    print("[ADMIN LOG] ❌ KRITISCH: Weder GROQ_API_KEY noch GROK_API_KEY gefunden!", flush=True)

# Das OpenAI-kompatible Open-Modell auf Groq
GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"

user_balances = {}
INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 15

# --- DATENBANK SETUP (SQLite) ---
DB_PATH = os.getenv("DB_PATH", "bot_memory.db")

def init_db():
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

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)', (str(user_id), role, content))
    conn.commit()
    conn.close()

def get_history(user_id, limit=MAX_HISTORY_LENGTH):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?', (str(user_id), limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

def save_user_fact(user_id, key, value):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_profile (user_id, fact_key, fact_value) 
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, fact_key) DO UPDATE SET fact_value = excluded.fact_value
    ''', (str(user_id), key, value))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT fact_key, fact_value FROM user_profile WHERE user_id = ?', (str(user_id),))
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

init_db()

# --- HILFSFUNKTIONEN & TOOLS ---
def search_web(query):
    try:
        print(f"[ADMIN LOG] 🔍 KI startet Live-Websuche für: {query}", flush=True)
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if not results:
                return "Keine aktuellen Web-Ergebnisse gefunden."
            return "\n".join([f"• {item.get('title', '')}: {item.get('body', '')}" for item in results])
    except Exception as e:
        print(f"[ADMIN LOG] ⚠️ Web Search Fehler: {e}", flush=True)
        return "Websuche derzeit nicht erreichbar."

ai_tools = [
    {
        "type": "function",
        "function": {
            "name": "save_user_fact",
            "description": "Speichert oder aktualisiert einen wichtigen Fakt oder eine Vorliebe über den User (z.B. Wohnort, Hobbys, Projekte).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Name des Fakts (z.B. 'wohnort')."},
                    "value": {"type": "string", "description": "Wert dazu (z.B. 'Berlin')."}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Führt eine Live-Websuche im Internet durch, um aktuelle Nachrichten, Preise, Daten oder Fakten zu finden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Der Suchbegriff für die Abfrage."}
                },
                "required": ["query"]
            }
        }
    }
]

SYSTEM_PROMPT = (
    "Du bist 'KI Sekretär', ein hochkompetenter, proaktiver KI-Assistent. "
    "Erinnere dich exakt an den Kontext und nutze bei Bedarf aktiv das Websuch-Tool, um aktuelle Fragen des Nutzers zu beantworten."
)

def clean_think_tags(text):
    if not text:
        return ""
    if "</think>" in text:
        return text.split("</think>")[-1].strip()
    return text

def send_telegram_message(chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    final_text = f"{text}\n\n[Team: {model_name}]" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try:
        response = requests.post(url, json={"chat_id": chat_id, "text": final_text}, timeout=5)
        res_json = response.json()
        if res_json.get("ok"):
            return res_json["result"]["message_id"]
        else:
            print(f"[ADMIN LOG] ❌ Telegram Ablehnung: {res_json}", flush=True)
    except Exception as e:
        print(f"Fehler beim Telegram-Senden: {e}")
    return None

def edit_telegram_message(chat_id, message_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    final_text = f"{text}\n\n[Team: {model_name}]" if str(chat_id) == ADMIN_USER_ID and model_name else text
    try:
        requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": final_text}, timeout=5)
    except Exception as e:
        print(f"Fehler beim Bearbeiten: {e}")

def get_telegram_file_bytes(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=5).json()
        if not r.get("ok"): return None
        return requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{r['result']['file_path']}", timeout=10).content
    except Exception as e:
        print(f"Fehler Bild-Download: {e}")
        return None

def call_groq_text(messages_list):
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_TEXT_MODEL, messages=messages_list, tools=ai_tools, tool_choice="auto", temperature=0.7, max_tokens=1024
        )
        msg = response.choices[0].message
        return clean_think_tags(msg.content or ""), f"Groq ({GROQ_TEXT_MODEL})", msg.tool_calls
    except Exception as e:
        print(f"Groq Fehler: {e}", flush=True)
        return "Technischer Fehler aufgetreten.", "Groq (Fehler)", None

def call_groq_vision(user_text, image_bytes):
    try:
        b64 = io.base64.b64encode(image_bytes).decode('utf-8')
        response = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_text or "Bild analysieren"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}],
            temperature=0.5, max_tokens=1024
        )
        return clean_think_tags(response.choices[0].message.content), "Qwen Vision"
    except Exception as e:
        print(f"Vision Fehler: {e}", flush=True)
        return "Bildanalyse-Fehler", "Groq (Fehler)"

def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    try:
        if chat_id not in user_balances: user_balances[chat_id] = INITIAL_BALANCE
        save_message(chat_id, "user", user_text or "{Bild gesendet}")
        
        profile = get_user_profile(chat_id)
        history = get_history(chat_id, limit=MAX_HISTORY_LENGTH)
        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\nProfil: {json.dumps(profile, ensure_ascii=False)}"}] + history

        if image_bytes:
            bot_reply, used_model_name = call_groq_vision(user_text, image_bytes)
            tool_calls = None
        else:
            bot_reply, used_model_name, tool_calls = call_groq_text(messages)

        if tool_calls:
            for tc in tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                
                if func_name == "save_user_fact":
                    save_user_fact(chat_id, args.get("key"), args.get("value"))
                    bot_reply = f"Habe mir gemerkt: {args.get('key')} = {args.get('value')}"
                
                elif func_name == "search_web":
                    search_result = search_web(args.get("query"))
                    messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": search_result})
                    bot_reply, used_model_name, _ = call_groq_text(messages)

        bot_reply = bot_reply or "Es ist ein unerwarteter Fehler aufgetreten."
        save_message(chat_id, "assistant", bot_reply)

        if loading_msg_id:
            edit_telegram_message(chat_id, loading_msg_id, bot_reply, model_name=used_model_name)
        else:
            send_telegram_message(chat_id, bot_reply, model_name=used_model_name)
    except Exception as e:
        print(f"Worker Fehler: {e}", flush=True)

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data or "message" not in data: return "OK", 200
        msg = data["message"]
        chat_id = str(msg["chat"]["id"])
        user_text = msg.get("text", msg.get("caption", ""))
        image_bytes = get_telegram_file_bytes(msg["photo"][-1]["file_id"]) if "photo" in msg else None
        
        if not user_text and image_bytes: user_text = "Was ist auf diesem Bild?"
        if not user_text and not image_bytes: return "OK", 200

        loading_msg_id = send_telegram_message(chat_id, "Analysiere..." if image_bytes else "Verarbeite...")
        executor.submit(process_message_async, chat_id, user_text, image_bytes, loading_msg_id)
    except Exception as e:
        print(f"Webhook Fehler: {e}", flush=True)
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping():
    return "Bot is alive!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
