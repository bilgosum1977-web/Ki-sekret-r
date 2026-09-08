import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


def send_telegram_message(chat_id, text):
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Fehler: {e}")


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

  # Korrekter Aufruf mit dem neuen Groq-Modell
  url = "https://api.groq.com/openai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {
      "model": "llama-3.1-8b-instant",
      "messages": [{"role": "user", "content": user_text}],
  }

  try:
    res = requests.post(url, json=payload, headers=headers, timeout=10)
    if res.status_code == 200:
      reply = res.json()["choices"][0]["message"]["content"]
    else:
      reply = f"Groq Fehler: {res.status_code} - {res.text}"
  except Exception as e:
    reply = f"Exception: {str(e)}"

  send_telegram_message(chat_id, reply)
  return "OK", 200


@app.route("/", methods=["GET"])
def index():
  return "OK", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)










