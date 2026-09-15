FROM searxng/searxng:latest

# Kopiert die Datei direkt in den inneren Programmordner von SearXNG
COPY searxng/settings.yml /usr/local/searxng/searx/settings.yml

# Sicherheitskopie am Standardort
COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/limiter.toml /etc/searxng/limiter.toml

EXPOSE 8888
