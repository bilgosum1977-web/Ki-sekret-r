import os
import concurrent.futures
from flask import Flask, request
from groq import Groq
import google.generativeai as genai
import requests

app = Flask(__name__)

# --- 1. Konfiguration & API-Schlüssel ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# SDK Clients initialisieren
groq_client = Groq(api_key=GROQ_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# Modell-Namen nach deiner Vorgabe
GROQ_MODEL = "openai/gpt-oss-20b"        # Dein gewähltes Groq-Modell
GEMINI_MODEL = "gemini-1.5-flash"       # Der Vision- & Websuche-Spezialist

# Lokaler Speicher für Chats & Guthaben
chat_histories = {}
user_balances = {}
INITIAL_BALANCE = 100
MAX_HISTORY_LENGTH = 15

SYSTEM_PROMPT = (
    "Du bist 'Ki Sekretär', ein hochkompetenter, freundlicher und effizienter KI-Assistent. "
    "Du agierst als Teil eines engen KI-Teams. Antworte präzise, professionell und auf den Punkt."
)

def send_telegram_message(chat_id, text, model_name=""):
    """Sendet die formatierte Antwort an den Telegram-Chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"{text}\n\n[Team: {model_name}]" if model_name else text
        # parse_mode bewusst weggelassen, um 400er Fehler zu verhindern!
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Fehler beim Telegram-Senden: {e}")

def get_telegram_file_bytes(file_id):
    """Lädt ein Bild direkt von den Telegram-Servern herunter."""
    try:
        file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        r = requests.get(file_info_url, timeout=5).json()
        if not r.get("ok"):
            return None
        file_path = r["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        img_data = requests.get(file_url, timeout=10).content
        return img_data
    except Exception as e:
        print(f"Fehler beim Herunterladen des Telegram-Bildes: {e}")
        return None

def call_groq_openai(history):
    """Ruft Groq mit dem Modell openai/gpt-oss-20b für Text auf."""
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        return reply, f"Groq ({GROQ_MODEL.split('/')[-1]})"
    except Exception as e:
        print(f"Groq Fehler: {e}")
        return None, None

def call_gemini(history, image_bytes=None):
    """Ruft Gemini für Web-Recherche, Dokumente und Bildanalysen auf."""
    try:
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT
        )
        
        gemini_history = []
        for msg in history[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})
            
        chat = model.start_chat(history=gemini_history)
        last_message = history[-1]["content"] if history else "Hallo"
        
        if image_bytes:
            image_part = {
                "mime_type": "image/jpeg",
                "data": image_bytes
            }
            response = chat.send_message([last_message, image_part])
        else:
            response = chat.send_message(last_message)
            
        return response.text, f"Gemini ({GEMINI_MODEL})"
    except Exception as e:
        print(f"Gemini Fehler: {e}")
        return None, None

def smart_route_message(history, user_text, image_bytes=None):
    """Smarter Team-Router:
       - Wenn Bild-Bytes da sind -> Direkt zu Gemini (Vision).
       - Wenn nur Text -> Versucht Groq (mit 2.0 Sek. Timeout). Zögert Groq, übernimmt Gemini.
    """
    if image_bytes:
        print("[DV] Echte Bilddaten vorhanden -> Direkt zu Gemini.")
        resp, model_name = call_gemini(history, image_bytes=image_bytes)
        if resp:
            return resp, model_name

    def try_groq():
        return call_groq_openai(history)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(try_groq)
        try:
            resp, model_name = future.result(timeout=2.0)
            if resp:
                return resp, model_name
        except concurrent.futures.TimeoutError:
            print("[SI] Groq hat gezögert (>2s Timeout). Gemini springt ein!")
        except Exception as e:
            print(f"[SI] Groq Fehler: {e}. Gemini übernimmt.")

    print("[SF] Fallback greift -> Gemini übernimmt.")
    resp, model_name = call_gemini(history, image_bytes=None)
    if resp:
        return resp, model_name
        
    return "Entschuldigung, im Moment sind alle Leitungen überlastet.", "System-Fallback"

@app.route("/webhook", methods=["POST"])
def webhook():
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
            user_text = "Was ist auf diesen Bild zu sehen?"
            
    if not user_text and not image_bytes:
        return "OK", 200
        
    if chat_id not in user_balances:
        user_balances[chat_id] = INITIAL_BALANCE
        
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
        
    content_desc = user_text if user_text else "[Bild gesendet]"
    chat_histories[chat_id].append({"role": "user", "content": content_desc})
    
    if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]
        
    current_history = chat_histories[chat_id]
    
    bot_reply, used_model_name = smart_route_message(current_history, user_text, image_bytes=image_bytes)
    
    if not bot_reply:
        bot_reply = "Es ist ein unerwarteter Fehler aufgetreten."
        
    chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
    send_telegram_message(chat_id, bot_reply, model_name=used_model_name)
    
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping_server():
    """Keep-Alive Endpunkt für UptimeRobot/Render."""
    return "Bot is awake and running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
