import os
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ki-Sekretaer Bot is running!")

    def do_POST(self):
        # Hier kommen später die Telegram-Nachrichten an
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('', port)
    httpd = HTTPServer(server_address, SimpleHandler)
    print(f"Server läuft auf Port {port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
