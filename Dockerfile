FROM searxng/searxng:latest

ENV SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml

COPY searxng/settings.yml /etc/searxng/settings.yml
COPY searxng/limiter.toml /etc/searxng/limiter.toml

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "searx.webapp:create_app()"]

