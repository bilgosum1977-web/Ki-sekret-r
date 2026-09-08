import os
import requests
from flask import Flask, request

app = Flask(__name__)

# API-Schlüssel aus den Render Environment Variables laden
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# URL für die Telegram API
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# In-Memory-Speicher für Chats und Historie
chat_histories = {}
user_balances = {}
INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 10

def send_telegram_message(chat_id, text, model_name=None):
    """Sendet eine Nachricht an den Telegram-Chat zurück (ohne parse_mode zur Fehlervermeidung)."""
    if model_name:
        text = f"{text}\n\n[Team: {model_name}]"
    
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Telegram Sende-Antwort Status: {response.status_code}", flush=True)
    except Exception as e:
        print(f"Fehler beim Senden der Telegram-Nachricht: {e}", flush=True)

def get_telegram_file_bytes(file_id):
    """Lädt ein Bild von Telegram herunter, falls der Nutzer eins geschickt hat."""
    try:
        file_info_url = f"{TELEGRAM_API_URL}/getFile?file_id={file_id}"
        resp = requests.get(file_info_url, timeout=5).json()
        if resp.get("ok"):
            file_path = resp["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            img_resp = requests.get(download_url, timeout=10)
            if img_resp.status_code == 200:
                return img_resp.content
    except Exception as e:
        print(f"Fehler beim Herunterladen des Telegram-Files: {e}", flush=True)
    return None

def smart_route_message(history, user_text, image_bytes=None):
    """Verarbeitet die Nachricht mit den KI-Modellen."""
    if not GROQ_API_KEY and not GEMINI_API_KEY:
        return "Hallo! Ich habe deine Nachricht erhalten, aber es sind noch keine API-Schlüssel (Groq/Gemini) in Render hinterlegt.", "System-Fallback"

    response_text = f"Echo: Ich habe deine Nachricht erhalten: '{user_text}'"
    
    if GROQ_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama3-70b-8192",
                "messages": [{"role": "user", "content": user_text}]
            }
            res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                response_text = data["choices"][0]["message"]["content"]
                return response_text, "Groq (Llama3)"
            else:
                print(f"Groq API Fehler Status {res.status_code}: {res.text}", flush=True)
        except Exception as e:
            print(f"Groq API Exception: {e}", flush=True)

    return response_text, "Standard-Antwort"

@app.route("/", methods=["GET"])
def index():
    return "Bot is running and alive!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        print(f"Eingehendes JSON von Telegram: {data}", flush=True)
        
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
            
        if chat_id not in user_balances:
            user_balances[chat_id] = INITIAL_BALANCE
            
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
            
        content_desc = user_text if user_text else "[Bild gesendet]"
        chat_histories[chat_id].append({"role": "user", "content": content_desc})
        
        if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
            chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]
            
        current_history = chat_histories[chat_id]
        
        print(f"Starte Smart Routing für Text: '{user_text}'...", flush=True)
        bot_reply, used_model_name = smart_route_message(current_history, user_text, image_bytes=image_bytes)
        print(f"KI Antwort erhalten: {bot_reply} von Modell: {used_model_name}", flush=True)
        
        if not bot_reply:
            bot_reply = "Es ist ein unerwarteter Fehler aufgetreten."
            
        chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
        send_telegram_message(chat_id, bot_reply, model_name=used_model_name)
        
    except Exception as e:
        print(f"KRITISCHER FEHLER IM WEBHOOK: {e}", flush=True)
        
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
