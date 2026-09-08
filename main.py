import os
import io
import base64
import concurrent.futures
from flask import Flask, request
from groq import Groq
import requests
from PIL import Image
from duckduckgo_search import DDGS

app = Flask(__name__)

# --- Konfiguration & API-Schlüssel ---
TELEGRAM_BOT_TOKEN = "8818900840:AAHfyoscsxqiv1wez9qtZfb1b5PbhP1YBjY"
ADMIN_USER_ID = "8874543115"  # Deine Admin-ID
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

GROQ_TEXT_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"

chat_histories = {}
user_balances = {}
INITIAL_BALANCE = 10000
MAX_HISTORY_LENGTH = 15

# Erweiterter, hochintelligenter System-Prompt für proaktives Mitdenken und Kontext
SYSTEM_PROMPT = (
    "Du bist 'Ki Sekretär', ein hochkompetenter, proaktiver, mitdenkender und ehrlicher KI-Assistent. "
    "KERN-REGEL ZUM KONTEXT: Du erinnerst dich exakt an den gesamten Gesprächsverlauf – sowohl an das, was der Nutzer gesagt hat, als auch an deine eigenen vorherigen Antworten. "
    "Wenn der Nutzer kurze Befehle gibt (z. B. 'Plane es', 'Mach das', 'Mehr Details', 'Zeig mir mehr'), beziehe das IMMER intelligent und direkt auf den Inhalt der unmittelbar vorhergehenden Nachrichten (z. B. wenn vorher über Hotels in Istanbul gesprochen wurde, bedeutet 'Plane es' automatisch die Planung für diese Istanbul-Hotels). "
    "PROAKTIVES HANDELN: Erkenne Muster, nimm dem Nutzer die Arbeit ab, erleichtere ihm Aufgaben, verschaffe ihm Vorteile, antizipiere Wünsche und beuge Problemen vor. Handle wie ein echter, mitdenkender Chef-Sekretär. "
    "WICHTIG: Nutze KEINE internen Browser-Tools, Websuchen oder externe Funktionen. Wenn du Live-Daten benötigst, werden dir diese bereits vom System im Chat bereitgestellt. "
    "REGEL ZU KOSTEN: Alles, was mit reinem Wissen, Live-Suche oder Bildanalyse zu tun hat, ist für den Nutzer völlig kostenlos. "
    "Wenn der Nutzer verlangt, dass du aktiv wirst (z. B. externe Geschäfte anschreibst, Web-Scraping machst oder Verhandlungen führst), "
    "prüfe, ob das kostenpflichtige Dienste erfordert. Wenn ja, antworte direkt: "
    "'Das kann ich machen, aber das erfordert externe Dienste und kostet ca. [Betrag]. Soll ich das tun?'"
)

def clean_think_tags(text):
    if "</think>" in text:
        return text.split("</think>")[-1].strip()
    return text

def send_telegram_message(chat_id, text, model_name=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Zeige das Team-/Quellen-Tag NUR für dich als Admin an
    if str(chat_id) == ADMIN_USER_ID and model_name:
        final_text = f"{text}\n\n[Team: {model_name}]"
    else:
        final_text = text

    payload = {
        "chat_id": chat_id,
        "text": final_text
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    
    if str(chat_id) == ADMIN_USER_ID and model_name:
        final_text = f"{text}\n\n[Team: {model_name}]"
    else:
        final_text = text

    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": final_text
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Fehler beim Bearbeiten der Telegram-Nachricht: {e}", flush=True)

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
        print(f"Fehler beim Herunterladen des Telegram-Bildes: {e}", flush=True)
        return None

def search_web(query):
    try:
        print(f"[ADMIN LOG] 🔍 Starte DuckDuckGo Websuche für: '{query}'", flush=True)
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
            snippets = [f"- {item['title']}: {item['body']} ({item['href']})" for item in results]
            print(f"[ADMIN LOG] ✅ Websuche erfolgreich ({len(results)} Ergebnisse gefunden)", flush=True)
            return "\n".join(snippets)
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Web Search Fehler: {e}", flush=True)
    return None

def call_groq_text(history, search_context=None):
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        if search_context:
            messages.append({
                "role": "system", 
                "content": f"Aktuelle Live-Suchergebnisse aus dem Internet:\n{search_context}\nNutze diese Infos, um deine proaktive Antwort zu bereichern."
            })
            
        messages.extend(history)
        
        response = groq_client.chat.completions.create(
            model=GROQ_TEXT_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        reply = clean_think_tags(reply)
        
        model_tag = "DuckDuckGo Live-Suche + Groq" if search_context else f"Groq ({GROQ_TEXT_MODEL.split('/')[-1]} - Wissen)"
        print(f"[ADMIN LOG] 🤖 Antwort generiert via [{model_tag}]", flush=True)
        
        return reply, model_tag
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Groq Text Fehler: {e}", flush=True)
        return f"Es ist ein technischer Fehler aufgetreten: {e}", "Groq (Fehler)"

def call_groq_vision(user_text, image_bytes):
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
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
        return reply, "Groq Vision (Bildanalyse)"
    except Exception as e:
        print(f"[ADMIN LOG] ❌ Groq Vision Fehler: {e}", flush=True)
        return f"Bildanalyse-Fehler: {e}", "Groq (Fehler)"

def process_message_async(chat_id, user_text, image_bytes, loading_msg_id):
    try:
        if chat_id not in user_balances:
            user_balances[chat_id] = INITIAL_BALANCE
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
            
        lower_text = user_text.lower() if user_text else ""
        content_desc = user_text if user_text else "[Bild gesendet]"
        chat_histories[chat_id].append({"role": "user", "content": content_desc})
        
        if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
            chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]
            
        search_context = None
        live_triggers = ["wetter", "heute", "morgen", "aktuell", "nachrichten", "news", "wie ist", "wer ist", "was ist", "spielstand", "kurs", "hotel", "antalya", "istanbul", "rezensionen"]
        
        if image_bytes is not None:
            bot_reply, used_model_name = call_groq_vision(user_text, image_bytes)
        else:
            if any(trigger in lower_text for trigger in live_triggers):
                search_context = search_web(user_text)
            bot_reply, used_model_name = call_groq_text(chat_histories[chat_id], search_context=search_context)
            
        if not bot_reply:
            bot_reply = "Es ist ein unerwarteter Fehler aufgetreten."
            
        chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
        edit_telegram_message(chat_id, loading_msg_id, bot_reply, model_name=used_model_name)
            
    except Exception as e:
        print(f"[ADMIN LOG] ❌ FEHLER IM BACKGROUND WORKER: {e}", flush=True)

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
        print(f"[ADMIN LOG] ❌ KRITISCHER FEHLER IM WEBHOOK: {e}", flush=True)
        
    return "OK", 200

@app.route("/ping", methods=["GET"])
def ping_server():
    return "Bot is awake and running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(0.0.0.0, port=port)
