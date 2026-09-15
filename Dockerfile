FROM searxng/searxng:latest

# Setze Umgebungsvariablen für die Pfade fest
ENV SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
ENV SEARX_SETTINGS_PATH=/etc/searxng/settings.yml

# Erstelle die Ordner im Container vorsorglich
RUN mkdir -p /etc/searxng /usr/local/searxng

# Kopiere die Dateien an alle potenziellen Zielorte
COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/settings.yml /usr/local/searxng/settings.yml
COPY searxng/limiter.toml /etc/searxng/limiter.toml

EXPOSE 8888
