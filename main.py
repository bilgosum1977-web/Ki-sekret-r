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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "8874543115")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

GROQ_TEXT_MODEL = "openai/gpt-oss-20b"  # Oder dein bevorzugtes Groq-Modell
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"

user_balances = {}
INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 15

# --- DATENBANK SETUP (SQLite) ---
DB_PATH = os.getenv("DB_PATH", "bot_memory.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 1. Chat-Verlauf (Episodisch)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 2. Dauerhaftes Profil (Semantisch)
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

# Datenbank beim Start initialisieren
init_db()


# --- GROQ TOOLS FÜR FAKTEN-ERKENNUNG ---
ai_tools = [
    {
        "type": "function",
        "function": {
            "name": "save_user_fact",
            "description": "Speichert oder aktualisiert einen wichtigen Fakt oder eine Vorliebe über den User (z.B. Programmiersprache, Wohnort, Projektname).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Der Name des Fakts (z.B. 'programmiersprache', 'wohnort')."
                    },
                    "value": {
                        "type": "string",
                        "description": "Der Wert dazu (z.B. 'Python', 'Berlin')."
                    }
                },
                "required": ["key", "value"]
            }
        }
    }
]


# --- SYSTEM-PROMPT ---
SYSTEM_PROMPT = (
    "Du bist 'KI Sekretär', ein hochkompetenter, proaktiver, mitdenkender und ehrlicher KI-Assistent. "
    "KERN-REGEL ZUM KONTEXT: Du erinnerst dich exakt an den gesamten Gesprächsverlauf sowie an das, was der Nutzer "
    "gesagt hat, als auch an deine eigenen vorherigen Antworten. "
    "Wenn der Nutzer kurze Befehle gibt (z. B. 'Plane es', 'Mach das', 'Mehr Details', 'Zeig mir mehr'), beziehe das IMMER "
    "intelligent und direkt auf den Inhalt der unmittelbar vorherigen Nachrichten. "
    "PROAKTIVES HANDELN: Erkenne Muster, nimm dem Nutzer die Arbeit ab, erleichtere ihm Aufgaben, verschaffe ihm Vorteile, "
    "antizipiere Wünsche und beuge Problemen vor. Handle wie ein echter, mitdenkender Chef-Sekretär. "
    "WICHTIG: Nutze KEINE internen Browser-Tools, Websuchen oder externe Funktionen. Wenn du Live-Daten benötigst, werden "
    "dir diese bereits vom System im Chat bereitgestellt. "
    "REGEL ZU KOSTEN: Alles, was mit reinen Wissen, Live-Suche oder Bildanalyse zu tun hat, ist für den Nutzer völlig kostenlos. "
    "Präfe, ob das kostenpflichtige Dienste erfordert. Wenn ja, antworte direkt: "
    "'Das kann ich machen, aber das erfordert externe Dienste und kostet ca. [Betrag]. Soll ich das tun?'"
)


# --- HILFSFUNKTIONEN ---
def clean_think_tags(text):
    if "</think>" in text:
        return text.split("</think>")[-1].strip()
    return text

