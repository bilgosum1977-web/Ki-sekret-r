import os
import requests
from flask import Flask, request

app = Flask(__name__)

# API-Schlüssel aus den Render Environment Variables laden (Korrektur: separate Variablen)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
XAI_GROK_API_KEY = os.getenv("GROK_API_KEY")  # Getrennt von Groq!
APIFY_API_KEY = os.getenv("APIFY_API_KEY")
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")  # Optional

# Business Model Konfiguration
INITIAL_BALANCE = 2.50
COST_PER_PREMIUM_TASK = 0.50

# In-Memory Speicher für User-Guthaben und Chat-Verläufe
user_balances = {}
chat_histories = {}
MAX_HISTORY_LENGTH = 10


def send_telegram_message(chat_id, text):
  """Sendet eine Nachricht an den Telegram Chat"""
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Telegram Sende-Fehler: {e}")


def call_groq_llama(history):
  """Kostenloser Haupt-Worker: Groq (Llama)"""
  if not GROQ_API_KEY:
    print("Groq API Key fehlt oder ist leer!")
    return None
  url = "https://api.groq.com/openai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {"model": "llama-3.1-70b-versatile", "messages": history}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      return response.json()["choices"][0]["message"]["content"]
    else:
      print(f"Groq API Fehler: {response.text}")
  except Exception as e:
    print(f"Groq Fehler: {e}")
  return None


def call_gemini(history):
  """Google Gemini (Backup)"""
  if not GEMINI_API_KEY:
    return None
  url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
  contents = []
  for msg in history:
    role = "user" if msg["role"] == "user" else "model"
    contents.append({"role": role, "parts": [{"text": msg["content"]}]})

  payload = {"contents": contents}
  try:
    response = requests.post(url, json=payload, timeout=10)
    if response.status_code == 200:
      res_data = response.json()
      return res_data["candidates"][0]["content"]["parts"][0]["text"]
    else:
      print(f"Gemini API Fehler: {response.text}")
  except Exception as e:
    print(f"Gemini Fehler: {e}")
  return None


def call_grok(history):
  """xAI Grok 4 (Backup)"""
  if not XAI_GROK_API_KEY:
    return None
  url = "https://api.x.ai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {XAI_GROK_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {"model": "grok-4", "messages": history}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      return response.json()["choices"][0]["message"]["content"]
    else:
      print(f"Grok API Fehler: {response.text}")
  except Exception as e:
    print(f"Grok Fehler: {e}")
  return None


def call_openai_gpt(history):
  """OpenAI GPT (für Automatisierungen/Joker)"""
  if not OPENAI_API_KEY:
    return "OpenAI API Key fehlt."
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
    else:
      print(f"OpenAI API Fehler: {response.text}")
  except Exception as e:
    print(f"OpenAI Fehler: {e}")
  return None


def run_apify_scraper(prompt):
  """Apify Web-Scraping Integration"""
  if not APIFY_API_KEY:
    return "Apify API Key fehlt im System."
  url = f"https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items?token={APIFY_API_KEY}"
  payload = {"queries": prompt, "maxPagesPerQuery": 1, "resultsPerPage": 3}
  try:
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code == 201 or response.status_code == 200:
      data = response.json()
      if data and isinstance(data, list):
        snippets = [
            item.get("description", item.get("title", "")) for item in data[:3]
        ]
        return (
            "Ergebnis vom Web-Scraping:\n"
            + "\n".join([s for s in snippets if s])
        )
      return "Scraper erfolgreich, aber keine passenden Daten gefunden."
    else:
      return f"Fehler beim Scraping: {response.status_code}"
  except Exception as e:
    return f"Scraper-Fehlgeschlagen: {str(e)}"


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

  if chat_id not in user_balances:
    user_balances[chat_id] = INITIAL_BALANCE

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

  is_premium_request = (
      "scrape" in text_lower
      or "web durchsuchen" in text_lower
      or "make" in text_lower
      or "automatisch" in text_lower
  )

  if is_premium_request:
    if user_balances[chat_id] < COST_PER_PREMIUM_TASK:
      send_telegram_message(
          chat_id,
          "❌ **Nicht genug Guthaben!** Diese Aktion kostet"
          f" {COST_PER_PREMIUM_TASK:.2f} €. Dein Kontostand liegt bei"
          f" {user_balances[chat_id]:.2f} €.",
      )
      return "OK", 200

    user_balances[chat_id] -= COST_PER_PREMIUM_TASK

    if "scrape" in text_lower or "web durchsuchen" in text_lower:
      send_telegram_message(
          chat_id,
          f"💡 **Premium-Service:** -{COST_PER_PREMIUM_TASK:.2f} € abgezogen."
          f" Restguthaben: **{user_balances[chat_id]:.2f} €**. Starte"
          " Web-Abfrage...",
      )
      bot_reply = run_apify_scraper(user_text)
    else:
      send_telegram_message(
          chat_id,
          f"⚙️ **Automatisierung:** -{COST_PER_PREMIUM_TASK:.2f} € abgezogen."
          f" Restguthaben: **{user_balances[chat_id]:.2f} €**. Starte"
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

  else:
    # Hier greift jetzt zuerst Llama (Groq)
    bot_reply = call_groq_llama(current_history)

    # Wenn Groq ausfällt, nimm Gemini
    if not bot_reply:
      bot_reply = call_gemini(current_history)

    # Wenn auch das ausfällt, nimm Grok 4
    if not bot_reply:
      bot_reply = call_grok(current_history)

  if not bot_reply:
    bot_reply = (
        "Entschuldigung, im Moment sind alle Leitungen belegt. Bitte versuche"
        " es gleich noch einmal."
    )

  chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
  send_telegram_message(chat_id, bot_reply)

  return "OK", 200


@app.route("/", methods=["GET"])
def index():
  return "Bot is running live!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)







