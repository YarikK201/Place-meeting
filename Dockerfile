FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates openssl && \
    rm -rf /var/lib/apt/lists/*

COPY root.cer /tmp/root.cer
COPY sub.cer /tmp/sub.cer

RUN openssl x509 -inform DER -in /tmp/root.cer \
    -out /usr/local/share/ca-certificates/russian-root.crt 2>/dev/null || \
    openssl x509 -in /tmp/root.cer \
    -out /usr/local/share/ca-certificates/russian-root.crt

RUN openssl x509 -inform DER -in /tmp/sub.cer \
    -out /usr/local/share/ca-certificates/russian-sub.crt 2>/dev/null || \
    openssl x509 -in /tmp/sub.cer \
    -out /usr/local/share/ca-certificates/russian-sub.crt

RUN update-ca-certificates

COPY bot.py catalog.py import_events.py events.json /app/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY bot.py /app/bot.py

USER 65534:65534


CMD ["sh", "-c", "python import_events.py && exec python bot.py"]
