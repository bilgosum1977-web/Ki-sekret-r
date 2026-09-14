FROM searxng/searxng:latest

ENV SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml

COPY settings.yml /etc/searxng/settings.yml

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "searx.webapp:create_app()"]
