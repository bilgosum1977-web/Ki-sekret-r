import os
import json
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TOKEN}"

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ki-Sekretaer Bot is active and running!")

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            # Telegram-Daten als JSON einlesen
            data = json.loads(post_data.decode('utf-8'))
            
            # Prüfen, ob eine Nachricht im Update enthalten ist
            if "message" in data:
                chat_id = data["message"]["chat"]["id"]
                user_text = data["message"].get("text", "")
                
                # Antwort vorbereiten
                reply_text = f"Hallo! Ich habe deine Nachricht erhalten: '{user_text}'"
                
                # An Telegram API senden (sendMessage)
                url = f"{TELEGRAM_API_URL}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": reply_text
                }
                requests.post(url, json=payload)
                
        except Exception as e:
            print(f"Fehler beim Verarbeiten: {e}")
            
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('', port)
    httpd = HTTPServer(server_address, SimpleHandler)
    print(f"Server laeuft auf Port {port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()


