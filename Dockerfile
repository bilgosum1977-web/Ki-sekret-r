FROM searxng/searxng:latest

# Setze den Pfad für die Einstellungen
ENV SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml

# Kopiere deine Dateien an die exakt richtigen Stellen im System
COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/limiter.toml /etc/searxng/limiter.toml

# SearXNG nutzt standardmäßig Port 8888
EXPOSE 8888
