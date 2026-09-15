FROM searxng/searxng:latest

COPY ./searxng/limiter.toml /etc/searxng/limiter.toml
COPY ./searxng/settings.yml /etc/searxng/settings.yml

