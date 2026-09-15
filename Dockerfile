FROM searxng/searxng:latest

# Wir nutzen nur noch den absoluten Standard-Ordner
COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/limiter.toml /etc/searxng/limiter.toml

EXPOSE 8888
