FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
# Необязательный CA прокси сборки; сертификат не сохраняется в образе.
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then \
      PIP_CERT=/run/secrets/build_ca pip install --no-cache-dir -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi
COPY . /app/
# Если файлы root.cer и sub.cer лежат рядом с Dockerfile, установим их.
RUN set -eu; chmod -R a+rX /app; \
    for f in /app/root.cer /app/sub.cer; do \
      if [ -f "$f" ]; then \
        name="$(basename "$f" .cer)"; \
        openssl x509 -inform DER -in "$f" -out "/usr/local/share/ca-certificates/$name.crt" 2>/dev/null || \
        openssl x509 -in "$f" -out "/usr/local/share/ca-certificates/$name.crt"; \
      fi; \
    done; \
    update-ca-certificates; \
    mkdir -p /data; chown -R 65534:65534 /data

USER 65534:65534
ENV EVENTS_DB=/data/events_v2.db
CMD ["sh", "-c", "python import_events.py && exec python bot.py"]
