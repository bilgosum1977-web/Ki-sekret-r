import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


def send_telegram_message(chat_id, text):
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
  requests.post(url, json=payload, timeout=5)


@app.route("/", methods=["POST"])
def webhook():
  data = request.get_json()
  if not data or "message" not in data:
    return "OK", 200

  message = data["message"]
  chat_id = message["chat"]["id"]
  user_text = message.get("text", "")

  if not user_text:
    return "OK", 200

  # Test-Anfrage direkt an Groq senden
  url = "https://api.groq.com/openai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {
      "model": "llama-3.1-70b-versatile",
      "messages": [{"role": "user", "content": user_text}],
  }

  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      bot_reply = response.json()["choices"][0]["message"]["content"]
    else:
      bot_reply = f"Groq API Fehler ({response.status_code}): {response.text}"
  except Exception as e:
    bot_reply = f"Verbindungsfehler zu Groq: {str(e)}"

  send_telegram_message(chat_id, bot_reply)
  return "OK", 200


@app.route("/", methods=["GET"])
def index():
  return "Groq Test Bot is running!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)








