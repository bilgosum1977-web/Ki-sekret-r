import os
import requests
from flask import Flask, request

app = Flask(__name__)

# API Keys aus den Render Environment Variables auslesen
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GROK_API_KEY = os.environ.get("GROK_API_KEY")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Zentrales RAM-Gedächtnis: Speichert die letzten Nachrichten pro User-ID
chat_histories = {}
MAX_HISTORY_LENGTH = 10


def send_telegram_message(chat_id, text):
  """Sendet eine Nachricht an den Telegram-User."""
  url = f"{TELEGRAM_API_URL}/sendMessage"
  payload = {"chat_id": chat_id, "text": text}
  try:
    requests.post(url, json=payload)
  except Exception as e:
    print(f"Fehler beim Senden an Telegram: {e}")


def call_groq_llama(history):
  """Haupt-Worker: Llama 3.3 via Groq (Schnell & Kostenlos)"""
  if not GROQ_API_KEY:
    return None
  url = "https://api.groq.com/openai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {"model": "llama-3.3-70b-versatile", "messages": history}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      return response.json()["choices"][0]["message"]["content"]
  except Exception as e:
    print(f"Groq/Llama Fehler: {e}")
  return None


def call_gemini(history):
  """Experte für lange Texte: Gemini 1.5 Flash (Kostenlos/Günstig)"""
  if not GEMINI_API_KEY:
    return None
  contents = []
  for msg in history:
    role = "user" if msg["role"] == "user" else "model"
    contents.append({"role": role, "parts": [{"text": msg["content"]}]})

  url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
  headers = {"Content-Type": "application/json"}
  payload = {"contents": contents}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    if response.status_code == 200:
      return response.json()["candidates"][0]["content"]["parts"][0]["text"]
  except Exception as e:
    print(f"Gemini Fehler: {e}")
  return None


def call_openai_gpt(history):
  """Kostenpflichtiger Joker 1: OpenAI GPT-4o-mini"""
  if not OPENAI_API_KEY:
    return None
  url = "https://api.openai.com/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {OPENAI_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {"model": "gpt-4o-mini", "messages": history}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      return response.json()["choices"][0]["message"]["content"]
  except Exception as e:
    print(f"OpenAI Fehler: {e}")
  return None


def call_grok(history):
  """Kostenpflichtiger Joker 2: Grok 4 (xAI)"""
  if not GROK_API_KEY:
    return None
  url = "https://api.x.ai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROK_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {"model": "grok-4", "messages": history}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      return response.json()["choices"][0]["message"]["content"]
  except Exception as e:
    print(f"Grok Fehler: {e}")
  return None


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

  if chat_id not in chat_histories:
    chat_histories[chat_id] = []

  chat_histories[chat_id].append({"role": "user", "content": user_text})

  if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
    chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]

  current_history = chat_histories[chat_id]
  bot_reply = None

  # --- ROUTING NACH ANFRAGE-TYP ---
  text_lower = user_text.lower()

  # 1. Automatisierung / Make -> Startet direkt mit OpenAI
  if "make" in text_lower or "automatisch" in text_lower:
    print("Router: Anfrage zielt auf OpenAI...")
    bot_reply = call_openai_gpt(current_history)
    if MAKE_WEBHOOK_URL:
      try:
        requests.post(
            MAKE_WEBHOOK_URL,
            json={"chat_id": chat_id, "message": user_text},
            timeout=5,
        )
      except Exception as e:
        print(f"Make Webhook Fehler: {e}")

  # 2. Lange Texte -> Startet mit Gemini
  elif len(user_text) > 800:
    print("Router: Anfrage zielt auf Gemini...")
    bot_reply = call_gemini(current_history)

  # 3. Standard-Anfragen -> Startet mit dem kostenlosen Llama
  else:
    print("Router: Anfrage zielt auf Llama (Groq)...")
    bot_reply = call_groq_llama(current_history)

  # --- GEGENSEITIGE JOKER-KETTE (FALLBACKS) ---
  # Falls das primäre Modell versagt, greift die Kette mit gegenseitiger Absicherung:
  if not bot_reply:
    print("Primär-KI ausgefallen. Versuche Grok 4...")
    bot_reply = call_grok(current_history)

  if not bot_reply:
    print("Grok 4 konnte nicht, springe zu OpenAI...")
    bot_reply = call_openai_gpt(current_history)

  if not bot_reply:
    print("OpenAI konnte auch nicht, springe zu Gemini...")
    bot_reply = call_gemini(current_history)

  if not bot_reply:
    bot_reply = (
        "Entschuldigung, im Moment sind alle Systeme überlastet. Bitte versuche"
        " es gleich noch einmal."
    )

  chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
  send_telegram_message(chat_id, bot_reply)

  return "OK", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)




