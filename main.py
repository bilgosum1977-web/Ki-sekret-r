import os
import io
import base64
import concurrent.futures
from flask import Flask, request
from groq import Groq
import requests
from PIL import Image
from rembg import remove

app = Flask(__name__)

# --- Konfiguration & API-Schlüssel ---
TELEGRAM_BOT_TOKEN = "8818900840:AAHfyoscsxqiv1wez9qtZfb1b5PbhP1YBjY"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Groq Client initialisieren
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

# Modelle
GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"  # oder ein passendes Groq Vision Modell

# Lokaler Speicher für Chats & Guthaben
chat_histories = {}
user_balances = {}
INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 15

SYSTEM_PROMPT = (
    "Du bist 'Ki Sekretär', ein hochkompetenter, freundlicher und effizienter KI-Assistent. "
    "Antworte präzise, professionell und auf den Punkt."
)

def send_telegram_message(chat_id, text, model_name=""):
    """Sendet die formatierte Antwort an den Telegram-Chat und gibt die message_id zurück."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"{text}\n\n[Team: {model_name}]" if model_name else text
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        res_json = response.json()
        if res_json.get("ok"):
            return res_json["result"]["message_id"]
    except Exception as e:
        print(f"Fehler beim Telegram-Senden: {e}", flush=True)
    return None

def edit_telegram_message(chat_id, message_id, text, model_name=""):
    """Aktualisiert eine bestehende Nachricht."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": f"{text}\n\n[Team: {model_name}]" if model_name else text
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Fehler beim Bearbeiten der Telegram-Nachricht: {e}", flush=True)

def send_telegram_photo(chat_id, photo_bytes, caption=""):
    """Sendet ein bearbeitetes/generiertes Bild direkt an den Telegram-Chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("image.png", photo_bytes, "image/png")}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        response = requests.post(url, data=data, files=files, timeout=15)
        print(f"Telegram Foto-Sende-Antwort Status: {response.status_code}", flush=True)
    except Exception as e:
        print(f"Fehler beim Telegram-Foto-Senden: {e}", flush=True)

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
        print(f"Fehler beim Herunterladen des Telegram-Bildes: {e}", flush=True)
        return None

def call_groq_text(history):
    """Ruft Groq für reinen Text auf."""
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = groq_client.chat.completions.create(
            model=GROQ_TEXT_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        return reply, f"Groq ({GROQ_TEXT_MODEL.split('/')[-1]})"
    except Exception as e:
        print(f"Groq Text Fehler: {e}", flush=True)
        return f"Groq API Fehler: {e}", "Groq (Fehler)"

def call_groq_vision(user_text, image_bytes):
    """Analysiert Bilder über Groq Vision."""
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"
        
        prompt = user_text if user_text else "Was ist auf diesem Bild zu sehen?"
        
        response = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ],
            temperature=0.5,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        return reply, "Groq (Vision)"
    except Exception as e:
        print(f"Groq Vision Fehler: {e}", flush=True)
        return f"Groq Vision API Fehler: {e}", "Groq (Fehler)"

def process_image_with_rembg(image_bytes):
    """Entfernt lokal den Hintergrund mit RemBG (Pillow + RemBG)."""
    try:
        input_image = Image.open(io.BytesIO(image_bytes))
        output_image = remove(input_image)
        
        output_io = io.BytesIO()
        output_image.save(output_io, format="PNG")
        output_io.seek(0)
        return output_io.read()
    except Exception as e:
        print(f"RemBG Fehler: {e}", flush=True)
        return None

def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    """Verarbeitet die Nachricht im Hintergrund."""
    try:
        if chat_id not in user_balances:
            user_balances[chat_id] = INITIAL_BALANCE
            
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
            
        lower_text = user_text.lower() if user_text else ""
        
        # Prüfen, ob der Nutzer eine Bildbearbeitung (Hintergrund entfernen) wünscht
        if image_bytes is not None and any(cmd in lower_text for cmd in ["freistellen", "hintergrund entfernen", "ohne hintergrund"]):
            print("Starte lokale Bildbearbeitung (RemBG)...", flush=True)
            processed_bytes = process_image_with_rembg(image_bytes)
            if processed_bytes:
                # Lade-Nachricht löschen und das bearbeitete Bild senden
                send_telegram_photo(chat_id, processed_bytes, caption="[Team: Pillow + RemBG (Freigestellt)]")
                return
            else:
                edit_telegram_message(chat_id, loading_msg_id, "Fehler bei der Bildfreistellung.", model_name="RemBG")
                return

        content_desc = user_text if user_text else "[Bild gesendet]"
        chat_histories[chat_id].append({"role": "user", "content": content_desc})
        
        if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
            chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]
            
        # Entweder Bildanalyse oder Text-Antwort über Groq
        if image_bytes is not None:
            print("Leite Bild an Groq Vision weiter...", flush=True)
            bot_reply, used_model_name = call_groq_vision(user_text, image_bytes)
        else:
            print("Leite Text an Groq weiter...", flush=True)
            bot_reply, used_model_name = call_groq_text(chat_histories[chat_id])
            
        if not bot_reply:
            bot_reply = "Es ist ein unerwarteter Fehler aufgetreten."
            
        chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
        edit_telegram_message(chat_id, loading_msg_id, bot_reply, model_name=used_model_name)
            
    except Exception as e:
        print(f"FEHLER IN BACKGROUND WORKER: {e}", flush=True)

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
        
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(process_message_async, chat_id, user_text, image_bytes, loading_msg_id)
        
    except Exception as e:
        print(f"KRITISCHER FEHLER IM WEBHOOK: {e}", flush=True)
        
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping_server():
    return "Bot is awake and running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
