import os
import requests
from flask import Flask, request

app = Flask(__name__)

# API-Schlüssel aus den Render Environment Variables laden
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
XAI_GROK_API_KEY = os.getenv("GROK_API_KEY")
APIFY_API_KEY = os.getenv("APIFY_API_KEY")
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")

# Deine feste Admin Telegram Chat-ID
ADMIN_CHAT_ID = "8874543115"

# Business Model Konfiguration
INITIAL_BALANCE = 2.50
COST_PER_PREMIUM_TASK = 0.50

# In-Memory Speicher für User-Guthaben, Chat-Verläufe und das zuletzt genutzte Modell
user_balances = {}
chat_histories = {}
last_used_model = {}
MAX_HISTORY_LENGTH = 10

# System-Prompt: Multilingual, kostenloser Standard, absolute Geheimhaltung nach außen
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Du bist ausnahmslos der persönliche 'KI-Sekretär' und Chief of Staff"
        " des Nutzers auf Telegram. Du darfst unter keinen Umständen erwähnen,"
        " dass du von OpenAI, Groq, Google, xAI oder einem anderen Anbieter"
        " stammst. Antworte immer in der exakt selben Sprache, in der der"
        " Nutzer dich anspricht (z. B. Deutsch, Englisch, Spanisch, Französisch"
        " etc.). Die Grundversion ist komplett kostenlos. Premium-Aktionen"
        " kosten 0,50 €. Agiere stets professionell und hilfsbereit."
    ),
}


def send_telegram_message(chat_id, text, model_name=None):
  if str(chat_id) == ADMIN_CHAT_ID and model_name:
    text = f"🤖 *[Genutzte KI: {model_name}]*\n\n{text}"

  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Telegram Sende-Fehler: {e}")


def get_full_history_with_system(history):
  return [SYSTEM_PROMPT] + history


def call_groq_llama(history, chat_id):
  if not GROQ_API_KEY:
    return None, None
  url = "https://api.groq.com/openai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {
      "model": "openai/gpt-oss-20b",
      "messages": get_full_history_with_system(history),
  }
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      model_label = "Groq (Llama / Kostenlos & Multilingual)"
      last_used_model[chat_id] = model_label
      return (
          response.json()["choices"][0]["message"]["content"],
          model_label,
      )
  except Exception as e:
    print(f"Groq Fehler: {e}")
  return None, None


def call_gemini(history, chat_id):
  if not GEMINI_API_KEY:
    return None, None
  url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
  contents = [{
      "role": "user",
      "parts": [{
          "text": (
              "System instruction: You are exclusively the user's personal"
              " AI Secretary. Reply in the user's language. Never mention"
              " providers.\n\n"
          )
      }],
  }]
  for msg in history:
    role = "user" if msg["role"] == "user" else "model"
    contents.append({"role": role, "parts": [{"text": msg["content"]}]})
  payload = {"contents": contents}
  try:
    response = requests.post(url, json=payload, timeout=10)
    if response.status_code == 200:
      model_label = "Google Gemini (Backup)"
      last_used_model[chat_id] = model_label
      res_data = response.json()
      return (
          res_data["candidates"][0]["content"]["parts"][0]["text"],
          model_label,
      )
  except Exception as e:
    print(f"Gemini Fehler: {e}")
  return None, None


def call_grok(history, chat_id):
  if not XAI_GROK_API_KEY:
    return None, None
  url = "https://api.x.ai/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {XAI_GROK_API_KEY}",
      "Content-Type": "application/json",
  }
  # Hier nutzen wir das offizielle Grok 4 Modell
  payload = {"model": "grok-4", "messages": get_full_history_with_system(history)}
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    if response.status_code == 200:
      model_label = "xAI Grok 4 (Kreativ & Multilingual)"
      last_used_model[chat_id] = model_label
      return (
          response.json()["choices"][0]["message"]["content"],
          model_label,
      )
  except Exception as e:
    print(f"Grok Fehler: {e}")
  return None, None


