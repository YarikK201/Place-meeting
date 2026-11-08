FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
LABEL org.opencontainers.image.source="https://github.com/YarikK201/Place-meeting"

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
# Необязательный CA прокси сборки; сертификат не сохраняется в образе.
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then \
      PIP_CERT=/run/secrets/build_ca pip install --no-cache-dir -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi
COPY *.py events.json catalog_legacy_urls.json /app/
COPY tests/ /app/tests/
RUN chmod -R a+rX /app && mkdir -p /data && chown 65534:65534 /data

USER 65534:65534
ENV EVENTS_DB=/data/events_v2.db
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["sh", "-c", "python import_events.py && exec python bot.py"]
