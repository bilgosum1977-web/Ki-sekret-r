FROM searxng/searxng:latest

# Erstelle alle potenziellen Ordnerstrukturen im Server
RUN mkdir -p /etc/searxng /searxng /usr/local/searxng

# Kopiere die Einstellungen in absolut jeden möglichen Pfad
COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/settings.yml /searxng/settings.yml
COPY searxng/settings.yml /usr/local/searxng/settings.yml

# Kopiere die Limiter-Datei
COPY searxng/limiter.toml /etc/searxng/limiter.toml

EXPOSE 8888
