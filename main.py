
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
APIFY_API_KEY = os.environ.get("APIFY_API_KEY")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Gedächtnis für Chat-Verläufe und Guthaben pro User-ID
chat_histories = {}
user_balances = {}  # Speichert das Guthaben in Euro pro User
MAX_HISTORY_LENGTH = 10
INITIAL_BALANCE = 2.50  # Startguthaben zum Testen (entspricht 5 Premium-Aktionen)
COST_PER_PREMIUM_TASK = 0.50


def send_telegram_message(chat_id, text):
  """Sendet eine Nachricht an den Telegram-User."""
  url = f"{TELEGRAM_API_URL}/sendMessage"
  payload = {"chat_id": chat_id, "text": text}
  try:
    requests.post(url, json=payload)
  except Exception as e:
    print(f"Fehler beim Senden an Telegram: {e}")


def call_groq_llama(history):
  """Haupt-Worker: Llama 3.3 via Groq (Kostenlos)"""
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
  """Experte für lange Texte: Gemini 1.5 Flash (Kostenlos)"""
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
  """Kostenpflichtiger Joker 2: Grok 4"""
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


def run_apify_scraper(query):
  """Apify Web-Scraper"""
  if not APIFY_API_KEY:
    return "Apify API Key ist nicht konfiguriert."
  url = f"https://api.apify.com/v2/acts/apify~web-scraper/run-sync-get-dataset-items?token={APIFY_API_KEY}"
  payload = {"startUrls": [{"url": f"https://www.google.com/search?q={query}"}]}
  try:
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code in [200, 201]:
      return "Web-Abfrage erfolgreich abgeschlossen."
  except Exception as e:
    print(f"Apify Fehler: {e}")
  return "Fehler beim Ausführen des Web-Scrapers."


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

  # Guthaben initialisieren, falls neuer User
  if chat_id not in user_balances:
    user_balances[chat_id] = INITIAL_BALANCE

  # Befehl zum Guthaben abfragen
  if user_text.lower() in ["/guthaben", "guthaben", "balance"]:
    send_telegram_message(
        chat_id,
        f"💳 **Dein Kontostand:** {user_balances[chat_id]:.2f} €\n(Kosten"
        f" pro Premium-Aufgabe: {COST_PER_PREMIUM_TASK:.2f} €)",
    )
    return "OK", 200

  if chat_id not in chat_histories:
    chat_histories[chat_id] = []

  chat_histories[chat_id].append({"role": "user", "content": user_text})

  if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
    chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]

  current_history = chat_histories[chat_id]
  bot_reply = None
  text_lower = user_text.lower()

  # 1. Premium-Aufgaben (Scraping oder Automatisierung)
  is_premium_request = (
      "scrape" in text_lower
      or "web durchsuchen" in text_lower
      or "make" in text_lower
      or "automatisch" in text_lower
  )

  if is_premium_request:
    # Prüfen ob genug Guthaben da ist
    if user_balances[chat_id] < COST_PER_PREMIUM_TASK:
      send_telegram_message(
          chat_id,
          "❌ **Nicht genug Guthaben!** Diese Aktion kostet"
          f" {COST_PER_PREMIUM_TASK:.2f} €. Dein Kontostand liegt bei"
          f" {user_balances[chat_id]:.2f} €. Bitte lade dein Guthaben auf.",
      )
      return "OK", 200

    # Guthaben abziehen
    user_balances[chat_id] -= COST_PER_PREMIUM_TASK

    # Ausführen je nach Art
    if "scrape" in text_lower or "web durchsuchen" in text_lower:
      send_telegram_message(
          chat_id,
          f"💡 **Premium-Service:** -{COST_PER_PREMIUM_TASK:.2f} € abgezogen."
          f" Restguthaben: **{user_balances[chat_id]:.2f} €**. Führe Abfrage"
          " aus...",
      )
      bot_reply = run_apify_scraper(user_text)
    else:
      send_telegram_message(
          chat_id,
          f"⚙️ **Automatisierungs-Service:** -{COST_PER_PREMIUM_TASK:.2f} €"
          f" abgezogen. Restguthaben: **{user_balances[chat_id]:.2f} €**. Starte"
          " Workflow...",
      )
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

  # 2. Kostenlose Standard-Anfragen
  else:
    if len(user_text) > 800:
      bot_reply = call_gemini(current_history)
    else:
      bot_reply = call_groq_llama(current_history)

  # 3. Fallbacks (Joker) - Falls die kostenlosen ausfallen, zieht es ebenfalls den Betrag ab
  if not bot_reply and not is_premium_request:
    if user_balances[chat_id] >= COST_PER_PREMIUM_TASK:
      user_balances[chat_id] -= COST_PER_PREMIUM_TASK
      send_telegram_message(
          chat_id,
          "⚠️ **Hinweis:** Server ausgelastet. Premium-Joker aktiv"
          f" (-{COST_PER_PREMIUM_TASK:.2f} €). Restguthaben:"
          f" **{user_balances[chat_id]:.2f} €**.",
      )
      bot_reply = call_grok(current_history)
    else:
      bot_reply = (
          "Entschuldigung, unsere Server sind überlastet und dein Guthaben"
          " reicht für den Notfall-Joker nicht mehr aus."
      )

  if not bot_reply and is_premium_request:
    # Falls OpenAI im Premium-Zweig versagt hat, probieren wir Grok
    bot_reply = call_grok(current_history)

  if not bot_reply:
    bot_reply = (
        "Entschuldigung, im Moment ist ein Fehler aufgetreten. Bitte versuche"
        " es gleich noch einmal."
    )

  chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
  send_telegram_message(chat_id, bot_reply)

  return "OK", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)