def send_telegram_message(chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if str(chat_id) == ADMIN_USER_ID and model_name:
        final_text = f"{text}\n\n[Team: {model_name}]"
    else:
        final_text = text
    
    payload = {"chat_id": chat_id, "text": final_text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        res_json = response.json()
        if res_json.get("ok"):
            return res_json["result"]["message_id"]
    except Exception as e:
        print(f"Fehler beim Telegram-Senden: {e}")
    return None

def edit_telegram_message(chat_id, message_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    if str(chat_id) == ADMIN_USER_ID and model_name:
        final_text = f"{text}\n\n[Team: {model_name}]"
    else:
        final_text = text
        
    payload = {"chat_id": chat_id, "message_id": message_id, "text": final_text}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Fehler beim Bearbeiten der Telegram-Nachricht: {e}")

def get_telegram_file_bytes(file_id):
    try:
        file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        r = requests.get(file_info_url, timeout=5).json()
        if not r.get("ok"):
            return None
        file_path = r["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        return requests.get(file_url, timeout=10).content
    except Exception as e:
        print(f"Fehler beim Herunterladen des Telegram-Bildes: {e}")
        return None

def search_web(query):
    try:
        print(f"[ADMIN LOG] 🔍 Starte DuckDuckGo Websuche für: '{query}'", flush=True)
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
            snippets = [f"• {item['title']}: {item['body']} ({item['href']})" for item in results]
            print(f"[ADMIN LOG] ✅ Websuche erfolgreich ({len(results)} Ergebnisse gefunden)", flush=True)
            return "\n".join(snippets)
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Web Search Fehler: {e}", flush=True)
        return None


# --- GROQ TEXT & TOOL AUFRUF ---
def call_groq_text(messages_list, search_context=None):
    try:
        if search_context:
            messages_list.append({
                "role": "system",
                "content": f"Aktuelle Live-Suchergebnisse aus dem Internet:\n{search_context}\nNutze diese Infos, um proaktive Antwort zu bereichern."
            })

        response = groq_client.chat.completions.create(
            model=GROQ_TEXT_MODEL,
            messages=messages_list,
            tools=ai_tools,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=1024
        )
        
        message = response.choices[0].message
        reply = message.content or ""
        
        # Tool-Calls abfangen (Fakten im Profil speichern)
        if message.tool_calls:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "save_user_fact":
                    args = json.loads(tool_call.function.arguments)
                    # Da wir die chat_id hier indirekt brauchen, holen wir sie uns oder nutzen ein globales Event
                    # (Im Background-Worker übergeben wir die chat_id sauber)
                    print(f"[GEDÄCHTNIS UPDATE VIA TOOL] {args.get('key')} = {args.get('value')}")
            
        reply = clean_think_tags(reply)
        model_tag = "DuckDuckGo Live-Suche + Groq" if search_context else f"Groq ({GROQ_TEXT_MODEL.split('/')[-1]})"
        print(f"[ADMIN LOG] 🤖 Antwort generiert via [{model_tag}]", flush=True)
        return reply, model_tag, message.tool_calls
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Groq Text Fehler: {e}", flush=True)
        return "Es ist ein technischer Fehler aufgetreten.", "Groq (Fehler)", None


# --- GROQ VISION AUFRUF ---
def call_groq_vision(user_text, image_bytes):
    try:
        base64_image = io.base64.b64encode(image_bytes).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"
        prompt = user_text if user_text else "Was ist auf diesem Bild zu sehen?"

        response = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }],
            temperature=0.5,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        reply = clean_think_tags(reply)
        print(f"[ADMIN LOG] 👁️ Bildanalyse erfolgreich mit Qwen Vision", flush=True)
        return reply, "Qwen Vision"
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Groq Vision Fehler: {e}", flush=True)
        return "Bildanalyse-Fehler", "Groq (Fehler)"


# --- ASYNCHRONE NACHRICHTEN-VERARBEITUNG ---
def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    try:
        if chat_id not in user_balances:
            user_balances[chat_id] = INITIAL_BASE_BALANCE = INITIAL_BALANCE

        # 1. User-Nachricht in SQLite speichern
        content_desc = user_text if user_text else "{Bild gesendet}"
        save_message(chat_id, "user", content_desc)

        # 2. Kontext laden: Profil + Historie aus SQLite
        profile = get_user_profile(chat_id)
        history = get_history(chat_id, limit=MAX_HISTORY_LENGTH)

        # System-Prompt dynamisch mit Profil anreichern
        dynamic_system_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Das weißt du bereits über diesen Nutzer (Langzeit-Profil): {json.dumps(profile, ensure_ascii=False)}"
        )
        messages = [{"role": "system", "content": dynamic_system_prompt}] + history

        search_context = None
        live_triggers = ["wetter", "heute", "morgen", "aktuell", "nachrichten", "news", "wie ist", "wer ist", "was ist", "spielstand", "kurs", "hotel", "antalya", "istanbul", "rezensionen"]
        lower_text = user_text.lower() if user_text else ""

        if image_bytes is not None:
            bot_reply, used_model_name = call_groq_vision(user_text, image_bytes)
        else:
            if any(trigger in lower_text for trigger in live_triggers):
                search_context = search_web(user_text)
            
            bot_reply, used_model_name, tool_calls = call_groq_text(messages, search_context=search_context)
            
            # Tools (Fakten speichern) für diesen User ausführen
            if tool_calls:
                for tc in tool_calls:
                    if tc.function.name == "save_user_fact":
                        args = json.loads(tc.function.arguments)
                        save_user_fact(chat_id, args.get("key"), args.get("value"))

        if not bot_reply:
            bot_reply = "Es ist ein unerwarteter Fehler aufgetreten."

        # 3. Assistenten-Antwort in SQLite speichern
        save_message(chat_id, "assistant", bot_reply)
        
        # An Telegram senden
        edit_telegram_message(chat_id, loading_msg_id, bot_reply, model_name=used_model_name)

    except Exception as e:
        print(f"[ADMIN LOG] ❌ FEHLER IM BACKGROUND WORKER: {e}", flush=True)


# --- WEBHOOK ROUTE ---
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data or "message" not in data:
            return "OK", 200

        message = data["message"]
        chat_id = str(message["chat"]["id"])
        user_text = message.get("text", message.get("caption", ""))
        image_bytes = None

        if "photo" in message:
            photo_array = message["photo"]
            file_id = photo_array[-1]["file_id"]
            image_bytes = get_telegram_file_bytes(file_id)
            if not user_text:
                user_text = "Was ist auf diesem Bild zu sehen?"

        if not user_text and not image_bytes:
            return "OK", 200

        loading_text = "Analysiere das Bild..." if image_bytes else "Verarbeite Anfrage..."
        loading_msg_id = send_telegram_message(chat_id, loading_text)

        executor.submit(process_message_async, chat_id, user_text, image_bytes, loading_msg_id)

    except Exception as e:
        print(f"[ADMIN LOG] ❌ KRITISCHER FEHLER IM WEBHOOK: {e}", flush=True)

    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping_server():
    return "Bot is awake and running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