def call_openai_gpt(history, chat_id):
  if not OPENAI_API_KEY:
    return None, None
  url = "https://api.openai.com/v1/chat/completions"
  headers = {
      "Authorization": f"Bearer {OPENAI_API_KEY}",
      "Content-Type": "application/json",
  }
  payload = {
      "model": "gpt-4o-mini",
      "messages": get_full_history_with_system(history),
  }
  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code == 200:
      model_label = "OpenAI GPT-4o-mini (Code/Logik)"
      last_used_model[chat_id] = model_label
      return (
          response.json()["choices"][0]["message"]["content"],
          model_label,
      )
  except Exception as e:
    print(f"OpenAI Fehler: {e}")
  return None, None


def run_apify_scraper(prompt):
  if not APIFY_API_KEY:
    return "Apify API Key fehlt im System."
  url = f"https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items?token={APIFY_API_KEY}"
  payload = {"queries": prompt, "maxPagesPerQuery": 1, "resultsPerPage": 3}
  try:
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code in [200, 201]:
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


def smart_route_message(history, user_text, chat_id):
  text_lower = user_text.lower()
  if any(
      kw in text_lower
      for kw in [
          "code",
          "programm",
          "python",
          "script",
          "fehler",
          "bug",
          "funktion",
      ]
  ):
    resp, m_name = call_openai_gpt(history, chat_id)
    if resp:
      return resp, m_name

  if any(kw in text_lower for kw in ["kreativ", "story", "gedicht", "grok"]):
    resp, m_name = call_grok(history, chat_id)
    if resp:
      return resp, m_name

  # Standard-Router: Nutzt Groq (Llama) kostenlos und sprachübergreifend
  resp, m_name = call_groq_llama(history, chat_id)
  if not resp:
    resp, m_name = call_gemini(history, chat_id)
  if not resp:
    resp, m_name = call_grok(history, chat_id)
  return resp, m_name


@app.route("/", methods=["POST"])
def webhook():
  data = request.get_json()
  if not data or "message" not in data:
    return "OK", 200

  message = data["message"]
  chat_id = str(message["chat"]["id"])
  user_text = message.get("text", "")

  if not user_text:
    return "OK", 200

  if chat_id not in user_balances:
    user_balances[chat_id] = INITIAL_BALANCE

  if user_text.lower() == "/id":
    send_telegram_message(chat_id, f"🔑 **Deine Telegram Chat-ID:** `{chat_id}`")
    return "OK", 200

  if user_text.lower() in ["/status", "/admin"]:
    if chat_id == ADMIN_CHAT_ID:
      current_model = last_used_model.get(chat_id, "Noch kein Modell genutzt")
      balance = user_balances[chat_id]
      send_telegram_message(
          chat_id,
          f"🛠️ **ADMIN STATUS**\n- Letzte KI: `{current_model}`\n- Kontostand:"
          f" `{balance:.2f} €`",
      )
    else:
      send_telegram_message(
          chat_id, "Entschuldigung, diesen Befehl kenne ich nicht."
      )
    return "OK", 200

  if user_text.lower() in ["/guthaben", "guthaben", "balance"]:
    send_telegram_message(
        chat_id,
        f"💳 **Dein Kontostand:** {user_balances[chat_id]:.2f} €\n(Grundversion:"
        f" **Kostenlos** | Premium-Aufgabe: {COST_PER_PREMIUM_TASK:.2f} €)",
    )
    return "OK", 200

  if chat_id not in chat_histories:
    chat_histories[chat_id] = []

  chat_histories[chat_id].append({"role": "user", "content": user_text})

  if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
    chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]

  current_history = chat_histories[chat_id]
  bot_reply = None
  used_model_name = "Unbekannt"
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
          f"💡 **Premium-Service (0,50 €):** Starte Web-Abfrage...",
      )
      bot_reply = run_apify_scraper(user_text)
      used_model_name = "Apify Scraper"
    else:
      send_telegram_message(
          chat_id,
          f"⚙️ **Automatisierung (0,50 €):** Starte Workflow...",
      )
      bot_reply, used_model_name = call_openai_gpt(current_history, chat_id)
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
    bot_reply, used_model_name = smart_route_message(
        current_history, user_text, chat_id
    )

  if not bot_reply:
    bot_reply = (
        "Entschuldigung, im Moment sind alle Leitungen belegt. Bitte versuche"
        " es gleich noch einmal."
    )

  chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
  send_telegram_message(chat_id, bot_reply, model_name=used_model_name)

  return "OK", 200


@app.route("/", methods=["GET"])
def index():
  return "Ki Sekretär Bot is running live!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)












