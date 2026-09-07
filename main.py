import os
import requests
from flask import Flask, request

app = Flask(__name__)

# Deine Token aus den Render Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROK_API_KEY = os.environ.get("GROK_API_KEY")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
XAI_API_URL = "https://api.xai.ai/v1/chat/completions"  # Endpoint für Grok/xAI

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()
    
    # Prüfen, ob eine Nachricht vorhanden ist
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        user_text = data["message"]["text"]
        
        # 1. Anfrage an Grok (xAI) senden
        ai_reply = ask_grok(user_text)
        
        # 2. Antwort an Telegram zurückschicken
        send_telegram_message(chat_id, ai_reply)
        
    return "OK", 200

def ask_grok(prompt):
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-beta",  # Aktuelles Standardmodell von xAI
        "messages": [
            {"role": "system", "content": "Du bist ein hilfreicher, präziser AI Secretary."},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = requests.post(XAI_API_URL, json=payload, headers=headers)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return f"Fehler von xAI: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Fehler bei der Verbindung zur KI: {str(e)}"

def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)



